"""运行观察者：LeadAgent 只报告"发生了什么"，由观察者决定怎么呈现。

- RichObserver：REPL 用，彩色打印给人看（原 lead_agent 里的 console.print 原样搬过来）
- JsonlObserver：headless 用，每个事件一行 JSON 写到 stdout，给机器读
"""
import json
import sys
import time
from typing import Any, Dict, TextIO

from rich.console import Console

from rove.messages import ToolCall

EVENT_VERSION = 1
PREVIEW_HEAD = 1000   # tool_result 预览保留头部字符数
PREVIEW_TAIL = 1000   # tool_result 预览保留尾部字符数


class RunObserver:
    """观察者基类：所有回调默认什么都不做，子类按需覆盖。

    on_run_started / on_run_finished 由最外层入口（headless main）触发，其余由 LeadAgent 触发。
    """

    def on_run_started(self, **info: Any) -> None:
        """一次 run 开始：模型、权限模式等元信息。"""

    def on_run_finished(self, reason: str, exit_code: int, detail: str = "", **info: Any) -> None:
        """一次 run 结束；reason 是封闭枚举，detail 放原始报错。"""

    def on_text_delta(self, text: str) -> None:
        """模型流式输出的一小段文本。"""

    def on_assistant_message(self, content: str) -> None:
        """一轮 LLM 调用结束，content 是这一轮完整的文本（可能为空）。"""

    def on_compaction(self) -> None:
        """上下文溢出，触发了应急压缩。"""

    def on_tool_call(self, call: ToolCall) -> None:
        """即将执行一个工具。"""

    def on_tool_result(self, call: ToolCall, output: str, is_error: bool,
                       error_kind: str | None = None) -> None:
        """工具执行完毕；error_kind 见 tool_registry.ToolOutcome。"""

    def on_usage(self, input_tokens: int, output_tokens: int) -> None:
        """本次 run 的 token 用量汇总。"""


class RichObserver(RunObserver):
    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def on_text_delta(self, text: str) -> None:
        self.console.print(text, end="", markup=False, highlight=False)

    def on_assistant_message(self, content: str) -> None:
        if content:
            self.console.print()

    def on_compaction(self) -> None:
        self.console.print("[bold yellow]⚠ 上下文溢出，触发应急压缩[/bold yellow]")

    def on_tool_call(self, call: ToolCall) -> None:
        self.console.print(f"[bold yellow]▸ {call.tool_name}[/bold yellow] [dim cyan]{call.tool_args}[/dim cyan]")

    def on_tool_result(self, call: ToolCall, output: str, is_error: bool,
                       error_kind: str | None = None) -> None:
        label = "error" if is_error else "output"
        color = "red" if is_error else "green"
        preview = output[:500]
        if len(output) > 500:
            preview += " ..."
        self.console.print(f"[{color}]  {label}:[/{color}] {preview}")

    def on_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.console.print(f"[dim]⚡ In: {input_tokens} · Out: {output_tokens}[/dim]")


def _preview(output: str) -> Dict[str, Any]:
    """头 + 尾预览。按字符（不是字节）切，避免切断 UTF-8 多字节字符。"""
    original_bytes = len(output.encode("utf-8"))
    if len(output) <= PREVIEW_HEAD + PREVIEW_TAIL:
        return {"preview": output, "truncated": False, "original_bytes": original_bytes}
    omitted = len(output) - PREVIEW_HEAD - PREVIEW_TAIL
    preview = f"{output[:PREVIEW_HEAD]}\n... [{omitted} chars omitted] ...\n{output[-PREVIEW_TAIL:]}"
    return {"preview": preview, "truncated": True, "original_bytes": original_bytes}


class JsonlObserver(RunObserver):
    """每个事件一行 JSON，写完立刻 flush（接管道时 stdout 是块缓冲）。

    流式 delta 不输出：一轮结束后在 on_assistant_message 里整段发一条 assistant_text，
    避免一个字一行把事件表撑爆。
    """

    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stdout

    def emit(self, type_: str, **fields: Any) -> None:
        event = {"v": EVENT_VERSION, "type": type_, "ts": time.time(), **fields}
        self.stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self.stream.flush()

    def on_assistant_message(self, content: str) -> None:
        if content:
            self.emit("assistant_text", text=content)

    def on_compaction(self) -> None:
        self.emit("compaction", trigger="context_overflow")

    def on_tool_call(self, call: ToolCall) -> None:
        self.emit("tool_call", call_id=call.tool_id, name=call.tool_name, args=call.tool_args)

    def on_tool_result(self, call: ToolCall, output: str, is_error: bool,
                       error_kind: str | None = None) -> None:
        self.emit("tool_result", call_id=call.tool_id, name=call.tool_name,
                  is_error=is_error, error_kind=error_kind, **_preview(output))

    def on_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.emit("usage", input_tokens=input_tokens, output_tokens=output_tokens)

    def on_run_started(self, **info: Any) -> None:
        self.emit("run_started", **info)

    def on_run_finished(self, reason: str, exit_code: int, detail: str = "", **info: Any) -> None:
        self.emit("run_finished", reason=reason, exit_code=exit_code, detail=detail, **info)
