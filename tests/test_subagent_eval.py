"""P4 评测 harness（plans/subagent-optimize.md §4-P4）单测。

真实跑批需要 LLM API 与预算，这里验证 harness 自身的契约：
任务加载、三种跑法注册表差异、指标采集、聚合与渲染。
LLM 以 FakeLLM 注入，验收命令用与工作区无关的 true/false。
"""

import json
from pathlib import Path

import pytest

from rove.benchmarks.subagent_eval import (
    EvalTask,
    _PeakTrackingProxy,
    aggregate_results,
    build_registry,
    load_tasks,
    render_markdown,
    run_single,
)
from rove.tools.background import BackGroundManager
from rove.tools.message_bus import MessageBus
from test_subagent import FakeLLM, final_response


@pytest.fixture(autouse=True)
def isolate_lead_globals(tmp_path, monkeypatch):
    """run_single 驱动真实 LeadAgent，会读全局 BUS/bg_manager——替换为隔离实例。"""
    monkeypatch.setattr("rove.lead_agent.BUS", MessageBus(tmp_path / "inbox"))
    monkeypatch.setattr("rove.lead_agent.bg_manager", BackGroundManager())


def final_llm():
    return FakeLLM([final_response("评测回复", usage=(1500, 300))])


# ---------- 任务集 ----------

def test_load_tasks_parses_builtin_task_set_and_skips_bad_lines(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps({"task_id": "t1", "prompt": "p", "verify": "true"}) + "\n"
        + "not-json\n"
        + json.dumps({"prompt": "missing id"}) + "\n"
        + json.dumps({"task_id": "t2", "prompt": "p", "verify": "true", "setup": ""}) + "\n",
        encoding="utf-8",
    )
    tasks = load_tasks(path)
    assert [t.task_id for t in tasks] == ["t1", "t2"]

    # 内置任务集：10 个任务、setup/verify 齐备、id 唯一
    builtin = load_tasks(Path(__file__).parent.parent
                         / "src/rove/benchmarks/tasks_subagent_eval.jsonl")
    assert len(builtin) == 10
    assert len({t.task_id for t in builtin}) == 10
    assert all(t.verify for t in builtin)
    assert all(t.setup for t in builtin)


# ---------- 三种跑法的注册表差异 ----------

def test_registry_tool_sets_differ_by_mode(tmp_path):
    base = {"todo", "bash", "read_file", "write_file", "edit_file", "execute_python"}

    def names(mode):
        registry = build_registry(mode, final_llm(), tmp_path / mode)
        return {t["name"] for t in registry.get_schemas()}

    lead = names("lead")
    teammate = names("teammate")
    subagent = names("subagent")

    assert base <= lead
    assert "spawn_teammate" not in lead and "dispatch_subagent" not in lead
    assert "spawn_teammate" in teammate and "dispatch_subagent" not in teammate
    assert "dispatch_subagent" in subagent and "spawn_teammate" not in subagent
    assert "reclaim_teammate_tasks" in teammate

    with pytest.raises(ValueError):
        build_registry("bogus", final_llm(), tmp_path / "x")


# ---------- run_single 指标采集 ----------

def test_run_single_collects_metrics_and_success(tmp_path):
    task = EvalTask(task_id="t-ok", prompt="做点事", verify="true")
    result = run_single(task, "lead", tmp_path / "ws", llm=final_llm())

    assert result["success"] is True
    assert result["error"] == ""
    assert result["peak_input_tokens"] > 0
    assert result["total_input_tokens"] > 0
    assert result["cost_usd"] > 0
    assert result["wall_seconds"] >= 0
    # peak 记录的是单次调用最大 input
    assert result["peak_input_tokens"] == result["total_input_tokens"]


def test_run_single_reports_failure_on_failing_verify(tmp_path):
    task = EvalTask(task_id="t-bad", prompt="做点事", verify="false")
    result = run_single(task, "lead", tmp_path / "ws", llm=final_llm())
    assert result["success"] is False

    task2 = EvalTask(task_id="t-crash", prompt="做点事", verify="true")
    boom = FakeLLM(behavior=lambda i, req: (_ for _ in ()).throw(RuntimeError("api down")))
    result2 = run_single(task2, "lead", tmp_path / "ws2", llm=boom)
    assert result2["success"] is False
    assert "api down" in result2["error"]


def test_peak_tracking_proxy_delegates_and_tracks():
    inner = FakeLLM([final_response("a", usage=(100, 10)),
                     final_response("b", usage=(9000, 10))])
    proxy = _PeakTrackingProxy(inner)
    proxy.stream(None)
    proxy.complete(None)
    assert proxy.peak_input_tokens == 9000
    assert proxy.total_input_tokens == 9100  # 委托到底层计数器
    assert proxy.model == "fake-main"


# ---------- 聚合与渲染 ----------

def test_aggregate_results_and_markdown_render():
    def row(mode, success, peak, cost, wall):
        return {"task_id": "t", "mode": mode, "success": success, "error": "",
                "peak_input_tokens": peak, "total_input_tokens": peak,
                "total_output_tokens": 0, "cost_usd": cost, "wall_seconds": wall}

    results = [
        row("lead", True, 1000, 0.01, 1.0),
        row("lead", False, 2000, 0.03, 3.0),
        row("subagent", True, 500, 0.02, 2.0),
    ]
    summary = aggregate_results(results)
    by_mode = {s["mode"]: s for s in summary}
    assert by_mode["lead"]["success_rate"] == 0.5
    assert by_mode["lead"]["avg_peak_input_tokens"] == 1500
    assert by_mode["lead"]["total_cost_usd"] == 0.04
    assert by_mode["subagent"]["success_rate"] == 1.0
    assert "teammate" not in by_mode  # 未跑的模式不出现

    md = render_markdown(summary)
    assert "纯 lead" in md and "lead+dispatch_subagent" in md
    assert "50%" in md
