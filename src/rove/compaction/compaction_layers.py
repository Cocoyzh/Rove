"""
Context Manager
四层压缩流水线：L3 → L1 → L2 → L4
前三层零 API 调用，L4 在超标时调 LLM 做摘要。
"""

import json
import time
from pathlib import Path
from rich.console import Console
from rove.llm_client import client, MODEL
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
def _block_type(block):
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)

def _has_tool_use(msg: dict) -> bool:
    if msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if _block_type(block) == "tool_use":
            return True
    return False

def _is_tool_result(msg: dict) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if _block_type(block) == "tool_result":
            return True
    return False


def snip_compact(messages: list) -> list:
    if len(messages) <=MAX_MESSAGES:
        return messages

    head_end = KEEP_HEAD    # [0, head_end)
    tail_start = len(messages) - KEEP_TAIL  # [tail_start, end)

    if head_end > 0 and _has_tool_use(messages[head_end-1]):
        if head_end < len(messages) and _is_tool_result(messages[head_end]):
            head_end += 1

    if (tail_start > 0 and _is_tool_result(messages[tail_start])
        and _has_tool_use(messages[tail_start-1])):
        tail_start -= 1

    if head_end >= tail_start:
        return messages

    snipped_count = tail_start - head_end
    placeholder = {"role": "user", "content": f"[已压缩 {snipped_count} 条消息]"}

    return messages[:head_end] + [placeholder] + messages[tail_start:]

# ---------------- L2 -------------------
def micro_compact(messages: list) -> list:
    tool_results: list[tuple[int, int, dict]] = []
    for msg_index, msg in enumerate(messages):
        if not isinstance(msg.get("content"), list): continue
        for block_index, block in enumerate(msg["content"]):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_results.append((msg_index, block_index, block))

    if  len(tool_results) <= KEEP_RECENT:
        return messages

    for _, _, block in tool_results[:-KEEP_RECENT]:
        if len(block.get("content", "")) > 120:
            block["content"] = "[Earlier tool result compacted, Re-run if needed.]"
    return messages

# ---------------- L3 -------------------
def tool_result_budget(messages: list, tool_results_dir: Path) -> list:
    if not messages:
        return messages

    latest_msg = messages[-1]
    if latest_msg.get("role") != "user" or not isinstance(latest_msg.get("content"), list):
        return messages

    blocks = []
    for block in latest_msg["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            blocks.append(block)

    if not blocks:
        return messages

    total_size = sum(len(str(block.get("content", ""))) for block in blocks)
    if total_size <= TOOL_RESULT_BUDGET:
        return messages

    reranked_blocks = sorted(blocks, key=lambda b: len(str(b.get("content", ""))), reverse=True)
    for block in reranked_blocks:
        if total_size <= TOOL_RESULT_BUDGET:
            break
        content = str(block.get("content", ""))
        if len(content) <= PERSIST_THRESHOLD:
            continue

        tool_use_id = block.get("tool_use_id", "unknown")
        tool_results_dir.mkdir(parents=True, exist_ok=True)
        persist_path = tool_results_dir / f"{tool_use_id}.txt"
        if not persist_path.exists():
            persist_path.write_text(content, encoding="utf-8")

        block["content"] = (
            f"<persisted-output>\n"
            f"Full output : {str(persist_path)}\n"
            f"Preview : {content[:PREVIEW_CHARS]}\n"
            f"</persisted-output>"
        )

        total_size = sum(len(str(block.get("content", ""))) for block in blocks)
    return messages

# ---------------- L4 -------------------

def _estimate_size(messages: list) -> int:
    """估算消息列表的字符大小"""
    return len(str(messages))


def _write_transcript(messages: list) -> Path:
    """压缩前将完整对话存档到磁盘，防止信息丢失。"""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")
    return path


def _summarize_history(messages: list) -> str:
    """调 LLM 对对话历史做摘要，保留目标、发现、约束。"""
    conversation = json.dumps(messages, default=str, ensure_ascii=False)[:80000]
    prompt = SUMMARY_PROMPT.format(conversation=conversation)

    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_SUMMARY_TOKENS,
    )

    return "\n".join(
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", None) == "text"
    ).strip() or "(摘要为空)"


def compact_history(messages: list) -> list:
    """L4: 前三层压不下去时，调 LLM 做全量摘要。
    存档 → 摘要 → 替换整个 messages。
    """
    transcript_path = _write_transcript(messages)
    console.print(f"[bold yellow]⚠ L4 压缩: 对话已存档[/bold yellow] [dim]{transcript_path}[/dim]")

    summary = _summarize_history(messages)
    console.print(f"[bold yellow]⚠ L4 压缩: 摘要完成[/bold yellow] [dim]{len(summary)} 字符[/dim]")

    return [{"role": "user", "content": f"[对话已压缩]\n\n{summary}"}]


def reactive_compact(messages: list) -> list:
    """应急压缩：API 返回 prompt_too_long 时的最后手段。
    存档 → 摘要 → 只保留最近 5 条消息 + 摘要。
    """
    transcript_path = _write_transcript(messages)
    console.print(f"[bold yellow]⚠ 应急压缩: 对话已存档[/bold yellow] [dim]{transcript_path}[/dim]")

    summary = _summarize_history(messages)

    # 保留最近 5 条消息，但不能拆散 tool_use/tool_result 配对
    tail_start = max(0, len(messages) - 5)
    if (0 < tail_start < len(messages)
            and _is_tool_result(messages[tail_start])
            and _has_tool_use(messages[tail_start - 1])):
        tail_start -= 1

    return [
        {"role": "user", "content": f"[应急压缩]\n\n{summary}"},
        *messages[tail_start:],
    ]


# ---------------- Pipeline -------------------
MAX_REACTIVE_RETRIES = 1


def run_pipeline(messages: list) -> list:
    """压缩流水线入口：L3 → L1 → L2 → L4。
    在每轮 LLM 调用前调用，原地修改 messages。
    """
    messages[:] = tool_result_budget(messages, TOOL_RESULTS_DIR)  # L3
    messages[:] = snip_compact(messages)                           # L1
    messages[:] = micro_compact(messages)                          # L2

    if _estimate_size(messages) > CONTEXT_LIMIT:
        console.print("[bold yellow]⚠ 上下文超标，触发 L4 摘要[/bold yellow]")
        messages[:] = compact_history(messages)

    return messages
