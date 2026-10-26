"""dispatch_subagent 单测：受限注册表、轮数/token 上限、报告回传与落盘、权限与并发上限。"""

import threading
from pathlib import Path

from rove.llm import LLMResponse, Usage
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import ToolCall
from rove.permissions import PermissionDecision, PermissionPolicy
from rove.skill_loader import SkillLoader
from rove.task_manager import TaskManager
from rove.tools.agent_teams import TeammateManager
from rove.tools.subagent import (
    MAX_CONCURRENT_SUBAGENTS,
    MAX_INLINE_CHARS,
    SubagentManager,
    build_restricted_registry,
    build_subagent_tool,
)

WORKSPACE = Path(__file__).parent.parent.resolve()


def deny_all(tool_name, arguments, reason) -> bool:
    """测试用审批兜底：意外触发 ASK 时直接拒绝，避免单测阻塞在交互输入。"""
    return False


class FakeLLM(BaseLLMAdapter):
    """脚本化适配器：按序弹出 responses，或用 behavior(i, request) 动态生成。"""

    def __init__(self, responses=None, behavior=None):
        super().__init__(model="fake-main", api_key="test-key", base_url=None, timeout=5)
        self._responses = list(responses or [])
        self._behavior = behavior
        self.calls: list = []

    def _create_client(self):
        return None

    def complete(self, request):
        self.calls.append(request)
        if self._behavior is not None:
            return self._behavior(len(self.calls), request)
        # 与真实适配器一致：累计 usage 计数器
        return self._record_usage(self._responses.pop(0))

    def stream(self, request, on_text=None):
        return self.complete(request)


def tool_use_response(tool_id="t1", name="read_file", args=None, usage=(100, 50)):
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(tool_id=tool_id, tool_name=name,
                             tool_args=args or {"path": "pyproject.toml"})],
        model="fake-main",
        stop_reason="tool_use",
        usage=Usage(*usage),
    )


def final_response(text, usage=(100, 50)):
    return LLMResponse(
        content=text,
        tool_calls=[],
        model="fake-main",
        stop_reason="end_turn",
        usage=Usage(*usage),
    )


def make_manager(llm, results_dir=None) -> SubagentManager:
    return SubagentManager(
        llm,
        results_dir=results_dir,
        approval_handler=deny_all,
    )


# ---------- 注册表快照与权限 ----------

def test_restricted_registry_snapshot():
    names = {t["name"] for t in build_restricted_registry().get_schemas()}
    assert names == {"bash", "read_file", "read_notebook", "write_file", "edit_file", "execute_python"}

    forbidden = {
        # team 协作（防递归 spawn / 越权任务板）
        "send_message", "read_inbox", "scan_tasks", "claim_task", "spawn_teammate",
        "list_teammates", "protocol_request", "protocol_response", "idle",
        # memory 写工具（防越权）与检索
        "memory_store", "memory_update", "memory_recall",
        # 外呼与后台
        "web_search", "run_background", "check_background",
        # 自递归与其余 lead 工具
        "dispatch_subagent", "todo", "load_skill",
    }
    assert names.isdisjoint(forbidden)


def test_dispatch_subagent_defaults_to_ask():
    policy = PermissionPolicy(WORKSPACE)
    decision, _ = policy.decide("dispatch_subagent", {"objective": "x", "context": "y"})
    assert decision == PermissionDecision.ASK


def test_default_registry_contains_subagent_tool(tmp_path):
    from rove.tools.tools_setup import build_default_registry

    registry = build_default_registry(
        skill_loader=SkillLoader(tmp_path / "skills"),
        task_manager=TaskManager(tmp_path / "tasks"),
        team_manager=TeammateManager(tmp_path / "team", None, FakeLLM()),
    )
    names = {t["name"] for t in registry.get_schemas()}
    assert "dispatch_subagent" in names


# ---------- 同步派发与上下文隔离 ----------

def test_sync_dispatch_returns_condensed_report_only(tmp_path):
    llm = FakeLLM([
        tool_use_response("t1", "read_file", {"path": "pyproject.toml"}, usage=(300, 80)),
        final_response(
            "## 结论摘要\n项目名为 rove。\n\n## 关键文件与证据\n- pyproject.toml\n\n## 遇到的障碍\n无",
            usage=(100, 50),
        ),
    ])
    mgr = make_manager(llm, results_dir=tmp_path)

    report = mgr.dispatch(objective="确认项目名", context="pyproject.toml 位于工作区根目录")

    # 回传 lead 的只有结构化报告（tool_result 即本返回值）
    assert report.startswith("## 结论摘要")
    assert "项目名为 rove" in report
    assert "usage: turns=2, input_tokens=400, output_tokens=130" in report
    # 中间轮次隔离在子上下文：briefing 单独起步，工具轮次只进子消息历史
    assert len(llm.calls) == 2
    assert len(llm.calls[0].messages) == 1
    assert len(llm.calls[1].messages) == 3  # briefing + assistant(tool_use) + tool result


def test_build_subagent_tool_handler_passes_through(tmp_path):
    mgr = make_manager(FakeLLM([final_response("ok 报告", usage=(10, 10))]), results_dir=tmp_path)
    tool = build_subagent_tool(mgr)

    assert tool.name == "dispatch_subagent"
    out = tool.handler(objective="o", context="c")
    assert "ok 报告" in out
    assert "usage: turns=1" in out


# ---------- 预算兜底 ----------

def test_token_budget_terminates_and_returns_partial(tmp_path):
    llm = FakeLLM(behavior=lambda i, req: tool_use_response(
        f"t{i}", "read_file", {"path": "pyproject.toml"}, usage=(30_000, 30_000)))
    mgr = make_manager(llm, results_dir=tmp_path)

    report = mgr.dispatch(objective="o", context="c", token_budget=50_000, max_turns=10)

    assert len(llm.calls) == 1, "超预算后不得再发起 LLM 调用"
    assert "token_budget exhausted (60000/50000" in report
    assert "## 遇到的障碍" in report
    assert "usage: turns=1" in report


def test_max_turns_terminates_and_returns_partial(tmp_path):
    llm = FakeLLM(behavior=lambda i, req: tool_use_response(
        f"t{i}", "read_file", {"path": "pyproject.toml"}, usage=(10, 10)))
    mgr = make_manager(llm, results_dir=tmp_path)

    report = mgr.dispatch(objective="o", context="c", max_turns=3)

    assert len(llm.calls) == 3
    assert "max_turns reached (3)" in report
    assert "usage: turns=3" in report


def test_llm_failure_becomes_report_instead_of_silent_death(tmp_path):
    def boom(i, req):
        raise RuntimeError("api down")

    mgr = make_manager(FakeLLM(behavior=boom), results_dir=tmp_path)

    report = mgr.dispatch(objective="o", context="c")
    assert "LLM call failed at turn 1" in report
    assert "api down" in report


# ---------- 报告落盘 ----------

def test_oversized_report_persisted_and_inlined_with_pointer(tmp_path):
    big = "## 结论摘要\n" + "证据" * 9000 + "\n\n## 关键文件与证据\n- x\n\n## 遇到的障碍\n无"
    mgr = make_manager(FakeLLM([final_response(big)]), results_dir=tmp_path)

    report = mgr.dispatch(objective="o", context="c")

    assert "persisted at" in report
    files = list(tmp_path.glob("sub-*.md"))
    assert len(files) == 1
    assert big in files[0].read_text(), "全量报告必须落盘"
    assert len(report) < MAX_INLINE_CHARS, "内联部分必须截断"


# ---------- 入参校验与并发上限 ----------

def test_dispatch_validates_required_inputs(tmp_path):
    mgr = make_manager(FakeLLM(), results_dir=tmp_path)
    assert mgr.dispatch("", "c").startswith("Error: objective")
    assert mgr.dispatch("o", "  ").startswith("Error: context")


def test_dispatch_without_llm_adapter(tmp_path):
    mgr = make_manager(None, results_dir=tmp_path)
    assert mgr.dispatch("o", "c").startswith("Error: no LLM")


def test_concurrency_limit_blocks_second_dispatch(tmp_path):
    gate, release = threading.Event(), threading.Event()

    def behavior(i, req):
        gate.set()
        release.wait(timeout=5)
        return final_response("done")

    mgr = make_manager(FakeLLM(behavior=behavior), results_dir=tmp_path)

    results = {}
    t = threading.Thread(
        target=lambda: results.update(report=mgr.dispatch(objective="o", context="c")))
    t.start()
    assert gate.wait(timeout=5)

    second = mgr.dispatch(objective="o2", context="c2")
    assert second.startswith("Error: concurrent subagent limit reached")

    release.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert "usage: turns=1" in results["report"]


# ---------- 后台化与进度心跳 ----------

def make_p1_manager(llm, tmp_path):
    """bg/记录均指向测试实例，不污染全局 bg_manager 与真实收件箱。"""
    from rove.tools.background import BackGroundManager
    from rove.tools.message_bus import MessageBus

    return SubagentManager(
        llm,
        results_dir=tmp_path / "results",
        bus=MessageBus(tmp_path / "inbox"),
        bg=BackGroundManager(),
        approval_handler=deny_all,
    ), tmp_path


def wait_until(predicate, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_background_dispatch_reports_via_bg_manager(tmp_path):
    mgr, base = make_p1_manager(FakeLLM([final_response("后台结论", usage=(10, 10))]), tmp_path)

    reply = mgr.dispatch(objective="后台任务", context="c", run_in_background=True)
    assert reply.startswith("Background subagent sub-")
    task_id = reply.split()[2]

    assert wait_until(lambda: mgr._bg.tasks.get(task_id, {}).get("status") == "completed")

    # 完成通知进入既有通知管道，lead 下一步 drain_notifications 即可读到全量报告
    notifs = mgr._bg.drain_notifications()
    assert len(notifs) == 1
    assert notifs[0]["task_id"] == task_id
    assert notifs[0]["status"] == "completed"
    assert "后台结论" in notifs[0]["result"]
    assert "usage: turns=1" in notifs[0]["result"]
    assert "后台结论" in mgr._bg.check(task_id)


def test_background_progress_visible_in_check_background(tmp_path):
    release = threading.Event()

    def behavior(i, req):
        # 第 1 轮正常执行（产生进度心跳），第 2 轮挂起等待测试放行
        if i == 1:
            return tool_use_response("t1", "read_file", {"path": "pyproject.toml"})
        release.wait(timeout=5)
        return final_response("done")

    mgr, base = make_p1_manager(FakeLLM(behavior=behavior), tmp_path)

    reply = mgr.dispatch(objective="慢任务", context="c", run_in_background=True)
    task_id = reply.split()[2]
    assert wait_until(lambda: "progress:" in mgr._bg.check(task_id))
    assert "turn 1/" in mgr._bg.check(task_id)

    release.set()
    assert wait_until(lambda: mgr._bg.tasks[task_id]["status"] == "completed")


def test_progress_heartbeat_written_to_lead_inbox(tmp_path):
    llm = FakeLLM([
        tool_use_response("t1", "read_file", {"path": "pyproject.toml"}, usage=(10, 10)),
        final_response("done", usage=(10, 10)),
    ])
    mgr, base = make_p1_manager(llm, tmp_path)

    mgr.dispatch(objective="o", context="c")

    inbox = mgr._bus.read_inbox("lead")
    progress = [m for m in inbox if m["type"] == "progress"]
    assert len(progress) == 1, "只有执行了工具的轮次写心跳"
    assert progress[0]["from"].startswith("sub-")
    assert "turn 1/12" in progress[0]["content"]
    assert "read_file" in progress[0]["content"]


def test_subagent_events_emitted(tmp_path):
    llm = FakeLLM([
        tool_use_response("t1", "read_file", {"path": "pyproject.toml"}, usage=(10, 10)),
        final_response("done", usage=(30, 20)),
    ])
    mgr, base = make_p1_manager(llm, tmp_path)
    events = []
    mgr._on_event = events.append

    mgr.dispatch(objective="探索任务", context="c")

    kinds = [(e["type"], e["status"]) for e in events]
    assert ("subagent_status", "running") in kinds
    assert ("subagent_status", "completed") in kinds
    start = events[0]
    assert start["objective"] == "探索任务" and start["max_turns"] == 12
    progress_events = [e for e in events if e.get("turn")]
    assert progress_events and progress_events[0]["turn"] == 1
    end = events[-1]
    assert end["total_tokens"] == 70


def test_message_bus_accepts_progress_type(tmp_path):
    from rove.tools.message_bus import MessageBus

    bus = MessageBus(tmp_path / "inbox")
    bus.send("sub-abc", "lead", "turn 1/12: read_file(a.txt)", msg_type="progress")

    inbox = bus.read_inbox("lead")
    assert len(inbox) == 1
    assert inbox[0]["type"] == "progress"
    assert inbox[0]["from"] == "sub-abc"

    import pytest
    with pytest.raises(ValueError):
        bus.send("a", "b", "c", msg_type="bogus")
