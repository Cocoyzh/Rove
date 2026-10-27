import threading
import time
import uuid
import subprocess
from ..tool_registry import Tool
from rove.paths import WORKSPACE_ROOT


class BackGroundManager:
    def __init__(self):
        self.tasks = {}
        self._lock = threading.Lock()
        self._notification_queue = []
        self._procs: dict[str, subprocess.Popen] = {}   # 运行中的后台子进程，shutdown 时要清理

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
        # 用 Popen 而不是 run：要把进程句柄登记下来，进程退出前 shutdown() 才能找到并终止它。
        # 不另开进程组（不用 start_new_session），让它留在 Rove 的进程组里，
        # 平台对整个进程组发信号时能一并覆盖到（包括 shell 派生的孙进程）。
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=WORKSPACE_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with self._lock:
                self._procs[task_id] = proc
            try:
                out, err = proc.communicate(timeout=300)
                output = (out + err).strip()[:50000]
                if proc.returncode < 0:
                    status = "cancelled"
                else:
                    status = "completed"
                    if proc.returncode != 0:
                        output = f"{output}\n[exit code {proc.returncode}]".strip()
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                output = "Error: timeout(300s)"
                status = "timeout"
        except Exception as e:
            output = f"Error: {e}"
            status = "error"
        finally:
            with self._lock:
                self._procs.pop(task_id, None)

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

    def shutdown(self, grace: float = 3.0) -> int:
        """终止所有还在跑的后台子进程并等它们退出：先 SIGTERM，宽限期后 SIGKILL。

        返回被终止的进程数。headless 在发 run_finished 之前调用：清理完才能宣布结束。
        """
        with self._lock:
            running = [p for p in self._procs.values() if p.poll() is None]
        for proc in running:
            proc.terminate()
        deadline = time.monotonic() + grace
        for proc in running:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return len(running)

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
