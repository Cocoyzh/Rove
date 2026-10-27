import json
import time
from dataclasses import dataclass
from typing import List, Literal
from rich.console import Console
from rove.tool_registry import ToolRegistry
from rove.prompt.system_prompt import SYSTEM_PROMPT, TEAM_PROMPT
from rove.compaction.compaction_layers import run_pipeline, reactive_compact, MAX_REACTIVE_RETRIES, compact_history
from rove.memory import MEMORY
from rove.tools.message_bus import BUS
from rove.tools.background import bg_manager
from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import Message
from rove.observers import RunObserver, RichObserver

console = Console()
err_console = Console(stderr=True)

StopKind = Literal["completed", "max_steps", "max_tokens", "refusal", "unexpected_stop"]

# LLM 的 stop_reason → run 的结束原因；不在表里的一律算 unexpected_stop（fail closed）
_STOP_REASONS: dict[str, StopKind] = {
    "end_turn": "completed",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


@dataclass(frozen=True)
class RunResult:
    """一次 run 正常返回时的结果。抛异常的情况（超时、鉴权失败等）由调用方分类。"""
    content: str
    reason: StopKind
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reason == "completed"


def _store_l4_summary(summary: str) -> None:
    """L4/应急压缩的摘要沉淀进长期记忆（Issue #4：压缩与记忆联动）。"""
    MEMORY.long_term.store("episodic", f"summary_{int(time.time())}", summary[:2000])


def _build_system_prompt(query: str, team: bool = False) -> str:
    """System Prompt = 核心指令（+ 团队段）+ 记忆块（工作记忆 + 长期记忆检索）。"""
    prompt = f"{SYSTEM_PROMPT}\n{TEAM_PROMPT}" if team else SYSTEM_PROMPT
    memory_block = MEMORY.build_system_context(query)
    if memory_block:
        return f"{prompt}\n\n{memory_block}"
    return prompt


class LeadAgent:
    def __init__(self, llm: BaseLLMAdapter, registry: ToolRegistry, max_steps: int = 50,
                 observer: RunObserver | None = None):
        self.llm = llm
        self.observer = observer or RichObserver(console)
        self.registry = registry
        self.max_steps = max_steps
        self.messages: List[Message] = []
        self.tools = registry.get_schemas()
        self.team = any(t["name"] == "spawn_teammate" for t in self.tools)
        self._conv_start_input = self.llm.total_input_tokens
        self._conv_start_output = self.llm.total_output_tokens

    def reset(self) -> None:
        """清空对话历史，开始新会话。工作记忆快照归档进长期记忆（轻量反思）。"""
        MEMORY.archive_working_memory()
        self.messages = []
        self._conv_start_input = self.llm.total_input_tokens
        self._conv_start_output = self.llm.total_output_tokens

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

    def run(self, query: str) -> RunResult:
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
                    request = LLMRequest(messages=self.messages, tools=self.tools,
                                         max_tokens=8000,
                                         system_prompt=_build_system_prompt(query if step == 0 else "", self.team))
                    response: LLMResponse = self.llm.stream(request, on_text=self.observer.on_text_delta)
                    self.observer.on_assistant_message(response.content)
                    if response.usage:
                        total_input_tokens += response.usage.input_tokens
                        total_output_tokens += response.usage.output_tokens
                    reactive_retries = 0
                except Exception as e:
                    if ("prompt_too_long" in str(e).lower() or "too many tokens" in str(e).lower()) \
                            and reactive_retries < MAX_REACTIVE_RETRIES:
                        self.observer.on_compaction()
                        self.messages[:] = reactive_compact(self.llm, self.messages)
                        reactive_retries += 1
                        continue  # 重试当前 step
                    raise

                self.messages.append(Message(role="assistant",
                                             content=response.content,
                                             tool_calls=response.tool_calls))

                if response.stop_reason != "tool_use":
                    reason = _STOP_REASONS.get(response.stop_reason, "unexpected_stop")
                    detail = "" if reason == "completed" else f"stop_reason={response.stop_reason}"
                    if reason == "unexpected_stop":
                        err_console.print(f"[yellow]⚠ 未知的 stop_reason={response.stop_reason!r}，按失败处理[/yellow]")
                    return RunResult(response.content, reason, detail)

                used_todo = False

                for tool in response.tool_calls:
                    self.observer.on_tool_call(tool)

                    outcome = self.registry.run(tool.tool_name, tool.tool_args)
                    self.observer.on_tool_result(tool, outcome.content, outcome.is_error, outcome.error_kind)

                    self.messages.append(Message(role="tool",
                                                 tool_call_id=tool.tool_id,
                                                 content=outcome.content))

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

            return RunResult("", "max_steps", f"max_steps={self.max_steps} exceeded before the agent finished")
        finally:
            self.observer.on_usage(total_input_tokens, total_output_tokens)