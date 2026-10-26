import json
import time
from typing import Dict, List, Optional
from rich.console import Console
from rove.tool_registry import ToolRegistry
from rove.prompt.system_prompt import SYSTEM_PROMPT, SEGMENT_ORDER
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


def _build_system_prompt() -> str:
    """System Prompt 逐字节稳定（P1 前缀稳定化）。

    动态记忆块（工作记忆黑板 + 长期记忆检索）不再拼进 system prompt——
    黑板每回合变化会使整个请求前缀缓存全量失效——改为由 _inject_memory_turn
    作为尾部合成 user 消息注入，旧副本留在历史中原样不动，前缀保持只增不改。
    """
    return SYSTEM_PROMPT


def _request_composition(memory_block: bool) -> Dict:
    """请求构成记录（system 各段 + 本回合记忆块有无），供 CallTrace 的 request_composition 使用。"""
    return {"system_segments": list(SEGMENT_ORDER), "memory_block": memory_block}


def _inject_synthetic(messages: List[Message], content: str) -> None:
    """尾部注入一条合成 user 消息（inbox / 后台结果 / 提醒 / 记忆块统一入口）。

    合成消息内容以保留标签开头，L1 的 _is_user_anchor 据此排除出用户锚点。
    """
    if content:
        messages.append(Message(role="user", content=content))


# 记忆回合注入消息的识别前缀（与 L1 锚点排除表、会话标题提取共用同一组标签）
MEMORY_TURN_PREFIXES = ("<working-memory>", "<long-term-memory>")

# 工具失败原因归类：按错误输出前缀识别（context-optimize P3）
FAILURE_CATEGORY_PREFIXES = (
    ("unknown tool", "unknown_tool"),
    ("permission denied", "permission"),
    ("error: 参数校验失败", "schema"),
)


def _categorize_failure(output: str) -> str:
    head = str(output).strip().lower()
    for prefix, category in FAILURE_CATEGORY_PREFIXES:
        if head.startswith(prefix):
            return category
    return "exception"


class LeadAgent:
    def __init__(self, llm: BaseLLMAdapter, registry: ToolRegistry, max_steps: int = 50,
                 tracer: Optional[AgentTracer] = None):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.messages: List[Message] = []
        self.tools = registry.get_schemas()
        self._tool_failures: dict = {}   # (tool_name, category) -> 连续出现次数
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
        self._tool_failures = {}
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

    def _inject_memory_turn(self, query: str) -> bool:
        """黑板 + 长期记忆检索结果作为尾部合成 user 消息注入，每回合给到最新状态。

        内容与历史中最后一条记忆消息完全一致时跳过（旧副本仍在历史里，
        信息不丢失）；内容变化时追加新副本，旧副本随历史老化、交给压缩剪除。
        返回本回合是否有记忆块（供 request_composition 记录）。
        """
        content = MEMORY.build_system_context(query)
        if not content:
            return False
        for msg in reversed(self.messages):
            if msg.role == "user" and msg.content and msg.content.startswith(MEMORY_TURN_PREFIXES):
                if msg.content == content:
                    return True
                break
        _inject_synthetic(self.messages, content)
        return True

    def _handle_tool_failure(self, tool_name: str, output: str) -> None:
        """失败经验沉淀（P3）：同类失败（工具名+原因类别）入长期记忆，
        第二次出现时在尾部注入规避提示，让模型先修正再重试。"""
        category = _categorize_failure(output)
        key = (tool_name, category)
        self._tool_failures[key] = self._tool_failures.get(key, 0) + 1
        count = self._tool_failures[key]
        try:
            MEMORY.long_term.store("episodic", f"tool_fail_{tool_name}_{category}",
                                   f"工具 {tool_name} 因 {category} 失败：{output[:200]}")
        except Exception:
            pass  # 记忆写入失败不影响主流程
        if count >= 2:
            _inject_synthetic(
                self.messages,
                f"<tool-failure-hint>此前 {tool_name} 已因 {category} 失败 {count} 次"
                f"（最近错误：{output[:150]}）。请先修正调用方式再重试；"
                f"若是权限拒绝，不要尝试绕过。</tool-failure-hint>")

    def run(self, query: str) -> str:
        self.messages.append(Message(role="user", content=query))

        rounds_since_todo = 0   # 记录多少轮没使用Todo Manager
        reminder_cooldown = 0   # 提醒退避冷却计数
        reactive_retries = 0    # 应急压缩重试计数
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_read = 0

        try:
            for step in range(self.max_steps):
                inbox = BUS.read_inbox('lead')
                if inbox:
                    _inject_synthetic(self.messages, f"<inbox>{json.dumps(inbox, indent=2)}</inbox>")
                notifs = bg_manager.drain_notifications()
                if notifs:
                    notif_text = "\n".join(
                        f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
                    )
                    _inject_synthetic(self.messages,
                                      f"<background-results>\n{notif_text}\n</background-results>")

                run_pipeline(self.llm, self.messages, on_summary=_store_l4_summary)
                has_memory = self._inject_memory_turn(query if step == 0 else "")

                try:
                    seq = None
                    system_prompt = _build_system_prompt()
                    prompt_composition = _request_composition(has_memory)
                    request = LLMRequest(messages=self.messages, tools=self.tools,
                                         max_tokens=8000,
                                         system_prompt=system_prompt)
                    prompt_composition["message_count"] = len(self.messages)
                    prompt_composition["tool_count"] = len(self.tools)
                    seq = self.tracer.llm_start(query=query if step == 0 else None,
                                                composition=prompt_composition)
                    response: LLMResponse = self.llm.stream(request, on_text=self._stream_text)
                    self.tracer.llm_end(seq, response)
                    if response.content:
                        console.print()
                    if response.usage:
                        total_input_tokens += response.usage.context_input_tokens
                        total_output_tokens += response.usage.output_tokens
                        total_cache_read += response.usage.cache_read_tokens
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

                    if is_error:
                        self._handle_tool_failure(tool.tool_name, str(output))

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
                        _inject_synthetic(
                            self.messages,
                            "<reminder>If your plan has evolved or the current subtask is completed, please update your todos. Otherwise continue your work.</reminder>",
                        )
                        reminder_cooldown = 6

            return "Error: max_steps exceeded before the agent finished."
        finally:
            hit_pct = total_cache_read / total_input_tokens * 100 if total_input_tokens else 0
            console.print(f"[dim]⚡ In: {total_input_tokens} (缓存命中 {hit_pct:.0f}%) · Out: {total_output_tokens}[/dim]")