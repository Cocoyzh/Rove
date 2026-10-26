"""P4 效果评测最小闭环（plans/subagent-optimize.md §4-P4）。

固定 10 个探索/修改任务，对比三种跑法：纯 lead / lead+teammate / lead+dispatch_subagent。
指标：成功率、lead 上下文峰值 token、总成本（按模型计价）、墙钟时间。

隔离模型：paths.py 的 WORKSPACE_ROOT 在导入时绑定环境，因此每个
(task × mode) 组合由独立子进程执行（ROVE_WORKSPACE 指向全新工作区），
外层 orchestrator 聚合 JSON 结果并渲染对比表。

用法：
    python -m rove.benchmarks.subagent_eval run \
        --tasks src/rove/benchmarks/tasks_subagent_eval.jsonl \
        --modes lead,teammate,subagent \
        --out .rove/eval/results.json \
        [--append-plan plans/subagent-optimize.md]

单跑（orchestrator 内部调用）：
    python -m rove.benchmarks.subagent_eval single --task <json文件> --mode <m> --workspace <dir>
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import AnthropicLLMAdapter, estimate_cost_usd
from rove.paths import WORKSPACE_ROOT

MODES = ("lead", "teammate", "subagent")
VERIFY_TIMEOUT = 60


@dataclass
class EvalTask:
    task_id: str
    prompt: str
    verify: str                 # 工作区内执行的验收命令，exit 0 即成功
    setup: str = ""             # 工作区初始化命令（orchestrator 在派发前执行）

    @classmethod
    def from_dict(cls, data: dict) -> "EvalTask":
        return cls(
            task_id=str(data["task_id"]),
            prompt=str(data["prompt"]),
            verify=str(data["verify"]),
            setup=str(data.get("setup", "") or ""),
        )


def load_tasks(path: Path) -> list[EvalTask]:
    tasks = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tasks.append(EvalTask.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError):
            continue  # 跳过坏行而非中断整个跑批
    return tasks


class _PeakTrackingProxy:
    """适配器代理：记录 lead 上下文峰值（单次调用最大 input_tokens）并转发其余属性。"""

    def __init__(self, inner):
        self._inner = inner
        self.peak_input_tokens = 0

    def _track(self, response: LLMResponse) -> LLMResponse:
        if response.usage and response.usage.input_tokens > self.peak_input_tokens:
            self.peak_input_tokens = response.usage.input_tokens
        return response

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        return self._track(self._inner.stream(request, on_text))

    def complete(self, request: LLMRequest) -> LLMResponse:
        return self._track(self._inner.complete(request))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def build_registry(mode: str, llm, workspace: Path):
    """按跑法构建注册表：base 为 lead 常规工具，协作工具按模式注入，
    用于隔离「协作通道本身」对 lead 上下文经济性的影响。"""
    from rove.permissions import APPROVAL_MANAGER, ApprovalMode, PermissionPolicy
    from rove.skill_loader import SkillLoader
    from rove.task_manager import TaskManager
    from rove.tool_registry import ToolRegistry
    from rove.tools.execute_python import execute_python_tool
    from rove.tools.file_tools import FILE_TOOLS
    from rove.tools.background import check_bg_tool, run_bg_tool
    from rove.tools.memory_tools import build_memory_tools
    from rove.tools.skill_tool import build_skill_tool
    from rove.tools.todo import build_todo_tool
    from rove.tools.web_search import web_search_tool

    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")

    task_manager = TaskManager(workspace / ".tasks")
    registry = ToolRegistry(PermissionPolicy(workspace), lambda *a, **k: True)
    # 无人值守评测：完全访问模式（硬拒绝与越界底线仍然生效）
    registry.permission_policy.set_approval_mode(ApprovalMode.FULL_ACCESS)

    tools = [
        build_todo_tool(task_manager),
        execute_python_tool,
        *FILE_TOOLS,
        build_skill_tool(SkillLoader(workspace / ".skills")),
        *build_memory_tools(),
        web_search_tool,
        run_bg_tool,
        check_bg_tool,
    ]
    if mode == "teammate":
        from rove.tools.agent_teams import TeammateManager
        from rove.tools.team_tool import build_team_tools
        team = TeammateManager(workspace / ".team", task_manager, llm)
        tools.extend(build_team_tools(team, task_manager))
    elif mode == "subagent":
        from rove.tools.subagent import SubagentManager, build_subagent_tool
        from rove.tools.background import bg_manager
        sub = SubagentManager(llm, bg=bg_manager,
                              fast_model_id=os.getenv("LLM_FAST_MODEL_ID"))
        tools.append(build_subagent_tool(sub))

    registry.register_many(tools)
    return registry


def build_lead_llm() -> AnthropicLLMAdapter:
    return AnthropicLLMAdapter(
        model=os.getenv("LLM_MODEL_ID", "claude-sonnet-5"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL"),
        timeout=int(os.getenv("LLM_TIMEOUT", "120")),
    )


def run_single(task: EvalTask, mode: str, workspace: Path, llm=None, max_steps: int = 30) -> dict:
    """在给定工作区跑一个 (task × mode) 组合，返回结构化指标。"""
    from rove.lead_agent import LeadAgent

    workspace.mkdir(parents=True, exist_ok=True)
    llm = _PeakTrackingProxy(llm if llm is not None else build_lead_llm())
    started = time.perf_counter()
    error = ""
    try:
        registry = build_registry(mode, llm, workspace)
        lead = LeadAgent(llm, registry, max_steps=max_steps)
        lead.run(task.prompt)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    wall = time.perf_counter() - started

    verify = subprocess.run(
        task.verify, shell=True, cwd=str(workspace),
        capture_output=True, text=True, timeout=VERIFY_TIMEOUT,
    )
    success = verify.returncode == 0 and not error

    total_in = llm.total_input_tokens
    total_out = llm.total_output_tokens
    return {
        "task_id": task.task_id,
        "mode": mode,
        "success": success,
        "error": error,
        "peak_input_tokens": llm.peak_input_tokens,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "cost_usd": round(estimate_cost_usd(llm.model, total_in, total_out), 6),
        "wall_seconds": round(wall, 2),
    }


def aggregate_results(results: list[dict]) -> list[dict]:
    """按 mode 聚合：成功率 / lead 上下文峰值均值 / 总成本 / 平均墙钟。"""
    summary = []
    for mode in MODES:
        rows = [r for r in results if r["mode"] == mode]
        if not rows:
            continue
        n = len(rows)
        summary.append({
            "mode": mode,
            "runs": n,
            "success_rate": round(sum(r["success"] for r in rows) / n, 3),
            "avg_peak_input_tokens": round(sum(r["peak_input_tokens"] for r in rows) / n),
            "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
            "avg_wall_seconds": round(sum(r["wall_seconds"] for r in rows) / n, 2),
        })
    return summary


def render_markdown(summary: list[dict]) -> str:
    lines = [
        "| 跑法 | 任务数 | 成功率 | lead 上下文峰值 (avg) | 总成本 USD | 墙钟 avg (s) |",
        "|---|---|---|---|---|---|",
    ]
    names = {"lead": "纯 lead", "teammate": "lead+teammate", "subagent": "lead+dispatch_subagent"}
    for s in summary:
        lines.append(
            f"| {names.get(s['mode'], s['mode'])} | {s['runs']} | "
            f"{s['success_rate']:.0%} | {s['avg_peak_input_tokens']:,} | "
            f"{s['total_cost_usd']:.4f} | {s['avg_wall_seconds']} |"
        )
    return "\n".join(lines)


# ==================== CLI ====================

def _cmd_single(args) -> None:
    task = EvalTask.from_dict(json.loads(Path(args.task).read_text(encoding="utf-8")))
    result = run_single(task, args.mode, Path(args.workspace))
    print(json.dumps(result, ensure_ascii=False))


def _cmd_run(args) -> None:
    tasks = load_tasks(Path(args.tasks))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    root = Path(args.workdir)
    for task in tasks:
        for mode in [m for m in MODES if m in args.modes.split(",")]:
            workspace = root / f"{mode}-{task.task_id}"
            if task.setup:
                workspace.mkdir(parents=True, exist_ok=True)
                subprocess.run(task.setup, shell=True, cwd=str(workspace), timeout=60)
            # 独立子进程：ROVE_WORKSPACE 隔离 + 指标 JSON 回传
            task_file = root / f"task-{task.task_id}.json"
            root.mkdir(parents=True, exist_ok=True)
            task_file.write_text(json.dumps({
                "task_id": task.task_id, "prompt": task.prompt, "verify": task.verify,
            }, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "rove.benchmarks.subagent_eval", "single",
                 "--task", str(task_file), "--mode", mode, "--workspace", str(workspace)],
                capture_output=True, text=True, timeout=args.timeout,
                env={**os.environ, "ROVE_WORKSPACE": str(workspace)},
            )
            try:
                results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
            except (IndexError, json.JSONDecodeError):
                results.append({
                    "task_id": task.task_id, "mode": mode, "success": False,
                    "error": (proc.stderr or proc.stdout or "no output")[-500:],
                    "peak_input_tokens": 0, "total_input_tokens": 0,
                    "total_output_tokens": 0, "cost_usd": 0.0, "wall_seconds": 0.0,
                })
            print(f"[{mode}/{task.task_id}] success={results[-1]['success']}", file=sys.stderr)

    out_path.write_text(json.dumps({"results": results,
                                    "summary": aggregate_results(results)},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(render_markdown(aggregate_results(results)))

    if args.append_plan:
        appendix = (
            f"\n### P4 评测结果（{time.strftime('%Y-%m-%d %H:%M')}，任务集 {Path(args.tasks).name}）\n\n"
            f"{render_markdown(aggregate_results(results))}\n"
        )
        Path(args.append_plan).open("a", encoding="utf-8").write(appendix)


def main() -> None:
    parser = argparse.ArgumentParser(prog="subagent_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_single = sub.add_parser("single")
    p_single.add_argument("--task", required=True, help="任务 JSON 文件")
    p_single.add_argument("--mode", required=True, choices=MODES)
    p_single.add_argument("--workspace", required=True)
    p_single.set_defaults(func=_cmd_single)

    p_run = sub.add_parser("run")
    p_run.add_argument("--tasks", required=True)
    p_run.add_argument("--modes", default="lead,teammate,subagent")
    p_run.add_argument("--out", default=".rove/eval/results.json")
    p_run.add_argument("--workdir", default=".rove/eval/runs")
    p_run.add_argument("--timeout", type=int, default=1800, help="单组合超时（秒）")
    p_run.add_argument("--append-plan", default="", help="把结果表追加到该 markdown 文件")
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
