import json
import time
from typing import List, Optional
from rich.console import Console
from rove.tool_registry import ToolRegistry
from rove.prompt.system_prompt import SYSTEM_PROMPT
from rove.compaction.compaction_layers import run_pipeline, reactive_compact, MAX_REACTIVE_RETRIES, compact_history
from rove.memory import MEMORY
from rove.tools.message_bus import BUS
from rove.tools.background import bg_manager
from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import Message
from rove.call_trace import AgentTracer, CallTracer, new_session_id

console = Console()


def _store_l4_summary(summary: str) -> None:
    """L4/应急压缩的摘要沉淀进长期记忆（Issue #4：压缩与记忆联动）。"""
    MEMORY.long_term.store("episodic", f"summary_{int(time.time())}", summary[:2000])


def _build_system_prompt(query: str) -> str:
    """System Prompt = 核心指令 + 记忆块（工作记忆 + 长期记忆检索）。

    记忆走 system prompt 注入，天然位于压缩流水线之外，永不失忆。
    """
    memory_block = MEMORY.build_system_context(query)
    if memory_block:
        return f"{SYSTEM_PROMPT}\n\n{memory_block}"
    return SYSTEM_PROMPT


class LeadAgent:
    def __init__(self, llm: BaseLLMAdapter, registry: ToolRegistry, max_steps: int = 50,
                 tracer: Optional[AgentTracer] = None):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.messages: List[Message] = []
        self.tools = registry.get_schemas()
        self._conv_start_input = self.llm.total_input_tokens
        self._conv_start_output = self.llm.total_output_tokens
        # 调用级追踪：Web 运行时注入共享 tracer（与 teammate 同会话落盘）；
        # REPL 自建独立会话，trace 同样落盘（.rove/traces/），只是没有前端展示
        if tracer is not None:
            self.tracer = tracer
            self._owns_tracer = False
        else:
            self.tracer = CallTracer(new_session_id()).for_agent("lead")
            self._owns_tracer = True

    def reset(self) -> None:
        """清空对话历史，开始新会话。工作记忆快照归档进长期记忆（轻量反思）。"""
        MEMORY.archive_working_memory()
        self.messages = []
        self._conv_start_input = self.llm.total_input_tokens
        self._conv_start_output = self.llm.total_output_tokens
        if self._owns_tracer:
            # /new 即新会话：trace 按会话一文件，重开新文件
            self.tracer = CallTracer(new_session_id()).for_agent("lead")

    @property
    def conv_input_tokens(self) -> int:
        return self.llm.total_input_tokens - self._conv_start_input

    @property
    def conv_output_tokens(self) -> int:
        return self.llm.total_output_tokens - self._conv_start_output

    def compact(self) -> None:
        if not self.messages:
            console.print("[dim]没有可压缩的对话[/dim]")
            return
        self.messages[:] = compact_history(self.llm, self.messages, on_summary=_store_l4_summary)

    @staticmethod
    def _stream_text(text: str) -> None:
        console.print(text, end="", markup=False, highlight=False)

    def run(self, query: str) -> str:
        self.messages.append(Message(role="user", content=query))

        rounds_since_todo = 0   # 记录多少轮没使用Todo Manager
        reminder_cooldown = 0   # 提醒退避冷却计数
        reactive_retries = 0    # 应急压缩重试计数
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            for step in range(self.max_steps):
                inbox = BUS.read_inbox('lead')
                if inbox:
                    self.messages.append(Message(role="user",
                                                 content=f"<inbox>{json.dumps(inbox, indent=2)}</inbox>"))
                notifs = bg_manager.drain_notifications()
                if notifs:
                    notif_text = "\n".join(
                        f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
                    )
                    self.messages.append(Message(role="user",
                                                 content=f"<background-results>\n{notif_text}\n</background-results>"))

                run_pipeline(self.llm, self.messages, on_summary=_store_l4_summary)

                try:
                    seq = None
                    request = LLMRequest(messages=self.messages, tools=self.tools,
                                         max_tokens=8000,
                                         system_prompt=_build_system_prompt(query if step == 0 else ""))
                    seq = self.tracer.llm_start(query=query if step == 0 else None)
                    response: LLMResponse = self.llm.stream(request, on_text=self._stream_text)
                    self.tracer.llm_end(seq, response)
                    if response.content:
                        console.print()
                    if response.usage:
                        total_input_tokens += response.usage.input_tokens
                        total_output_tokens += response.usage.output_tokens
                    reactive_retries = 0
                except Exception as e:
                    self.tracer.llm_error(seq, str(e))
                    if ("prompt_too_long" in str(e).lower() or "too many tokens" in str(e).lower()) \
                            and reactive_retries < MAX_REACTIVE_RETRIES:
                        console.print("[bold yellow]⚠ 上下文溢出，触发应急压缩[/bold yellow]")
                        self.messages[:] = reactive_compact(self.llm, self.messages)
                        reactive_retries += 1
                        continue  # 重试当前 step
                    raise

                self.messages.append(Message(role="assistant",
                                             content=response.content,
                                             tool_calls=response.tool_calls))

                if response.stop_reason != "tool_use":
                    return response.content

                used_todo = False

                for tool in response.tool_calls:
                    console.print(f"[bold yellow]▸ {tool.tool_name}[/bold yellow] [dim cyan]{tool.tool_args}[/dim cyan]")

                    tseq = self.tracer.tool_start(tool.tool_id, tool.tool_name, tool.tool_args)
                    start_t = time.time()
                    output = self.registry.execute(tool.tool_name, tool.tool_args)
                    cost_ms = int((time.time() - start_t) * 1000)

                    output_str = str(output)
                    is_error = output_str.strip().lower().startswith(("error", "exception", "traceback"))
                    self.tracer.tool_end(tseq, tool.tool_id, tool.tool_name, output_str, is_error, cost_ms)

                    label = "error" if is_error else "output"
                    color = "red" if is_error else "green"
                    preview = output_str[:500]
                    if len(output_str) > 500:
                        preview += " ..."
                    console.print(f"[{color}]  {label}:[/{color}] {preview}")

                    self.messages.append(Message(role="tool",
                                                 tool_call_id=tool.tool_id,
                                                 content=str(output)))

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
                        self.messages.append(Message(
                            role="user",
                            content="<reminder>If your plan has evolved or the current subtask is completed, please update your todos. Otherwise continue your work.</reminder>"
                        ))
                        reminder_cooldown = 6

            return "Error: max_steps exceeded before the agent finished."
        finally:
            console.print(f"[dim]⚡ In: {total_input_tokens} · Out: {total_output_tokens}[/dim]")