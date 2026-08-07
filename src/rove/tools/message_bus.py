import json
import time
from pathlib import Path
import threading
from rove.paths import INBOX_DIR

VALID_MSG_TYPES = ["message", "broadcast"]

class MessageBus:
    def __init__(self, inbox_dir: Path):
        self.dir = inbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict | None = None) -> str:
        """Send a message to one agent. Return message_id"""
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(f"Error: Invalid type {msg_type}")

        msg = {
            "type": msg_type,
            "from": sender,
            "to": to,
            "content": content,
            "timestamp": int(time.time()),
        }
        if extra:
            msg.update(extra)

        inbox_path = self.dir / f"{to}.jsonl"
        line = json.dumps(msg, ensure_ascii=False)

        with self._lock:
            with open(inbox_path, "a", encoding="utf-8") as f:
                f.write(line + '\n')

        return f"Send {msg_type} to {to}"

    def read_inbox(self, name: str) -> list[dict]:
        """Read and consume all unread messages for one agent"""
        inbox_path = self.dir / f"{name}.jsonl"

        with self._lock:
            if not inbox_path.exists():
                return []

            lines = inbox_path.read_text(encoding="utf-8").splitlines()
            inbox_path.write_text("", encoding="utf-8")
        return [json.loads(line) for line in lines if line.strip()]

    def broadcast(self, sender: str, content: str, teammates: list) -> str:
        """Send one message to many agents"""
        cnt = 0
        for teammate in teammates:
            if teammate != sender:
                self.send(sender, teammate, content, msg_type="broadcast")
                cnt += 1

        return f"Broadcast {content} to {cnt} teammates"

BUS = MessageBus(inbox_dir=INBOX_DIR)
