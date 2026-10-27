from dataclasses import dataclass
from typing import Any, Dict, Callable, List, Optional
from rove.permissions import PermissionDecision, PermissionPolicy

@dataclass(frozen=True)
class ToolOutcome:
    """一次工具调用的结果。is_error 由产生错误的地方直接标记，而不是事后按文本前缀猜。

    error_kind: unknown_tool / no_handler / permission_denied / approval_denied / exception / tool_reported
    """
    content: str
    is_error: bool = False
    error_kind: Optional[str] = None


# 工具 handler 约定用 "Error: ..." 报告自身失败（超时、参数缺失等）
_TOOL_ERROR_PREFIX = "Error:"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Optional[Callable] = None

class ToolRegistry:
    def __init__(
        self,
        permission_policy: PermissionPolicy,
        approval_handler: Callable[[str, Dict[str, Any], str], bool],
    ) -> None:
        self._tools: Dict[str, Tool] = {}
        self._permission_policy = permission_policy
        self._approval_handler = approval_handler

    @property
    def permission_policy(self) -> PermissionPolicy:
        """暴露策略实例，供 CLI / Web 运行时切换权限审批模式。"""
        return self._permission_policy

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} already registered")

        self._tools[tool.name] = tool

    def register_many(self, tools: List[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """只要文本结果的调用方（subagent、teammate）用这个；需要 is_error 的用 run()。"""
        return self.run(name, arguments).content

    def run(self, name: str, arguments: Dict[str, Any]) -> ToolOutcome:
        tool = self._tools.get(name)

        if tool is None:
            return ToolOutcome(f"Unknown tool: {name}", True, "unknown_tool")
        if tool.handler is None:
            return ToolOutcome(f"Error: tool {name} has no handler registered.", True, "no_handler")

        decision, reason = self._permission_policy.decide(tool_name=name, arguments=arguments)

        if decision is PermissionDecision.DENY:
            return ToolOutcome(f"Permission Denied: {reason}", True, "permission_denied")

        if decision is PermissionDecision.ASK:
            approved = self._approval_handler(name, arguments, reason)
            if not approved:
                return ToolOutcome(f"Permission Denied: {name} requires approval and was not granted ({reason})",
                                   True, "approval_denied")

        try:
            result = tool.handler(**arguments)
        except Exception as e:
            return ToolOutcome(f"Error: {e}", True, "exception")
        if isinstance(result, ToolOutcome):
            # handler 自己知道成败（如子进程退出码），直接透传，不再从文本推断
            return result
        output = str(result)
        if output.startswith(_TOOL_ERROR_PREFIX):
            return ToolOutcome(output, True, "tool_reported")
        return ToolOutcome(output)
