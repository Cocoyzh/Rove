import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


def canonicalize_arguments(arguments: dict[str, Any]) -> str:
    """Returns a deterministic, sorted JSON representation of arguments."""
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


class ApprovalRule(ABC):
    """Base class for session approval rules."""

    @abstractmethod
    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        pass


class ToolWideRule(ApprovalRule):
    """Approves all calls to a specific tool for the entire session."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return tool_name == self.tool_name

    def __repr__(self) -> str:
        return f"ToolWideRule(tool={self.tool_name!r})"


class PathRule(ApprovalRule):
    """Approves write/edit operations to a specific relative file path."""

    def __init__(self, tool_name: str, path: str) -> None:
        self.tool_name = tool_name
        self.normalized_path = self._normalize(path)

    @staticmethod
    def _normalize(path_str: str) -> str:
        if not isinstance(path_str, str):
            return ""
        try:
            return Path(path_str).as_posix().lstrip("./")
        except ValueError:
            return path_str.strip()

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name != self.tool_name:
            return False
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str):
            return False
        return self._normalize(raw_path) == self.normalized_path

    def __repr__(self) -> str:
        return f"PathRule(tool={self.tool_name!r}, path={self.normalized_path!r})"


class CommandPrefixRule(ApprovalRule):
    """Approves bash or background commands starting with a specific prefix."""

    def __init__(self, tool_name: str, prefix: str) -> None:
        self.tool_name = tool_name
        self.prefix = prefix.strip()

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name != self.tool_name:
            return False
        raw_cmd = arguments.get("command")
        if not isinstance(raw_cmd, str):
            return False
        cmd = raw_cmd.strip()
        return cmd == self.prefix or cmd.startswith(self.prefix + " ") or cmd.startswith(self.prefix + "\t")

    def __repr__(self) -> str:
        return f"CommandPrefixRule(tool={self.tool_name!r}, prefix={self.prefix!r})"


class ExactMatchRule(ApprovalRule):
    """Approves a tool call only if the arguments match identically."""

    def __init__(self, tool_name: str, canonical_arguments: str) -> None:
        self.tool_name = tool_name
        self.canonical_arguments = canonical_arguments

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name != self.tool_name:
            return False
        return canonicalize_arguments(arguments) == self.canonical_arguments

    def __repr__(self) -> str:
        return f"ExactMatchRule(tool={self.tool_name!r})"


class SessionApprovalStore:
    """Thread-safe store of active session approval rules."""

    def __init__(self) -> None:
        self._rules: list[ApprovalRule] = []
        self._lock = threading.Lock()

    def is_approved(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        with self._lock:
            for rule in self._rules:
                if rule.matches(tool_name, arguments):
                    return True
        return False

    def add_rule(self, rule: ApprovalRule) -> None:
        with self._lock:
            self._rules.append(rule)

    def add_exact_rule(self, tool_name: str, arguments: dict[str, Any]) -> None:
        canonical = canonicalize_arguments(arguments)
        self.add_rule(ExactMatchRule(tool_name, canonical))

    def add_prefix_rule(self, tool_name: str, prefix: str) -> None:
        self.add_rule(CommandPrefixRule(tool_name, prefix))

    def add_path_rule(self, tool_name: str, path: str) -> None:
        self.add_rule(PathRule(tool_name, path))

    def add_tool_rule(self, tool_name: str) -> None:
        self.add_rule(ToolWideRule(tool_name))

    def clear(self) -> None:
        with self._lock:
            self._rules.clear()

    def list_rules(self) -> list[ApprovalRule]:
        with self._lock:
            return list(self._rules)
