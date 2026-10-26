"""Teammate 健壮性修补（plans/subagent-optimize.md P2）单测。

覆盖验收标准：
- 心跳每轮刷新；线程死亡 + 心跳过期 → list_teammates 标记 crashed，任务可 reclaim；
- cancel 取消协议：teammate 应答后收尾退出并先发工作总结；
- 空闲自弃 / 轮数超限均走优雅结束路径（先总结，再 shutdown）；
- 线程异常统一捕获：标记 crashed + 写 lead 收件箱，不允许静默死亡。

节奏常量（poll_interval/idle_timeout/max_total_turns）均可在构造时缩小，
测试用 0.05s 轮询 + 0.2s 空闲超时保证快速收敛。
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from rove.task_manager import TaskManager
from rove.tools.agent_teams import TeammateManager
from rove.tools.message_bus import MessageBus
from rove.tools.team_tool import build_team_tools
from test_subagent import FakeLLM, final_response, tool_use_response


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def make_team(tmp_path, responses=None, behavior=None, **kw) -> tuple[TeammateManager, FakeLLM]:
    llm = FakeLLM(responses=responses, behavior=behavior)
    team = TeammateManager(
        tmp_path / "team", None, llm,
        poll_interval=0.05, idle_timeout=0.2,
        bus=MessageBus(tmp_path / "inbox"),
        **kw,
    )
    return team, llm


# ---------- 心跳与优雅结束 ----------

def test_heartbeat_refreshed_and_idle_suicide_sends_summary(tmp_path):
    # 第 1 轮：调用 idle 工具 → 进入空闲轮询；0.2s 无消息 → 空闲自弃；
    # 第 2 次调用是总结请求（tools=[]）
    team, llm = make_team(tmp_path, responses=[
        tool_use_response("t1", "idle", {}),
        final_response("总结：完成了看板清理，无遗留，建议直接回收。"),
    ])

    team.spawn("alice", "explorer", "开始工作")
    assert wait_until(lambda: team._find_member("alice")["status"] == "shutdown")

    # 心跳文件存在且新鲜（mtime 在 10s 内）
    hb = team._heartbeat_path("alice")
    assert hb.exists()
    assert time.time() - hb.stat().st_mtime < 10

    # 空闲自弃前先向 lead 发工作总结
    inbox = team._bus.read_inbox("lead")
    summaries = [m for m in inbox if "总结" in m.get("content", "")]
    assert summaries and summaries[0]["from"] == "alice"


def test_graceful_exit_marks_shutdown_not_crashed(tmp_path):
    team, _ = make_team(tmp_path, responses=[
        tool_use_response("t1", "idle", {}),
        final_response("总结。"),
    ])
    team.spawn("bob", "worker", "工作")
    assert wait_until(lambda: team._find_member("bob")["status"] == "shutdown")
    assert "bob, worker, shutdown" in team.list_all()


# ---------- 取消协议 ----------

def test_cancel_protocol_triggers_wrapup_and_summary(tmp_path, monkeypatch):
    tmp_bus = MessageBus(tmp_path / "inbox")
    # cancel 应答经 protocol 模块的全局 BUS 投递，测试中替换为隔离实例
    monkeypatch.setattr("rove.tools.protocol.BUS", tmp_bus)
    from rove.tools.protocol import PROTOCOL, _handle_protocol_request

    team, _ = make_team(tmp_path, responses=[
        tool_use_response("t1", "idle", {}),
        final_response("总结：被取消前的进展如下……"),
    ])
    team.spawn("carol", "worker", "工作")
    assert wait_until(lambda: team._find_member("carol")["status"] == "idle")

    _handle_protocol_request(sender="lead", protocol_type="cancel", receiver="carol",
                             request_payload={"reason": "测试取消"})
    assert wait_until(lambda: team._find_member("carol")["status"] == "shutdown")

    inbox = tmp_bus.read_inbox("lead")
    types = [m["type"] for m in inbox]
    assert "protocol_response" in types, "teammate 必须先应答取消协议"
    response = next(m for m in inbox if m["type"] == "protocol_response")
    assert response["status"] == "approved"
    assert any("总结" in m.get("content", "") for m in inbox), "退出前必须发工作总结"

    # 协议状态机落地为 approved
    assert any(s.status == "approved" for s in PROTOCOL.states.values())


def test_cancel_during_work_loop_takes_effect(tmp_path):
    team, llm = make_team(tmp_path, behavior=lambda i, req: (
        final_response("总结：收到取消，已收尾。") if not req.tools
        else tool_use_response(f"t{i}", "read_file", {"path": "pyproject.toml"})
    ))
    team.spawn("dave", "worker", "工作")
    assert wait_until(lambda: llm.calls, timeout=5), "teammate 应开始工作"

    # 工作循环间隙投递取消（下一轮 inbox 检查时生效）
    from rove.tools.protocol import _handle_protocol_request
    import rove.tools.protocol as protocol_mod
    original_bus = protocol_mod.BUS
    protocol_mod.BUS = team._bus
    try:
        _handle_protocol_request(sender="lead", protocol_type="cancel", receiver="dave",
                                 request_payload={"reason": "中止"})
    finally:
        protocol_mod.BUS = original_bus

    assert wait_until(lambda: team._find_member("dave")["status"] == "shutdown")


# ---------- 崩溃感知与预算 ----------

def test_thread_exception_marks_crashed_and_notifies_lead(tmp_path):
    def boom(i, req):
        raise RuntimeError("boom")

    team, _ = make_team(tmp_path, behavior=boom)
    team.spawn("eve", "worker", "工作")

    assert wait_until(lambda: team._find_member("eve")["status"] == "crashed")
    inbox = team._bus.read_inbox("lead")
    assert any("crashed" in m.get("content", "") and "boom" in m.get("content", "")
               for m in inbox), "崩溃必须写 lead 收件箱，不允许静默死亡"
    assert "eve, worker, crashed" in team.list_all()


def test_max_total_turns_budget_forces_graceful_exit(tmp_path):
    team, llm = make_team(tmp_path, max_total_turns=2, behavior=lambda i, req: (
        final_response("预算耗尽总结。") if not req.tools
        else tool_use_response(f"t{i}", "read_file", {"path": "pyproject.toml"})
    ))
    team.spawn("frank", "worker", "工作")

    assert wait_until(lambda: team._find_member("frank")["status"] == "shutdown")
    # 2 轮工作 + 1 次总结调用，之后不再烧钱
    assert len(llm.calls) == 3
    assert any("总结" in m.get("content", "") for m in team._bus.read_inbox("lead"))


def test_restart_stale_member_marked_crashed_and_reclaimable(tmp_path):
    # 模拟 kill -9 / 进程重启：磁盘配置遗留 working 成员，无线程、心跳过期
    team_dir = tmp_path / "team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        '{"team_name": "default", "members": [{"name": "ghost", "role": "worker", "status": "working"}]}')
    hb = team_dir / "heartbeat" / "ghost"
    hb.parent.mkdir(parents=True)
    hb.write_text("0")
    stale = time.time() - 100
    os.utime(hb, (stale, stale))

    team, _ = make_team(tmp_path)
    listing = team.list_all()
    assert "ghost, worker, crashed" in listing
    assert "reclaim" in listing
    assert team._find_member("ghost")["status"] == "crashed"

    # crashed 成员名下的进行中任务可被回收
    tm = TaskManager(tmp_path / "tasks")
    task_id = json.loads(tm.create(subject="遗留任务"))["id"]
    tm.claim_task(task_id, owner="ghost")
    result = tm.reclaim("ghost")
    assert "Reclaimed 1" in result
    task = tm.get_all_tasks()[0]
    assert task["owner"] == "" and task["status"] == "pending"


def test_alive_thread_with_stale_heartbeat_not_crashed(tmp_path):
    # 长时间 LLM 调用期间心跳可能过期，但线程活着就不算崩溃
    release = threading.Event()

    def slow(i, req):
        release.wait(timeout=5)
        return tool_use_response("t1", "idle", {})

    team, _ = make_team(tmp_path, behavior=slow)
    team.spawn("grace", "worker", "工作")
    time.sleep(0.3)  # 超过 2×poll_interval
    assert "grace, worker, working" in team.list_all()
    release.set()
    assert wait_until(lambda: team._find_member("grace")["status"] == "shutdown")


# ---------- reclaim 工具注册 ----------

def test_reclaim_tool_registered_for_lead(tmp_path):
    tools = build_team_tools(
        TeammateManager(tmp_path / "team", None, None),
        TaskManager(tmp_path / "tasks"),
    )
    names = {t.name for t in tools}
    assert "reclaim_teammate_tasks" in names

    # 未提供 task_manager 时不注册该工具（向后兼容）
    tools_no_task = build_team_tools(TeammateManager(tmp_path / "team2", None, None))
    assert "reclaim_teammate_tasks" not in {t.name for t in tools_no_task}


if __name__ == "__main__":
    pytest.main([__file__])
