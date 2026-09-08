from dataclasses import dataclass
from typing import List, Optional, Any
from ..tool_registry import Tool


@dataclass
class TodoItem:
    id: str
    text: str
    status: str
    owner: str = ""


class TodoManager:
    VALID_STATUS = {"pending", "in_progress", "completed"}

    def __init__(self, task_manager: Optional[Any] = None):
        self.items: List[TodoItem] = []
        self.task_manager = task_manager

    def update(self, items: List[dict]) -> str:
        if len(items) > 30:
            raise ValueError("TodoManager only accepts up to 30 items")

        new_items: List[TodoItem] = []
        in_progress_indices: List[int] = []

        for i, item in enumerate(items):
            item_id = str(item.get("id", str(i + 1)))
            text = str(item.get("text") or item.get("subject", "")).strip()
            status = str(item.get("status", "pending")).lower()
            owner = str(item.get("owner", ""))

            if not text:
                raise ValueError(f"Todo #{item_id}: text is required")
            if status not in self.VALID_STATUS:
                raise ValueError(f"Todo #{item_id}: invalid status '{status}', must be one of {self.VALID_STATUS}")

            if status == "in_progress":
                in_progress_indices.append(len(new_items))

            new_items.append(TodoItem(id=item_id, text=text, status=status, owner=owner))

        # 容错自愈：若有多个 in_progress，自动保留最后一个（当前聚焦项），前面的降级为 pending
        auto_healed = False
        if len(in_progress_indices) > 1:
            auto_healed = True
            for idx in in_progress_indices[:-1]:
                new_items[idx].status = "pending"

        self.items = new_items

        # 若绑定了 TaskManager，以智能 Upsert 同步写入 .tasks/ 文件看板
        if self.task_manager is not None:
            sync_payload = [
                {
                    "id": item.id,
                    "text": item.text,
                    "status": item.status,
                    "owner": item.owner,
                }
                for item in self.items
            ]
            self.task_manager.sync_todos(sync_payload)

        return self.render(auto_healed=auto_healed)

    def render(self, auto_healed: bool = False) -> str:
        if not self.items:
            return "No todos found"

        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(item.status, "[?]")
            owner_info = f" (@{item.owner})" if item.owner else ""
            lines.append(f"{marker} #{item.id}: {item.text}{owner_info}")

        done = sum(1 for item in self.items if item.status == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")

        if auto_healed:
            lines.append("\n[Notice: Only one todo can be in_progress simultaneously. Earlier item(s) were automatically set to pending.]")

        return "\n".join(lines)


def build_todo_tool(task_manager: Optional[Any] = None) -> Tool:
    manager = TodoManager(task_manager=task_manager)
    return Tool(
        name="todo",
        description="Update the task list. Use this to organize, track progress, and coordinate multi-step tasks. Syncs with the project task board.",
        input_schema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["id", "text", "status"],
                    },
                }
            },
            "required": ["items"],
        },
        handler=lambda **kw: manager.update(kw["items"]),
    )


_todo = TodoManager()
todo_tool = build_todo_tool()
