import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading

from rove.messages import Message, ToolCall
from rove.paths import SESSIONS_DIR


def message_to_dict(msg: Message) -> Dict[str, Any]:
    d: Dict[str, Any] = {"role": msg.role}
    if msg.content is not None:
        d["content"] = msg.content
    if msg.tool_calls is not None:
        d["tool_calls"] = [tc.to_dict() for tc in msg.tool_calls]
    if msg.tool_call_id is not None:
        d["tool_call_id"] = msg.tool_call_id
    return d


def dict_to_message(d: Dict[str, Any]) -> Message:
    role = d["role"]
    content = d.get("content")
    tool_call_id = d.get("tool_call_id")
    tool_calls = None
    if "tool_calls" in d and d["tool_calls"] is not None:
        tool_calls = [
            ToolCall(
                tool_id=tc["tool_id"],
                tool_name=tc["tool_name"],
                tool_args=tc.get("tool_args", {}),
            )
            for tc in d["tool_calls"]
        ]
    return Message(
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )


class SessionManager:
    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self.dir = sessions_dir or SESSIONS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file_path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            sessions = []
            for file in self.dir.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    in_tok = data.get("total_input_tokens", 0)
                    out_tok = data.get("total_output_tokens", 0)
                    sessions.append({
                        "id": data.get("id", file.stem),
                        "title": data.get("title", "未命名会话"),
                        "created_at": data.get("created_at", 0),
                        "updated_at": data.get("updated_at", 0),
                        "message_count": len(data.get("messages", [])),
                        "total_input_tokens": in_tok,
                        "total_output_tokens": out_tok,
                        "last_context_tokens": data.get("last_context_tokens", 0),
                        "session_tokens": in_tok + out_tok,
                    })
                except Exception:
                    continue
            sessions.sort(key=lambda s: s["updated_at"], reverse=True)
            return sessions

    def get_project_total_tokens(self) -> int:
        """获取当前项目工作区下所有持久化会话累计消耗的 Token 总量。"""
        with self._lock:
            total = 0
            for file in self.dir.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    total += data.get("total_input_tokens", 0) + data.get("total_output_tokens", 0)
                except Exception:
                    continue
            return total

    def create_session(self, title: str = "新会话") -> Dict[str, Any]:
        session_id = f"s_{uuid.uuid4().hex[:12]}"
        now = time.time()
        session_data: Dict[str, Any] = {
            "id": session_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "last_context_tokens": 0,
        }
        path = self._file_path(session_id)
        with self._lock:
            path.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return session_data

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = self._file_path(session_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None

    def update_session_title(self, session_id: str, title: str) -> Optional[Dict[str, Any]]:
        path = self._file_path(session_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["title"] = title
                data["updated_at"] = time.time()
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return data
            except Exception:
                return None

    def delete_session(self, session_id: str) -> bool:
        path = self._file_path(session_id)
        with self._lock:
            if path.exists():
                try:
                    path.unlink()
                    return True
                except Exception:
                    return False
            return False

    def save_session_messages(
        self,
        session_id: str,
        messages: List[Message],
        auto_title: bool = True,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        last_context_tokens: Optional[int] = None,
    ) -> bool:
        path = self._file_path(session_id)
        with self._lock:
            if not path.exists():
                return False
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["messages"] = [message_to_dict(m) for m in messages]
                data["updated_at"] = time.time()
                if input_tokens is not None:
                    data["total_input_tokens"] = input_tokens
                if output_tokens is not None:
                    data["total_output_tokens"] = output_tokens
                if last_context_tokens is not None:
                    data["last_context_tokens"] = last_context_tokens

                # 如果还是初始标题且有用户消息，自动提取前20个字作为标题
                if auto_title and (data.get("title") in ("新会话", "未命名会话")):
                    for m in messages:
                        if m.role == "user" and m.content and not m.content.startswith(("<inbox>", "<background-results>", "<reminder>")):
                            summary = m.content.strip().splitlines()[0][:25]
                            if summary:
                                data["title"] = summary
                            break

                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return True
            except Exception:
                return False

    def load_session_messages(self, session_id: str) -> List[Message]:
        session_data = self.get_session(session_id)
        if not session_data or "messages" not in session_data:
            return []
        messages: List[Message] = []
        for d in session_data["messages"]:
            try:
                messages.append(dict_to_message(d))
            except Exception:
                continue
        return messages
