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

    def register(self, task_id: str, command: str) -> None:
        """外部任务（如后台 subagent）先行登记为 running，使 check_background 可见。"""
        with self._lock:
            self.tasks[task_id] = {"status": "running", "command": command, "result": None}

    def set_progress(self, task_id: str, progress: str) -> None:
        """更新运行中任务的进度心跳；未登记的任务静默忽略（同步 subagent 无 bg 记录）。"""
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["progress"] = progress

    def notify(self, task_id: str, status: str, result: str, command: str = "") -> None:
        """外部任务完成/失败时向通知管道投递结果，lead 下一步 drain_notifications 读到。"""
        with self._lock:
            task = self.tasks.setdefault(task_id, {"status": status, "command": command, "result": None})
            task["status"] = status
            task["result"] = result
            self._notification_queue.append({
                "task_id": task_id,
                "status": status,
                "result": result,
                "command": command[:80],
            })

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
            head = f"[{t['status']}] {t['command'][:80]}"
            if t.get("progress"):
                head += f"\nprogress: {t['progress']}"
            return f"{head}\n{t.get('result') or '(running)'}"
        lines = []
        for tid, t in self.tasks.items():
            line = f"[{tid}] [{t['status']}]: {t['command'][:80]}"
            if t.get("progress") and t["status"] == "running":
                line += f" · {t['progress']}"
            lines.append(line)
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
