import json
from typing import List, Dict, Any
from rich.console import Console
from rove.tool_registry import ToolRegistry
from rove.llm_client import client, MODEL, SYSTEM_PROMPT
from rove.compaction.compaction_layers import run_pipeline, reactive_compact, MAX_REACTIVE_RETRIES
from rove.tools.message_bus import BUS
from rove.tools.background import bg_manager

console = Console()

def extract_text(content: List) -> str:
    texts = []

    for block in content:
        if hasattr(block, "text"):
            texts.append(block.text)

    return "\n".join(texts)


def agent_loop(user_query: str, registry: ToolRegistry, max_steps: int = 20):
    messages: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": user_query,
        }
    ]

    tools = registry.get_schemas()
    rounds_since_todo = 0   # 记录多少轮没使用Todo Manager
    reactive_retries = 0    # 应急压缩重试计数

    for step in range(max_steps):
        inbox = BUS.read_inbox('lead')
        if inbox:
            messages.append({
                "role": "user",
                "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>",
            })
        notifs = bg_manager.drain_notifications()
        if notifs:
            notif_text = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
            )
            messages.append({"role": "user", "content": f"<background-results>\n{notif_text}\n</background-results>"})

        run_pipeline(messages)

        try:
            response = client.messages.create(
                messages=messages,
                model=MODEL,
                system=SYSTEM_PROMPT,
                tools=tools,
                max_tokens=8000,
            )
            reactive_retries = 0
        except Exception as e:
            if ("prompt_too_long" in str(e).lower() or "too many tokens" in str(e).lower()) \
                    and reactive_retries < MAX_REACTIVE_RETRIES:
                console.print("[bold yellow]⚠ 上下文溢出，触发应急压缩[/bold yellow]")
                messages[:] = reactive_compact(messages)
                reactive_retries += 1
                continue  # 重试当前 step
            raise

        messages.append({
            "role": "assistant",
            "content": response.content
        })

        if response.stop_reason != "tool_use":
            return extract_text(response.content)

        results = []
        used_todo = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_args = block.input

            console.print(f"[bold yellow]▸ {tool_name}[/bold yellow] [dim cyan]{tool_args}[/dim cyan]")

            output = registry.execute(tool_name, tool_args)

            is_error = str(output).strip().lower().startswith(("error", "exception", "traceback"))
            label = "error" if is_error else "output"
            color = "red" if is_error else "green"
            preview = str(output)[:500]
            if len(str(output)) > 500:
                preview += " ..."
            console.print(f"[{color}]  {label}:[/{color}] {preview}")

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output),
            })

            if tool_name == "todo":
                used_todo = True

        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1

        if rounds_since_todo >= 3:
            results.append({
                "type": "text",
                "text": "<reminder>Update your todos before continuing.</reminder>",
            })

        messages.append({
            "role": "user",
            "content": results,
        })

    return "Error: max_steps exceeded before the agent finished."
