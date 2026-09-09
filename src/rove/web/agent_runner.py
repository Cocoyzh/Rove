import json
import time
from typing import Callable, Optional, Dict, Any, List
from rich.console import Console

from rove.lead_agent import LeadAgent
from rove.prompt.system_prompt import SYSTEM_PROMPT
from rove.compaction.compaction_layers import run_pipeline, reactive_compact, MAX_REACTIVE_RETRIES
from rove.tools.message_bus import BUS
from rove.tools.background import bg_manager
from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import Message
from rove.tool_registry import ToolRegistry

console = Console()


class WebLeadAgent(LeadAgent):
    """支持 Web 事件流钩子的 LeadAgent 扩展类。

    不改动原 LeadAgent 的任何逻辑，增加实时回调通知：
    - on_text: token 流式输出
    - on_tool_start: 工具调用开始
    - on_tool_end: 工具调用结束
    """

    def __init__(
        self,
        llm: BaseLLMAdapter,
        registry: ToolRegistry,
        max_steps: int = 50,
        on_text: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        on_tool_end: Optional[Callable[[str, str, str, bool, int], None]] = None,
    ) -> None:
        super().__init__(llm, registry, max_steps)
        self.on_text_hook = on_text
        self.on_tool_start_hook = on_tool_start
        self.on_tool_end_hook = on_tool_end
        self.session_input_tokens: int = 0
        self.session_output_tokens: int = 0
        self.last_context_tokens: int = 0

    def _stream_text(self, text: str) -> None:
        super()._stream_text(text)
        if self.on_text_hook:
            self.on_text_hook(text)

    def run(self, query: str) -> str:
        self.messages.append(Message(role="user", content=query))

        rounds_since_todo = 0
        reminder_cooldown = 0
        reactive_retries = 0
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            for step in range(self.max_steps):
                inbox = BUS.read_inbox('lead')
                if inbox:
                    self.messages.append(
                        Message(role="user", content=f"<inbox>{json.dumps(inbox, indent=2)}</inbox>")
                    )
                notifs = bg_manager.drain_notifications()
                if notifs:
                    notif_text = "\n".join(
                        f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
                    )
                    self.messages.append(
                        Message(role="user", content=f"<background-results>\n{notif_text}\n</background-results>")
                    )

                run_pipeline(self.llm, self.messages)

                try:
                    request = LLMRequest(
                        messages=self.messages,
                        tools=self.tools,
                        max_tokens=8000,
                        system_prompt=SYSTEM_PROMPT,
                    )
                    response: LLMResponse = self.llm.stream(request, on_text=self._stream_text)
                    if response.content:
                        console.print()
                    if response.usage:
                        total_input_tokens += response.usage.input_tokens
                        total_output_tokens += response.usage.output_tokens
                        self.session_input_tokens += response.usage.input_tokens
                        self.session_output_tokens += response.usage.output_tokens
                        self.last_context_tokens = response.usage.input_tokens
                    reactive_retries = 0
                except Exception as e:
                    if (
                        ("prompt_too_long" in str(e).lower() or "too many tokens" in str(e).lower())
                        and reactive_retries < MAX_REACTIVE_RETRIES
                    ):
                        console.print("[bold yellow]⚠ 上下文溢出，触发应急压缩[/bold yellow]")
                        self.messages[:] = reactive_compact(self.llm, self.messages)
                        reactive_retries += 1
                        continue
                    raise

                self.messages.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )

                if response.stop_reason != "tool_use":
                    return response.content

                used_todo = False

                for tool in response.tool_calls:
                    console.print(
                        f"[bold yellow]▸ {tool.tool_name}[/bold yellow] [dim cyan]{tool.tool_args}[/dim cyan]"
                    )
                    if self.on_tool_start_hook:
                        self.on_tool_start_hook(tool.tool_id, tool.tool_name, tool.tool_args)

                    start_t = time.time()
                    output = self.registry.execute(tool.tool_name, tool.tool_args)
                    cost_ms = int((time.time() - start_t) * 1000)

                    output_str = str(output)
                    is_error = output_str.strip().lower().startswith(("error", "exception", "traceback"))

                    label = "error" if is_error else "output"
                    color = "red" if is_error else "green"
                    preview = output_str[:500]
                    if len(output_str) > 500:
                        preview += " ..."
                    console.print(f"[{color}]  {label}:[/{color}] {preview}")

                    if self.on_tool_end_hook:
                        self.on_tool_end_hook(tool.tool_id, tool.tool_name, output_str, is_error, cost_ms)

                    self.messages.append(
                        Message(role="tool", tool_call_id=tool.tool_id, content=output_str)
                    )

                    if tool.tool_name == "todo":
                        used_todo = True

                if used_todo:
                    rounds_since_todo = 0
                    reminder_cooldown = 0
                else:
                    rounds_since_todo += 1
                    if reminder_cooldown > 0:
                        reminder_cooldown -= 1
                    elif rounds_since_todo >= 4:
                        self.messages.append(
                            Message(
                                role="user",
                                content="<reminder>If your plan has evolved or the current subtask is completed, please update your todos. Otherwise continue your work.</reminder>",
                            )
                        )
                        reminder_cooldown = 6

            return "Error: max_steps exceeded before the agent finished."
        finally:
            console.print(f"[dim]⚡ In: {total_input_tokens} · Out: {total_output_tokens}[/dim]")
