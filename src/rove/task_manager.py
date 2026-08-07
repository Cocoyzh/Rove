"""
Task Manager: 持久化存储Tasks, 保存为json格式
{
    "id": "",
    "subject": "",
    "description": "",
    "status": "",
    "blockedBy": "[]",
    "owner": "",
}
"""
import json
import threading

class TaskManager:
    def __init__(self, task_dir):
        self.dir = task_dir
        self.dir.mkdir(exist_ok=True)
        self._next_id = self._max_id() + 1
        self._claim_lock = threading.Lock()

    def _max_id(self) -> int:
        ids = []
        for f in self.dir.glob("task_*.json"):
            ids.append(int(f.stem.split("_")[1]))
        return max(ids) if ids else 0

    def _load(self, task_id: int) -> dict:
        path = self.dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(path.read_text())

    def _save(self, task: dict) -> None:
        path = self.dir / f"task_{task['id']}.json"
        path.write_text(json.dumps(task, indent=2, ensure_ascii=False))

    def get(self, task_id: int) -> str:
        return json.dumps(self._load(task_id), indent=2, ensure_ascii=False)

    def create(self, subject: str, description: str="") -> str:
        task = {
            "id": self._next_id, "subject": subject,
            "description": description, "status": "pending",
            "blockedBy": [], "owner": ""
        }
        self._next_id = self._next_id + 1
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def _clear_dependency(self, completed_id: int) -> None:
        for f in self.dir.glob("task_*.json"):
            task = json.loads(f.read_text())
            if completed_id in task["blockedBy"]:
                task["blockedBy"].remove(completed_id)
                self._save(task)


    def update(self, task_id: int, status: str = None, add_blocked_by: list = None,
               remove_blocked_by: list = None) -> str:
        task = self._load(task_id)
        if status:
            if status not in ["pending", "completed", "in_progress"]:
                raise ValueError(f"Invalid status {status}")
            task["status"] = status
            if status == "completed":
                self._clear_dependency(task_id)
        if add_blocked_by:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        if remove_blocked_by:
            task["blockedBy"] = [x for x in task["blockedBy"] if x not in remove_blocked_by]
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def list_all(self) -> str:
        tasks = []
        files = sorted(
            self.dir.glob("task_*.json"),
            key=lambda f: int(f.stem.split("_")[1])
        )
        for f in files:
            tasks.append(json.loads(f.read_text()))
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t["status"], "[?]")
            blocked = f" (blocked by: {t['blockedBy']})" if t.get("blockedBy") else ""
            lines.append(f"{marker} #{t['id']}: {t['subject']}{blocked}")
        return "\n".join(lines)

    def scan_unclaimed_tasks(self) -> list:
        self.dir.mkdir(exist_ok=True)
        unclaimed = []
        for f in self.dir.glob("task_*.json"):
            task = json.loads(f.read_text())
            if (task["status"] == "pending" and
                not task.get("blockedBy") and not task.get("owner")):
                unclaimed.append(task)
        return unclaimed

    def claim_task(self, task_id: int, owner: str):
        with self._claim_lock:
            path = self.dir / f"task_{task_id}.json"
            if not path.exists():
                return f"Error: Task {task_id} not found"
            task = json.loads(path.read_text())
            if task.get('status') != "pending":
                status = task.get("status")
                return f"Error: Task {task_id} cannot be claimed due to status {status}"
            if task.get("owner"):
                existing_owner = task.get("owner")
                return f"Error: Task {task_id} has already been claimed by {existing_owner}"
            if task.get("blockedBy"):
                return f"Error: Task {task_id} is blocked by other task(s) and cannot be claimed yet"
            task["owner"] = owner
            task["status"] = "in_progress"
            path.write_text(json.dumps(task, indent=2))
        return f"Claimed Task #{task_id} for {owner}."