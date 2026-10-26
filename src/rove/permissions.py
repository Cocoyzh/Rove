import json
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from rove.security.approval_store import (
    SessionApprovalStore,
    canonicalize_arguments,
)
from rove.security.command_classifier import (
    CommandClassifier,
    extract_command_prefix,
)


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ApprovalMode(str, Enum):
    """全局三档权限审批模式（Issue #5 Phase 4）。

    安全底线在任何模式下都成立：硬拒绝命令与跨工作区文件写入一律 DENY。
    """
    STRICT = "strict"            # 严格审查：只读白名单自动放行，其余一律审批
    WORKSPACE = "workspace"      # 工作区信任：工作区内写入/重定向自动放行，越界才审批
    FULL_ACCESS = "full_access"  # 完全访问：除安全底线外全自动（仅建议隔离环境使用）


VALID_APPROVAL_MODES = {mode.value: mode for mode in ApprovalMode}


class ApprovalManager:
    def __init__(self, store: SessionApprovalStore | None = None) -> None:
        self._store = store if store is not None else SessionApprovalStore()
        self._lock = threading.Lock()

    @property
    def store(self) -> SessionApprovalStore:
        return self._store

    def read_input(self, prompt: str) -> str:
        with self._lock:
            return input(prompt)

    def request(self, tool_name: str, arguments: dict[str, Any], reason: str) -> bool:
        if self._store.is_approved(tool_name, arguments):
            return True

        with self._lock:
            if self._store.is_approved(tool_name, arguments):
                return True

            arguments_text = json.dumps(arguments, ensure_ascii=False, default=str)
            if len(arguments_text) > 1000:
                arguments_text = arguments_text[:1000] + " ..."

            print(f"\nPermission required: {reason}")
            print(f"Tool: {tool_name}")
            print(f"Arguments: {arguments_text}")

            prompt_str = self._build_prompt(tool_name, arguments)

            try:
                choice = input(prompt_str).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False

            return self._handle_choice(choice, tool_name, arguments)

    @staticmethod
    def _build_prompt(tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name in {"bash", "run_background"}:
            cmd = arguments.get("command", "")
            prefix = extract_command_prefix(cmd)
            prefix_display = f"'{prefix} *'" if prefix else "command prefix"
            return f"Allow? [y] once / [s] exact / [c] prefix {prefix_display} / [N] deny: "

        if tool_name in {"write_file", "edit_file"}:
            path = arguments.get("path", "")
            path_display = f"'{path}'" if path else "path"
            return f"Allow? [y] once / [s] exact / [p] path {path_display} / [t] tool / [N] deny: "

        return "Allow? [y] once / [s] exact / [t] tool / [N] deny: "

    def _handle_choice(self, choice: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        if choice in {"y", "yes"}:
            return True

        if choice in {"s", "session", "exact"}:
            self._store.add_exact_rule(tool_name, arguments)
            return True

        if choice in {"c", "command", "prefix"} and tool_name in {"bash", "run_background"}:
            prefix = extract_command_prefix(arguments.get("command", ""))
            if prefix:
                self._store.add_prefix_rule(tool_name, prefix)
            else:
                self._store.add_exact_rule(tool_name, arguments)
            return True

        if choice in {"p", "path"} and tool_name in {"write_file", "edit_file"}:
            path = arguments.get("path", "")
            if path:
                self._store.add_path_rule(tool_name, path)
            else:
                self._store.add_exact_rule(tool_name, arguments)
            return True

        if choice in {"t", "tool"} and tool_name not in {"bash", "run_background"}:
            self._store.add_tool_rule(tool_name)
            return True

        return False

    @staticmethod
    def _approval_key(tool_name: str, arguments: dict[str, Any]) -> str:
        canonical_arguments = canonicalize_arguments(arguments)
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
        "memory_store",
        "memory_recall",
        "memory_update",
    }

    _ASK_TOOLS = {
        "write_file",
        "edit_file",
        "bash",
        "execute_python",
        "run_background",
        "spawn_teammate",
    }

    def __init__(self, workspace: Path, approval_mode: ApprovalMode = ApprovalMode.WORKSPACE):
        self._workspace = workspace.resolve()
        self._classifier = CommandClassifier(self._workspace)
        self.approval_mode = approval_mode

    def set_approval_mode(self, mode: ApprovalMode) -> None:
        self.approval_mode = mode

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

        boundary_reason = self._check_bash_boundary(tool_name, arguments)
        if boundary_reason:
            return PermissionDecision.DENY, boundary_reason

        if self.approval_mode is ApprovalMode.FULL_ACCESS:
            # 安全底线（硬拒绝 + 文件越界）已在上方无条件拦截
            return PermissionDecision.ALLOW, f"Full access mode: {tool_name} auto-approved"

        if tool_name in {"bash", "run_background"}:
            command = arguments.get("command", "")
            is_safe, reason = self._classifier.classify(command)
            if is_safe:
                return PermissionDecision.ALLOW, "Safe read-only command"
            if self.approval_mode is ApprovalMode.WORKSPACE and self._classifier.is_workspace_redirection(command):
                return PermissionDecision.ALLOW, "Workspace mode: in-workspace redirection auto-approved"
            return PermissionDecision.ASK, f"Bash command requires approval: {reason}"

        if self.approval_mode is ApprovalMode.WORKSPACE and tool_name in {"write_file", "edit_file"}:
            # 跨工作区写入已在 _check_workspace_path 无条件 DENY，走到这里的必然在工作区内
            return PermissionDecision.ALLOW, "Workspace mode: in-workspace file write auto-approved"

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

    def _check_bash_boundary(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        """bash/run_background 参数路径逃逸工作区即硬拒绝（Issue #9）。

        与只读白名单和权限模式无关，对所有 bash 命令生效：阻止 find /
        全盘遍历、ls .. 越界窥探以及任意工作区外路径访问。命令参数无法
        区分读写意图，故按最严格的边界处理。
        """
        if tool_name not in {"bash", "run_background"}:
            return None

        command = arguments.get("command", "")
        if not isinstance(command, str):
            return None

        escape_token = self._classifier.find_workspace_escape(command)
        if escape_token:
            return f"Path escapes workspace: {escape_token}"
        return None
