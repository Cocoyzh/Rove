import json
from typing import List
from rich.console import Console
from rove.tool_registry import ToolRegistry
from rove.prompt.system_prompt import SYSTEM_PROMPT
from rove.compaction.compaction_layers import run_pipeline, reactive_compact, MAX_REACTIVE_RETRIES
from rove.tools.message_bus import BUS
from rove.tools.background import bg_manager
from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import Message

console = Console()


class LeadAgent:
    def __init__(self, llm: BaseLLMAdapter, registry: ToolRegistry, max_steps: int = 50):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.messages: List[Message] = []
        self.tools = registry.get_schemas()

    def reset(self) -> None:
        """清空对话历史，开始新会话。"""
        self.messages = []

    def run(self, query: str) -> str:
        self.messages.append(Message(role="user", content=query))

        rounds_since_todo = 0   # 记录多少轮没使用Todo Manager
        reactive_retries = 0    # 应急压缩重试计数

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

            run_pipeline(self.llm, self.messages)

            try:
                request = LLMRequest(messages=self.messages, tools=self.tools,
                                     max_tokens=8000, system_prompt=SYSTEM_PROMPT)
                response: LLMResponse = self.llm.complete(request)
                reactive_retries = 0
            except Exception as e:
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

                output = self.registry.execute(tool.tool_name, tool.tool_args)

                is_error = str(output).strip().lower().startswith(("error", "exception", "traceback"))
                label = "error" if is_error else "output"
                color = "red" if is_error else "green"
                preview = str(output)[:500]
                if len(str(output)) > 500:
                    preview += " ..."
                console.print(f"[{color}]  {label}:[/{color}] {preview}")

                self.messages.append(Message(role="tool",
                                             tool_call_id=tool.tool_id,
                                             content=str(output)))

                if tool.tool_name == "todo":
                    used_todo = True

            rounds_since_todo = 0 if used_todo else rounds_since_todo + 1

            if rounds_since_todo >= 3:
                self.messages.append(Message(role="user",
                                             content="<reminder>Update your todos before continuing.</reminder>"))

        return "Error: max_steps exceeded before the agent finished."
