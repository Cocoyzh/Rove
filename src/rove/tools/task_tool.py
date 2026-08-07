import json

from ..task_manager import TaskManager
from ..tool_registry import Tool

def build_create_task_tool(task_manager: TaskManager) -> Tool:
    return Tool(
        name="task_create",
        description="Create a new task",
        input_schema={
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string"
                },
                "description": {
                    "type": "string"
                }
            },
            "required": ["subject"]
        },
        handler=lambda **kw: task_manager.create(
            subject=kw["subject"],
            description=kw.get("description", "")
        )
    )

def build_update_task_tool(task_manager: TaskManager) -> Tool:
    return Tool(
        name="task_update",
        description="Update a task's status or dependencies",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed"]
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"}
                },
                "remove_blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"}
                }
            },
            "required": ["task_id"]
        },
        handler=lambda **kw: task_manager.update(
            task_id=kw["task_id"],
            status=kw.get("status", ""),
            add_blocked_by=kw.get("add_blocked_by", []),
            remove_blocked_by=kw.get("remove_blocked_by", []),
        )
    )

def build_get_task_tool(task_manager: TaskManager) -> Tool:
    return Tool(
        name="task_get",
        description="Get full details of a task by ID",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"}
            },
            "required": ["task_id"]
        },
        handler=lambda **kw: task_manager.get(
            task_id=kw["task_id"]
        )
    )

def build_list_all_task_tool(task_manager: TaskManager) -> Tool:
    return Tool(
        name="task_list",
        description="List all tasks with status summary.",
        input_schema={
            "type": "object",
            "properties": {}
        },
        handler=lambda **kw: task_manager.list_all()
    )


def build_scan_tasks_tool(task_manager: TaskManager) -> Tool:
    return Tool(
        name="scan_tasks",
        description="Scan the task board for unclaimed tasks (pending, no owner, no blockers).",
        input_schema={
            "type": "object",
            "properties": {}
        },
        handler=lambda **kw: json.dumps(task_manager.scan_unclaimed_tasks(), indent=2),
    )


def build_claim_task_tool(task_manager: TaskManager, owner: str) -> Tool:
    return Tool(
        name="claim_task",
        description="Atomically claim a task from the board by ID. Ownership is locked to you.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID of the task to claim."},
            },
            "required": ["task_id"],
        },
        handler=lambda **kw: task_manager.claim_task(kw["task_id"], owner),
    )
