"""Rove headless 入口：`rove run --prompt ...`，给平台（worker）以子进程方式调用。

契约：
- stdout 只输出 JSONL 事件（observers.JsonlObserver），第一条 run_started，最后一条 run_finished
- 其余一切输出（rich 打印、警告、工具子进程）都去 stderr
- 绝不读 stdin 做交互：需要审批的操作直接拒绝
- 退出码见 EXIT_CODES；没有 run_finished 的退出（SIGKILL、参数错误）由调用方兜底
"""
import argparse
import os
import signal
import sys
import time
from typing import Any, Dict, TextIO

import anthropic
from rich.console import Console

from rove.lead_agent import LeadAgent, RunResult
from rove.llm_adapters import AnthropicLLMAdapter
from rove.observers import JsonlObserver
from rove.paths import SKILL_DIR, TASK_DIR, TEAM_DIR, WORKSPACE_ROOT
from rove.permissions import VALID_APPROVAL_MODES
from rove.skill_loader import SkillLoader
from rove.task_manager import TaskManager
from rove.tools import background
from rove.tools.agent_teams import TeammateManager
from rove.tools.tools_setup import build_default_registry

err_console = Console(stderr=True)

# reason → 退出码。1、2 沿用 Python / argparse 的默认含义，自定义的从 3 开始
EXIT_CODES: Dict[str, int] = {
    "completed": 0,
    "crashed": 1,            # Rove 自身 bug（未预期的异常）
    "llm_timeout": 3,        # 3 = 外部临时故障，退避后可重试
    "rate_limited": 3,
    "llm_unavailable": 3,
    "auth_failed": 4,        # 4 = 配置错误，重试没用，同类任务也会失败
    "bad_request": 4,
    "config_error": 4,
    "max_steps": 5,          # 5 = Agent 没完成，原样重试大概率还是失败
    "max_tokens": 5,
    "refusal": 5,
    "context_overflow": 5,
    "unexpected_stop": 5,
    "cancelled": 143,        # 128 + SIGTERM(15)
}


class Terminated(BaseException):
    """SIGTERM 转成的异常。继承 BaseException：工具里的 `except Exception` 吞不掉它。"""


def _on_sigterm(signum: int, frame: Any) -> None:
    raise Terminated()


def classify_exception(e: BaseException) -> tuple[str, str]:
    """异常 → (reason, detail)。先判子类再判父类，顺序反了会被父类截走。"""
    detail = f"{type(e).__name__}: {e}"
    if isinstance(e, (Terminated, KeyboardInterrupt)):
        return "cancelled", detail
    if isinstance(e, anthropic.APITimeoutError):         # 是 APIConnectionError 的子类
        return "llm_timeout", detail
    if isinstance(e, anthropic.APIConnectionError):
        return "llm_unavailable", detail
    if isinstance(e, anthropic.APIStatusError):
        status = e.status_code
        if "prompt_too_long" in str(e).lower() or "too many tokens" in str(e).lower():
            return "context_overflow", detail             # 应急压缩重试用尽后抛出的 400
        if status == 429:
            return "rate_limited", detail
        if status in (401, 403):
            return "auth_failed", detail
        if status >= 500:
            return "llm_unavailable", detail
        return "bad_request", detail
    return "crashed", detail


def deny_approval(tool_name: str, arguments: Dict[str, Any], reason: str) -> bool:
    """headless 没有人可问：需要审批的一律拒绝，结果作为工具错误交还给模型。"""
    err_console.print(f"[yellow]⚠ headless 拒绝需要审批的操作 {tool_name}: {reason}[/yellow]")
    return False


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="rove run", description="以 headless 模式运行一次任务，stdout 输出 JSONL 事件")
    parser.add_argument("--prompt", default=None,
                        help="任务描述；省略或传 '-' 时从 stdin 读取（适合长 prompt）")
    parser.add_argument("--approval-mode", default="workspace", choices=sorted(VALID_APPROVAL_MODES),
                        help="权限模式；需要审批（ASK）的操作在 headless 下一律拒绝")
    parser.add_argument("--max-steps", type=int, default=50)
    args = parser.parse_args(argv)

    if args.prompt is None or args.prompt == "-":
        if sys.stdin is None or sys.stdin.isatty():
            parser.error("没有 --prompt，且 stdin 是终端：请用 --prompt 或通过管道传入 prompt")
        args.prompt = sys.stdin.read()
    if not args.prompt.strip():
        parser.error("prompt 为空")
    return args


def _isolate_stdout() -> TextIO:
    """把事件通道和其他输出彻底分开，返回只用于写事件的流。

    在 fd 层面做（而不只是替换 sys.stdout）：先复制一份真正的 stdout 留给事件，
    再把 fd 1 指向 stderr。这样 print、rich、C 扩展、以及继承 fd 1 的工具子进程，
    写到"stdout"的东西实际都去了 stderr，不会混进 JSONL。
    """
    sys.stdout.flush()
    events_fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(events_fd, "w", encoding="utf-8", buffering=1)


class ConfigError(Exception):
    """启动前就能发现的配置问题（缺环境变量等），归类为 config_error。"""


def _build_agent(args: argparse.Namespace, observer: JsonlObserver) -> LeadAgent:
    missing = [k for k in ("LLM_MODEL_ID", "LLM_API_KEY") if not os.getenv(k)]
    if missing:
        raise ConfigError(f"缺少环境变量: {', '.join(missing)}")
    if os.getenv("ROVE_TEAMMATES") == "1":
        # teammate 的注册表写死了交互审批和 workspace 模式（agent_teams._teammate_registry），
        # 既会调 input()，又不继承 --approval-mode。接通之前 fail closed。
        raise ConfigError("headless 暂不支持 ROVE_TEAMMATES=1：teammate 不继承非交互审批与 --approval-mode")

    ctx_override = os.getenv("LLM_CONTEXT_WINDOW")
    llm = AnthropicLLMAdapter(
        model=os.getenv("LLM_MODEL_ID"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        timeout=int(os.getenv("LLM_TIMEOUT", "60")),
        context_window=int(ctx_override) if ctx_override else None,
    )
    task_manager = TaskManager(TASK_DIR)
    registry = build_default_registry(
        skill_loader=SkillLoader(SKILL_DIR),
        task_manager=task_manager,
        team_manager=TeammateManager(TEAM_DIR, task_manager, llm),
        approval_handler=deny_approval,
    )
    registry.permission_policy.set_approval_mode(VALID_APPROVAL_MODES[args.approval_mode])
    return LeadAgent(llm, registry, max_steps=args.max_steps, observer=observer)


def execute(build_agent, prompt: str, observer: JsonlObserver, **started_info: Any) -> int:
    """唯一的收尾点：发 run_started → 跑 → 归类结束原因 → 发 run_finished → 返回退出码。"""
    started = time.monotonic()
    observer.on_run_started(**started_info)
    reason, detail, final_text = "crashed", "", ""
    try:
        agent = build_agent()
        result: RunResult = agent.run(prompt)
        reason, detail, final_text = result.reason, result.detail, result.content
    except ConfigError as e:
        reason, detail = "config_error", str(e)
    except BaseException as e:   # 包括 Terminated / KeyboardInterrupt：取消也要有 run_finished
        reason, detail = classify_exception(e)
        if reason == "crashed":
            err_console.print_exception()
    finally:
        # 先清理后台子进程，再宣布结束：否则平台以为已取消，旧进程却还在改工作区
        background_terminated = background.bg_manager.shutdown()
        exit_code = EXIT_CODES[reason]
        observer.on_run_finished(reason, exit_code, detail,
                                 final_text=final_text,
                                 background_terminated=background_terminated,
                                 duration_ms=int((time.monotonic() - started) * 1000))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)                       # 参数错误：argparse 直接 exit(2)，没有任何事件
    signal.signal(signal.SIGTERM, _on_sigterm)
    observer = JsonlObserver(_isolate_stdout())
    return execute(
        lambda: _build_agent(args, observer),
        args.prompt,
        observer,
        model=os.getenv("LLM_MODEL_ID"),
        approval_mode=args.approval_mode,
        max_steps=args.max_steps,
        workspace=str(WORKSPACE_ROOT),
        prompt_chars=len(args.prompt),
    )


if __name__ == "__main__":
    sys.exit(main())
