import json
import uuid
import threading
from typing import Any, Dict, Optional, Callable


class WebApprovalManager:
    """适用于 Web 环境的异步权限审批管理器。

    工作线程在请求审批时通过 threading.Event 挂起，
    前端通过 WebSocket 返回审批结果后唤醒工作线程。
    """

    def __init__(self, on_approval_needed: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._session_approvals: set[str] = set()
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.on_approval_needed = on_approval_needed

    @staticmethod
    def _approval_key(tool_name: str, arguments: dict[str, Any]) -> str:
        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return f"{tool_name}:{canonical_arguments}"

    def request(self, tool_name: str, arguments: dict[str, Any], reason: str) -> bool:
        approval_key = self._approval_key(tool_name, arguments)

        with self._lock:
            if approval_key in self._session_approvals:
                return True

            approval_id = f"appr_{uuid.uuid4().hex[:8]}"
            event = threading.Event()
            self._pending_requests[approval_id] = {
                "event": event,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
                "key": approval_key,
                "decision": None,
            }

        # 触发通知回调（如推送 WebSocket）
        if self.on_approval_needed:
            self.on_approval_needed({
                "approval_id": approval_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
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
            if decision in {"s", "session"}:
                self._session_approvals.add(approval_key)
                return True
            return decision in {"y", "yes"}

    def resolve(self, approval_id: str, decision: str) -> bool:
        with self._lock:
            req = self._pending_requests.get(approval_id)
            if not req:
                return False
            req["decision"] = decision.strip().lower()
            req["event"].set()
            return True
