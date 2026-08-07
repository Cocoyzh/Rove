import threading
import uuid
import subprocess
from ..tool_registry import Tool
from rove.paths import WORKSPACE_ROOT


class BackGroundManager:
    def __init__(self):
        self.tasks = {}
        self._lock = threading.Lock()
        self._notification_queue = []

    def run(self, command: str) -> str:
        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {"status": "running", "command": command, "result": None}
        thread = threading.Thread(target=self._execute, args=(task_id, command), daemon=True)
        thread.start()
        return f"Background task {task_id} started: {command[:80]}"

    def _execute(self, task_id: str, command: str):
        try:
            s = subprocess.run(
                command, shell=True, cwd=WORKSPACE_ROOT,
                capture_output=True, text=True, timeout=300,
            )
            output = (s.stdout + s.stderr).strip()[:50000]
            status = "completed"
        except subprocess.TimeoutExpired:
            output = "Error: timeout(300s)"
            status = "timeout"
        except Exception as e:
            output = f"Error: {e}"
            status = "error"

        with self._lock:
            self.tasks[task_id]["status"] = status
            self.tasks[task_id]["result"] = output or "(no output)"
            self._notification_queue.append({
                "task_id": task_id,
                "status": status,
                "result": (output or "(no output)")[:500],
                "command": command[:80],
            })

    def check(self, task_id: str = None) -> str:
        if task_id:
            t = self.tasks.get(task_id)
            if not t:
                return f"Error: Unknown task {task_id}"
            return f"[{t['status']}] {t['command'][:80]}\n{t.get('result') or '(running)'}"
        lines = [
            f"[{tid}] [{t['status']}]: {t['command'][:80]}"
            for tid, t in self.tasks.items()
        ]
        return "\n".join(lines) if lines else "No background tasks"

    def drain_notifications(self) -> list:
        with self._lock:
            notifs = list(self._notification_queue)
            self._notification_queue.clear()
        return notifs


bg_manager = BackGroundManager()

run_bg_tool = Tool(
    name="run_background",
    description="Run a shell command in a background thread. Returns immediately with a task_id.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."}
        },
        "required": ["command"],
    },
    handler=lambda **kw: bg_manager.run(command=kw["command"]),
)

check_bg_tool = Tool(
    name="check_background",
    description="Check background task status. Omit task_id to list all.",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID to check."}
        },
    },
    handler=lambda **kw: bg_manager.check(task_id=kw.get("task_id")),
)
