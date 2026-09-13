from dataclasses import dataclass
from typing import Any, Dict, Callable, List, Optional
from rove.permissions import PermissionDecision, PermissionPolicy

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
        # 按名称排序导出，保证请求前缀中工具定义逐字节稳定（前缀缓存友好）
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        tool = self._tools.get(name)

        if tool is None:
            return f"Unknown tool: {name}"
        if tool.handler is None:
            return f"Error: tool {name} has no handler registered."

        decision, reason = self._permission_policy.decide(tool_name=name, arguments=arguments)

        if decision is PermissionDecision.DENY:
            return f"Permission Denied: {reason}"

        if decision is PermissionDecision.ASK:
            approved = self._approval_handler(name, arguments, reason)
            if not approved:
                return f"Permission Denied: User rejected {name}"

        try:
            return str(tool.handler(**arguments))
        except Exception as e:
            return f"Error: {e}"
