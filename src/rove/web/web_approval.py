import json
import uuid
import threading
from typing import Any, Dict, Optional, Callable

from rove.security.approval_store import (
    SessionApprovalStore,
    canonicalize_arguments,
)
from rove.security.command_classifier import extract_command_prefix


class WebApprovalManager:
    """适用于 Web 环境的异步权限审批管理器。

    工作线程在请求审批时通过 threading.Event 挂起，
    前端通过 WebSocket 返回审批结果后唤醒工作线程。
    """

    def __init__(
        self,
        on_approval_needed: Optional[Callable[[Dict[str, Any]], None]] = None,
        store: Optional[SessionApprovalStore] = None,
    ) -> None:
        self._store = store if store is not None else SessionApprovalStore()
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.on_approval_needed = on_approval_needed

    @property
    def store(self) -> SessionApprovalStore:
        return self._store

    @staticmethod
    def _approval_key(tool_name: str, arguments: dict[str, Any]) -> str:
        return f"{tool_name}:{canonicalize_arguments(arguments)}"

    def request(self, tool_name: str, arguments: dict[str, Any], reason: str) -> bool:
        if self._store.is_approved(tool_name, arguments):
            return True

        with self._lock:
            if self._store.is_approved(tool_name, arguments):
                return True

            approval_id = f"appr_{uuid.uuid4().hex[:8]}"
            event = threading.Event()

            suggested_prefix = ""
            target_path = ""
            available_choices = ["y", "s", "n"]

            if tool_name in {"bash", "run_background"}:
                suggested_prefix = extract_command_prefix(arguments.get("command", ""))
                available_choices = ["y", "s", "c", "n"]
            elif tool_name in {"write_file", "edit_file"}:
                target_path = arguments.get("path", "")
                available_choices = ["y", "s", "p", "t", "n"]
            else:
                available_choices = ["y", "s", "t", "n"]

            self._pending_requests[approval_id] = {
                "event": event,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
                "suggested_prefix": suggested_prefix,
                "target_path": target_path,
                "decision": None,
            }

        # 触发通知回调（如推送 WebSocket）
        if self.on_approval_needed:
            self.on_approval_needed({
                "approval_id": approval_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
                "suggested_prefix": suggested_prefix,
                "target_path": target_path,
                "available_choices": available_choices,
            })

        # 等待前端响应，默认最多等待 300 秒防止死锁
        res = event.wait(timeout=300)
        if not res:
            with self._lock:
                self._pending_requests.pop(approval_id, None)
            return False

        with self._lock:
            info = self._pending_requests.pop(approval_id, None)
            if not info:
                return False
            decision = info.get("decision")
            tool_name = info["tool_name"]
            arguments = info["arguments"]
            suggested_prefix = info.get("suggested_prefix", "")
            target_path = info.get("target_path", "")

            if decision in {"y", "yes"}:
                return True
            if decision in {"s", "session", "exact"}:
                self._store.add_exact_rule(tool_name, arguments)
                return True
            if decision in {"c", "command", "prefix"} and tool_name in {"bash", "run_background"}:
                if suggested_prefix:
                    self._store.add_prefix_rule(tool_name, suggested_prefix)
                else:
                    self._store.add_exact_rule(tool_name, arguments)
                return True
            if decision in {"p", "path"} and tool_name in {"write_file", "edit_file"}:
                if target_path:
                    self._store.add_path_rule(tool_name, target_path)
                else:
                    self._store.add_exact_rule(tool_name, arguments)
                return True
            if decision in {"t", "tool"} and tool_name not in {"bash", "run_background"}:
                self._store.add_tool_rule(tool_name)
                return True

            return False

    def resolve(self, approval_id: str, decision: str) -> bool:
        with self._lock:
            req = self._pending_requests.get(approval_id)
            if not req:
                return False
            req["decision"] = decision.strip().lower()
            req["event"].set()
            return True
