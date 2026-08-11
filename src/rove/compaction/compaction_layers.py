"""
Context Manager
四层压缩流水线：L3 → L1 → L2 → L4
前三层零 API 调用，L4 在超标时调 LLM 做摘要。
"""

import json
import time
from pathlib import Path
from rich.console import Console
from rove.llm_adapters import BaseLLMAdapter
from rove.llm import LLMResponse, LLMRequest
from rove.messages import Message
from rove.paths import TOOL_RESULTS_DIR, TRANSCRIPT_DIR

console = Console()


# L1: Snip Compact
MAX_MESSAGES = 50
KEEP_HEAD = 3
KEEP_TAIL = 47

# L2: Micro Compact
KEEP_RECENT = 3 # 保留最近三轮的工具调用结果

# L3: Tool Results Budget
TOOL_RESULT_BUDGET = 200_000    # L3 单轮 tool_result 总大小上限（字节）
PERSIST_THRESHOLD = 30_000      # L3 单个输出持久化阈值（字节）
PREVIEW_CHARS = 2000            # L3 持久化后保留的预览字符数

# L4: Auto Compact
CONTEXT_LIMIT = 50_000          # 触发 L4 的字符数阈值
MAX_SUMMARY_TOKENS = 2000       # LLM 摘要的 max_tokens

# L4 摘要提示词
SUMMARY_PROMPT = """请总结以下对话，保留关键信息：
1. 用户的目标（分析、编码、调试等）
2. 关键发现或决策（含具体数值、文件路径、代码结果）
3. 已读取或修改的文件
4. 未完成的工作
5. 用户的约束条件

要求：简洁、保留具体细节（数值、行号、列名、错误信息）、不要复述工具调用过程。

对话内容：
{conversation}"""


# ---------------- L1 -------------------
def _has_tool_use(msg: Message) -> bool:
    """assistant 消息是否带着工具调用请求"""
    return msg.role == "assistant" and bool(msg.tool_calls)

def snip_compact(messages: list[Message]) -> list[Message]:
    if len(messages) <= MAX_MESSAGES:
        return messages

    head_end = KEEP_HEAD    # [0, head_end)
    tail_start = len(messages) - KEEP_TAIL  # [tail_start, end)

    # head 边界：最后一格是 tool 结果或 assistant-with-tools 时，
    # 向后扩展整串 tool，保证 [assistant, tool*] 完整进入 head，防止拆散配对
    if head_end > 0 and (messages[head_end - 1].role == "tool"
                         or _has_tool_use(messages[head_end - 1])):
        while head_end < len(messages) and messages[head_end].role == "tool":
            head_end += 1

    # tail 边界落在 tool 结果上 → 向前包含整串 tool 及其 assistant，防止拆散配对
    if 0 < tail_start < len(messages) and messages[tail_start].role == "tool":
        while tail_start > 0 and messages[tail_start - 1].role == "tool":
            tail_start -= 1
        if tail_start > 0 and _has_tool_use(messages[tail_start - 1]):
            tail_start -= 1

    if head_end >= tail_start:
        return messages

    snipped_count = tail_start - head_end
    placeholder = Message(role="user", content=f"[已压缩 {snipped_count} 条消息]")

    console.print(f"[bold yellow]⚠ L1 snip: 压缩了 {snipped_count} 条消息[/bold yellow]")
    return messages[:head_end] + [placeholder] + messages[tail_start:]

# ---------------- L2 -------------------
def micro_compact(messages: list[Message]) -> list[Message]:
    tool_results = [msg for msg in messages if msg.role == "tool"]
    if len(tool_results) <= KEEP_RECENT:
        return messages

    truncated = 0
    for msg in tool_results[:-KEEP_RECENT]:
        if msg.content and len(msg.content) > 120:
            msg.content = "[Earlier tool result compacted, Re-run if needed.]"
            truncated += 1

    if truncated:
        console.print(f"[bold yellow]⚠ L2 micro: 截断了 {truncated} 条旧工具结果[/bold yellow]")
    return messages

# ---------------- L3 -------------------
def tool_result_budget(messages: list[Message], tool_results_dir: Path) -> list[Message]:
    if not messages:
        return messages

    # 最近一轮的结果 = 结尾连续的一串 tool 消息（并行调用可能不止一条）
    tool_msgs: list[Message] = []
    for msg in reversed(messages):
        if msg.role != "tool":
            break
        tool_msgs.append(msg)
    tool_msgs.reverse()

    if not tool_msgs:
        return messages

    total_size = sum(len(msg.content or "") for msg in tool_msgs)
    if total_size <= TOOL_RESULT_BUDGET:
        return messages

    reranked = sorted(tool_msgs, key=lambda m: len(m.content or ""), reverse=True)
    persisted = 0
    for msg in reranked:
        if total_size <= TOOL_RESULT_BUDGET:
            break
        content = msg.content or ""
        if len(content) <= PERSIST_THRESHOLD:
            continue

        tool_results_dir.mkdir(parents=True, exist_ok=True)
        persist_path = tool_results_dir / f"{msg.tool_call_id}.txt"
        if not persist_path.exists():
            persist_path.write_text(content, encoding="utf-8")

        msg.content = (
            f"<persisted-output>\n"
            f"Full output : {str(persist_path)}\n"
            f"Preview : {content[:PREVIEW_CHARS]}\n"
            f"</persisted-output>"
        )
        persisted += 1

        total_size = sum(len(m.content or "") for m in tool_msgs)

    if persisted:
        console.print(f"[bold yellow]⚠ L3 budget: 持久化了 {persisted} 个超大工具输出到 tool-results/[/bold yellow]")
    return messages

# ---------------- L4 -------------------

def _estimate_size(messages: list[Message]) -> int:
    """估算消息列表的字符大小"""
    return len(json.dumps([m.to_dict() for m in messages], ensure_ascii=False))


def _write_transcript(messages: list[Message]) -> Path:
    """压缩前将完整对话存档到磁盘，防止信息丢失。"""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg.to_dict(), default=str, ensure_ascii=False) + "\n")
    return path


def _summarize_history(llm: BaseLLMAdapter, messages: list[Message]) -> str:
    """调 LLM 对对话历史做摘要，保留目标、发现、约束。"""
    conversation = json.dumps([m.to_dict() for m in messages],
                              default=str, ensure_ascii=False)[:80000]
    prompt = SUMMARY_PROMPT.format(conversation=conversation)

    request = LLMRequest(messages=[Message(role="user", content=prompt)], tools=[],
                         max_tokens=MAX_SUMMARY_TOKENS)
    response: LLMResponse = llm.complete(request)

    return response.content or "(摘要为空)"


def compact_history(llm: BaseLLMAdapter, messages: list[Message]) -> list[Message]:
    """L4: 前三层压不下去时，调 LLM 做全量摘要。
    存档 → 摘要 → 替换整个 messages。
    """
    transcript_path = _write_transcript(messages)
    console.print(f"[bold yellow]⚠ L4 压缩: 对话已存档[/bold yellow] [dim]{transcript_path}[/dim]")

    summary = _summarize_history(llm, messages)
    console.print(f"[bold yellow]⚠ L4 压缩: 摘要完成[/bold yellow] [dim]{len(summary)} 字符[/dim]")

    return [Message(role="user", content=f"[对话已压缩]\n\n{summary}")]


def reactive_compact(llm: BaseLLMAdapter, messages: list[Message]) -> list[Message]:
    """应急压缩：API 返回 prompt_too_long 时的最后手段。
    存档 → 摘要 → 只保留最近 5 条消息 + 摘要。
    """
    transcript_path = _write_transcript(messages)
    console.print(f"[bold yellow]⚠ 应急压缩: 对话已存档[/bold yellow] [dim]{transcript_path}[/dim]")

    summary = _summarize_history(llm, messages)

    # 保留最近 5 条消息，但不能拆散 tool_calls/tool 结果配对
    tail_start = max(0, len(messages) - 5)
    if 0 < tail_start < len(messages) and messages[tail_start].role == "tool":
        while tail_start > 0 and messages[tail_start - 1].role == "tool":
            tail_start -= 1
        if tail_start > 0 and _has_tool_use(messages[tail_start - 1]):
            tail_start -= 1

    return [
        Message(role="user", content=f"[应急压缩]\n\n{summary}"),
        *messages[tail_start:],
    ]


# ---------------- Pipeline -------------------
MAX_REACTIVE_RETRIES = 1


def run_pipeline(llm: BaseLLMAdapter, messages: list[Message]) -> list[Message]:
    """压缩流水线入口：L3 → L1 → L2 → L4。
    在每轮 LLM 调用前调用，原地修改 messages。
    """
    messages[:] = tool_result_budget(messages, TOOL_RESULTS_DIR)  # L3
    messages[:] = snip_compact(messages)                           # L1
    messages[:] = micro_compact(messages)                          # L2

    if _estimate_size(messages) > CONTEXT_LIMIT:
        console.print("[bold yellow]⚠ 上下文超标，触发 L4 摘要[/bold yellow]")
        messages[:] = compact_history(llm, messages)

    return messages
