import json
import threading
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ApprovalManager:
    def __init__(self):
        self._session_approvals: set[str] = set()
        self._lock = threading.Lock()

    def read_input(self, prompt: str) -> str:
        with self._lock:
            return input(prompt)

    def request(self, tool_name: str, arguments: dict[str, Any], reason: str) -> bool:
        approval_key = self._approval_key(tool_name, arguments)

        with self._lock:
            if approval_key in self._session_approvals:
                return True

            arguments_text = json.dumps(arguments, ensure_ascii=False, default=str)
            if len(arguments_text) > 1000:
                arguments_text = arguments_text[:1000] + " ..."

            print(f"\nPermission required: {reason}")
            print(f"Tool: {tool_name}")
            print(f"Arguments: {arguments_text}")

            try:
                choice = input(
                    "Allow? [y] once / [s] same action for session / [N] deny: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False

            if choice in {"s", "session"}:
                self._session_approvals.add(approval_key)
                return True

            return choice in {"y", "yes"}

    @staticmethod
    def _approval_key(tool_name: str, arguments: dict[str, Any]) -> str:
        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return f"{tool_name}:{canonical_arguments}"


APPROVAL_MANAGER = ApprovalManager()


class PermissionPolicy:
    _HARD_DENY_COMMANDS = (
        "rm -rf /",
        "sudo",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        "> /dev/sda",
    )

    _ALLOWED_TOOLS = {
        "read_file",
        "load_skill",
        "todo",
        "task_create",
        "task_update",
        "task_get",
        "task_list",
        "scan_tasks",
        "claim_task",
        "send_message",
        "list_teammates",
        "read_inbox",
        "protocol_request",
        "protocol_response",
        "check_background",
        "idle",
    }

    _ASK_TOOLS = {
        "write_file",
        "edit_file",
        "bash",
        "execute_python",
        "run_background",
        "spawn_teammate",
    }

    def __init__(self, workspace: Path):
        self._workspace = workspace.resolve()

    def decide(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[PermissionDecision, str]:
        hard_deny_reason = self._check_hard_deny(tool_name, arguments)
        if hard_deny_reason:
            return PermissionDecision.DENY, hard_deny_reason

        path_reason = self._check_workspace_path(tool_name, arguments)
        if path_reason:
            return PermissionDecision.DENY, path_reason

        if tool_name in self._ASK_TOOLS:
            return PermissionDecision.ASK, "This operation requires user approval"

        if tool_name in self._ALLOWED_TOOLS:
            return PermissionDecision.ALLOW, "Read-only tool"

        return PermissionDecision.ASK, "No matching permission rule"

    def _check_hard_deny(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        if tool_name not in {"bash", "run_background"}:
            return None

        command = arguments.get("command", "")
        if not isinstance(command, str):
            return "bash.command must be a string"

        command_lower = command.lower()
        for denied_command in self._HARD_DENY_COMMANDS:
            if denied_command in command_lower:
                return f"Blocked by hard deny rule: {denied_command!r}"

        return None

    def _check_workspace_path(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        if tool_name not in {"read_file", "write_file", "edit_file"}:
            return None

        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "path must be a non-empty string"

        target = (self._workspace / raw_path).resolve()
        if not target.is_relative_to(self._workspace):
            return f"Path escapes workspace: {raw_path}"

        return None
