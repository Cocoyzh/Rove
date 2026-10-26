import json
from dataclasses import dataclass
from typing import Any, Dict, Callable, List, Optional
from rove.permissions import PermissionDecision, PermissionPolicy

@dataclass
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Optional[Callable] = None


# 已退役工具名 → 现役名映射（context-optimize P3）：模型沿用旧名时不再整轮报废，
# 命中别名即执行现役工具，并在结果中提示使用新名。
# task_create/task_update/task_get/task_list 为 #7 统一看板时下线的碎片工具。
TOOL_ALIASES = {
    "task_create": "todo",
    "task_update": "todo",
    "task_get": "todo",
    "task_list": "todo",
}

# JSON Schema 类型 → Python 校验谓词（bool 是 int 子类，需显式排除）
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}

_TYPE_EXAMPLES = {
    "string": "<字符串>",
    "number": 0,
    "integer": 0,
    "boolean": True,
    "array": [],
    "object": {},
    "null": None,
}


def _example_from_schema(schema: Dict[str, Any]) -> str:
    """从 schema 生成最小合法参数示例，回喂给模型自愈。"""
    if not isinstance(schema, dict):
        return "{}"
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    names = [n for n in required if n in props] or list(props)[:3]

    def example_for(prop: dict, depth: int = 0) -> Any:
        if not isinstance(prop, dict) or depth > 2:
            return "<...>"
        if "enum" in prop and prop["enum"]:
            return prop["enum"][0]
        if "examples" in prop and prop["examples"]:
            return prop["examples"][0]
        if "default" in prop:
            return prop["default"]
        t = prop.get("type", "string")
        if t == "array":
            return [example_for(prop.get("items", {}), depth + 1)] if prop.get("items") else []
        if t == "object":
            sub = prop.get("properties") or {}
            sub_req = prop.get("required") or []
            keys = [k for k in sub_req if k in sub] or list(sub)[:3]
            return {k: example_for(sub[k], depth + 1) for k in keys}
        return _TYPE_EXAMPLES.get(t, "<...>")

    example = {name: example_for(props[name]) for name in names}
    return json.dumps(example, ensure_ascii=False)


def validate_tool_arguments(schema: Dict[str, Any], arguments: Any) -> List[str]:
    """执行前按工具 schema 校验参数，返回问题清单（空清单表示通过）。

    只做轻量结构校验：必填字段、声明字段类型、数组元素类型、枚举；
    未声明的额外字段不拒绝（模型常补充注释性字段）。
    """
    if not isinstance(schema, dict) or not schema:
        return []
    if not isinstance(arguments, dict):
        return [f"arguments 必须是对象，实际 {type(arguments).__name__}"]

    problems: List[str] = []
    for name in schema.get("required") or []:
        if name not in arguments:
            problems.append(f"缺少必填字段 '{name}'")

    props = schema.get("properties") or {}
    for name, value in arguments.items():
        prop = props.get(name)
        if not isinstance(prop, dict):
            continue
        expected = prop.get("type")
        if expected in _TYPE_CHECKS and not _TYPE_CHECKS[expected](value):
            problems.append(
                f"字段 '{name}' 期望 {expected}，实际 {type(value).__name__}（{value!r}）")
        elif expected == "array" and isinstance(value, list):
            item_type = (prop.get("items") or {}).get("type")
            if item_type in _TYPE_CHECKS:
                for i, item in enumerate(value):
                    if not _TYPE_CHECKS[item_type](item):
                        problems.append(
                            f"字段 '{name}[{i}]' 期望 {item_type}，实际 {type(item).__name__}")
        if "enum" in prop and value not in prop["enum"]:
            problems.append(f"字段 '{name}' 必须是 {prop['enum']} 之一，实际 {value!r}")
    return problems


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

    def _resolve_alias(self, name: str) -> tuple[str, str]:
        """解析退役工具名；返回 (现役名, 提示语)。"""
        if name in self._tools or name not in TOOL_ALIASES:
            return name, ""
        resolved = TOOL_ALIASES[name]
        hint = (f"[提示] 工具 '{name}' 已退役，本次已按 '{resolved}' 执行；"
                f"后续请直接使用 '{resolved}'（参数 schema 以其工具定义为准）。\n")
        return resolved, hint

    def _validate_or_error(self, tool: Tool, arguments: Dict[str, Any]) -> Optional[str]:
        """执行前 schema 校验，失败时返回结构化错误（字段/期望/示例）。"""
        problems = validate_tool_arguments(tool.input_schema, arguments)
        if not problems:
            return None
        lines = [f"Error: 参数校验失败 tool={tool.name}（调用未执行，请修正后重试）"]
        lines.extend(f"- {p}" for p in problems)
        lines.append(f"最小参数示例: {_example_from_schema(tool.input_schema)}")
        return "\n".join(lines)

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        resolved, alias_hint = self._resolve_alias(name)
        tool = self._tools.get(resolved)

        if tool is None:
            return f"Unknown tool: {name}"
        if tool.handler is None:
            return f"Error: tool {name} has no handler registered."

        # P3：执行前 schema 校验——格式错误不再进 handler 异常路径整轮报废，
        # 结构化错误作为 tool_result 回喂，让模型自愈
        validation_error = self._validate_or_error(tool, arguments)
        if validation_error is not None:
            return alias_hint + validation_error

        decision, reason = self._permission_policy.decide(tool_name=resolved, arguments=arguments)

        if decision is PermissionDecision.DENY:
            return f"Permission Denied: {reason}"

        if decision is PermissionDecision.ASK:
            approved = self._approval_handler(resolved, arguments, reason)
            if not approved:
                return f"Permission Denied: User rejected {resolved}"

        try:
            result = str(tool.handler(**arguments))
        except Exception as e:
            return f"Error: {e}"
        return alias_hint + result if alias_hint else result
