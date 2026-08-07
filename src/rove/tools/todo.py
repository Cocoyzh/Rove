from dataclasses import dataclass
from typing import List
from ..tool_registry import Tool


@dataclass
class TodoItem:
    id: str
    text: str
    status: str


class TodoManager:

    VALID_STATUS = {"pending", "in_progress", "completed"}

    def __init__(self):
        self.items: List[TodoItem] = []

    def update(self, items: List[dict]) -> str:
        if len(items) > 20:
            raise ValueError("TodoManager only accepts up to 20 items")

        new_items: List[TodoItem] = []
        in_progress_count = 0

        for i, item in enumerate(items):
            item_id = str(item.get("id", str(i + 1)))
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()

            if not text:
                raise ValueError(f"Todo {item_id}: text is required")
            if status not in self.VALID_STATUS:
                raise ValueError(f"Todo {item_id}: invalid status: {status}")
            if status == "in_progress":
                in_progress_count += 1

            new_items.append(TodoItem(id=item_id, text=text, status=status))

        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")

        self.items = new_items
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos found"

        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item.status]
            lines.append(f"{marker} #{item.id}: {item.text}")

        done = sum(1 for item in self.items if item.status == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


_todo = TodoManager()

todo_tool = Tool(
    name="todo",
    description="Update task list. Track progress on multi-step tasks.",
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
    handler=lambda **kw: _todo.update(kw["items"]),
)
