"""
Context Manager
四层压缩流水线：L3 → L2 → L1 → L4
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
MAX_MESSAGES = 60
KEEP_HEAD = 5
KEEP_TAIL = 35

# L2: Micro Compact
KEEP_RECENT = 10
COMPACT_THRESHOLD = 4_000

# L3: Tool Results Budget
TOOL_RESULT_BUDGET = 200_000
PERSIST_THRESHOLD = 60_000
PREVIEW_CHARS = 3_000

# L4: Auto Compact
CONTEXT_LIMIT = 50_000
MAX_SUMMARY_TOKENS = 2_000

# Watermarks
L2_WATERMARK_RATIO = 0.10
L1_WATERMARK_RATIO = 0.20
L4_WATERMARK_RATIO = 0.40

# L4 摘要提示词
SUMMARY_PROMPT = """请总结以下对话，保留关键信息：
1. 用户的目标（分析、编码、调试等）
2. 关键发现或决策（含具体数值、文件路径、代码结果）
3. 已读取或修改的文件
4. 未完成的工作
5. 用户的约束条件与中途指令变更

要求：简洁、保留具体细节（数值、行号、列名、错误信息、需求演进）、不要复述工具调用过程。

对话内容：
{conversation}"""


def fast_estimate_text_tokens(text: str, cjk_ratio: float = 1.3) -> int:
    if not text:
        return 0
    c_len = len(text)
    b_len = len(text.encode("utf-8", errors="ignore"))
    non_ascii = (b_len - c_len) // 2
    ascii_chars = c_len - non_ascii
    return int(ascii_chars / 3.8 + non_ascii * cjk_ratio)


def estimate_messages_tokens(messages: list[Message], llm: BaseLLMAdapter | None = None) -> int:
    """估算消息列表的 Token 数量。支持基于 llm 的增量与自适应分词校准。"""
    cjk_ratio = getattr(llm, "cjk_token_ratio", 1.3) if llm else 1.3

    if llm and getattr(llm, "last_input_tokens", 0) > 0:
        new_msgs = []
        for m in reversed(messages):
            if m.role == "assistant":
                break
            new_msgs.append(m)

        delta_tokens = 0
        for m in new_msgs:
            delta_tokens += 4 + fast_estimate_text_tokens(m.content or "", cjk_ratio=cjk_ratio)

        base_tokens = llm.last_input_tokens + getattr(llm, "last_output_tokens", 0)
        return base_tokens + delta_tokens

    total = 0
    for msg in messages:
        total += 4
        content = msg.content or ""
        if content:
            total += fast_estimate_text_tokens(content, cjk_ratio=cjk_ratio)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args_str = json.dumps(tc.tool_args, ensure_ascii=False)
                total += len(tc.tool_name) + int(len(args_str) / 3.5) + 10
    return total


def _has_tool_use(msg: Message) -> bool:
    """assistant 消息是否带着工具调用请求"""
    return msg.role == "assistant" and bool(msg.tool_calls)


# ---------------- L3 -------------------
def tool_result_budget(messages: list[Message], tool_results_dir: Path) -> list[Message]:
    """L3: 当轮 tool_result 超出预算时持久化到磁盘并保留预览。"""
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

        half_slice = PREVIEW_CHARS // 2
        head_slice = content[:half_slice]
        tail_slice = content[-half_slice:] if len(content) > PREVIEW_CHARS else ""
        omitted_chars = len(content) - len(head_slice) - len(tail_slice)
        line_count = content.count("\n") + 1

        if tail_slice and omitted_chars > 0:
            preview_body = (
                f"{head_slice}\n"
                f"\n... [已省略中间 {omitted_chars} 字符 / 约 {line_count} 行。完整输出已落盘，如需查看具体章节请使用 read_file 并指定起止行号] ...\n\n"
                f"{tail_slice}"
            )
        else:
            preview_body = head_slice

        msg.content = (
            f"<persisted-output>\n"
            f"Full output path : {str(persist_path)}\n"
            f"Total size : {len(content)} 字符 ({line_count} 行)\n"
            f"Preview :\n{preview_body}\n"
            f"</persisted-output>"
        )
        persisted += 1
        total_size = sum(len(m.content or "") for m in tool_msgs)

    if persisted:
        console.print(f"[bold yellow]⚠ L3 budget: 持久化了 {persisted} 个超大工具输出到 tool-results/[/bold yellow]")
    return messages


# ---------------- L2 -------------------
def micro_compact(messages: list[Message]) -> list[Message]:
    """L2: 压缩较早轮次的超长工具输出。"""
    tool_results = [msg for msg in messages if msg.role == "tool"]
    if len(tool_results) <= KEEP_RECENT:
        return messages

    truncated = 0
    for msg in tool_results[:-KEEP_RECENT]:
        content = msg.content or ""
        if "<persisted-output>" in content:
            continue
        if len(content) > COMPACT_THRESHOLD:
            head = content[:500]
            tail = content[-500:]
            omitted = len(content) - 1000
            msg.content = (
                f"{head}\n"
                f"\n... [Output truncated ({omitted} chars omitted). Use read_file with line ranges or grep if specific sections needed] ...\n\n"
                f"{tail}"
            )
            truncated += 1

    if truncated:
        console.print(f"[bold yellow]⚠ L2 micro: 截断了 {truncated} 条旧工具结果[/bold yellow]")
    return messages


# ---------------- L1 -------------------
def _is_user_anchor(msg: Message) -> bool:
    if msg.role != "user" or not msg.content:
        return False
    text = msg.content.strip()
    return not text.startswith(("[已压缩", "[对话已压缩]", "[应急压缩]"))


def snip_compact(messages: list[Message]) -> list[Message]:
    """L1: 超过消息阈值时剪除中间非用户消息。"""
    if len(messages) <= MAX_MESSAGES:
        return messages

    head_end = KEEP_HEAD
    tail_start = len(messages) - KEEP_TAIL

    if head_end > 0 and (messages[head_end - 1].role == "tool"
                         or _has_tool_use(messages[head_end - 1])):
        while head_end < len(messages) and messages[head_end].role == "tool":
            head_end += 1

    if 0 < tail_start < len(messages) and messages[tail_start].role == "tool":
        while tail_start > 0 and messages[tail_start - 1].role == "tool":
            tail_start -= 1
        if tail_start > 0 and _has_tool_use(messages[tail_start - 1]):
            tail_start -= 1

    if head_end >= tail_start:
        return messages

    middle_messages = messages[head_end:tail_start]
    anchors_with_idx = [(i, m) for i, m in enumerate(middle_messages) if _is_user_anchor(m)]

    if not anchors_with_idx:
        snipped_count = len(middle_messages)
        placeholder = Message(role="user", content=f"[已压缩 {snipped_count} 条消息]")
        console.print(f"[bold yellow]⚠ L1 snip: 压缩了 {snipped_count} 条消息[/bold yellow]")
        return messages[:head_end] + [placeholder] + messages[tail_start:]

    new_middle: list[Message] = []
    last_idx = 0
    total_pruned = 0

    for idx, anchor_msg in anchors_with_idx:
        pruned_segment = middle_messages[last_idx:idx]
        if pruned_segment:
            count = len(pruned_segment)
            total_pruned += count
            new_middle.append(Message(role="user", content=f"[已压缩 {count} 条消息]"))
        new_middle.append(anchor_msg)
        last_idx = idx + 1

    trailing_segment = middle_messages[last_idx:]
    if trailing_segment:
        count = len(trailing_segment)
        total_pruned += count
        new_middle.append(Message(role="user", content=f"[已压缩 {count} 条消息]"))

    console.print(f"[bold yellow]⚠ L1 snip: 压缩了 {total_pruned} 条消息[/bold yellow]")
    return messages[:head_end] + new_middle + messages[tail_start:]


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
    """L4: 前三层压不下去时，调 LLM 做全量摘要并保留近期尾部消息。"""
    transcript_path = _write_transcript(messages)
    console.print(f"[bold yellow]⚠ L4 压缩: 对话已存档[/bold yellow] [dim]{transcript_path}[/dim]")

    summary = _summarize_history(llm, messages)
    console.print(f"[bold yellow]⚠ L4 压缩: 摘要完成[/bold yellow] [dim]{len(summary)} 字符[/dim]")

    tail_count = min(6, len(messages))
    tail_start = max(0, len(messages) - tail_count)
    if 0 < tail_start < len(messages) and messages[tail_start].role == "tool":
        while tail_start > 0 and messages[tail_start - 1].role == "tool":
            tail_start -= 1
        if tail_start > 0 and _has_tool_use(messages[tail_start - 1]):
            tail_start -= 1

    summary_msg = Message(role="user", content=f"[对话已压缩]\n\n{summary}")
    return [summary_msg, *messages[tail_start:]]


def reactive_compact(llm: BaseLLMAdapter, messages: list[Message]) -> list[Message]:
    """应急压缩：API 返回 prompt_too_long 时的最后手段。"""
    transcript_path = _write_transcript(messages)
    console.print(f"[bold yellow]⚠ 应急压缩: 对话已存档[/bold yellow] [dim]{transcript_path}[/dim]")

    summary = _summarize_history(llm, messages)

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
    """压缩流水线入口：L3 → L2 → L1 → L4。
    在每轮 LLM 调用前调用，原地修改 messages。
    """
    context_window = getattr(llm, "context_window", 1_000_000)

    messages[:] = tool_result_budget(messages, TOOL_RESULTS_DIR)

    current_tokens = estimate_messages_tokens(messages, llm=llm)

    l2_limit = int(context_window * L2_WATERMARK_RATIO)
    if current_tokens >= l2_limit:
        messages[:] = micro_compact(messages)
        current_tokens = estimate_messages_tokens(messages)

    l1_limit = int(context_window * L1_WATERMARK_RATIO)
    if current_tokens >= l1_limit and len(messages) > MAX_MESSAGES:
        messages[:] = snip_compact(messages)
        current_tokens = estimate_messages_tokens(messages)

    l4_limit = int(context_window * L4_WATERMARK_RATIO)
    if current_tokens > l4_limit:
        console.print("[bold yellow]⚠ 上下文超标，触发 L4 摘要[/bold yellow]")
        messages[:] = compact_history(llm, messages)

    return messages
