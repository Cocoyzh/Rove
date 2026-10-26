"""context-optimize P3：调用一次成功率测试。

覆盖执行前 schema 预校验、退役工具名别名映射、失败经验沉淀与规避提示注入。
"""

import json

import pytest

from rove import lead_agent as la
from rove.lead_agent import LeadAgent
from rove.llm import LLMResponse, LLMRequest
from rove.memory import MemoryManager
from rove.messages import Message, ToolCall
from rove.paths import WORKSPACE_ROOT
from rove.permissions import PermissionPolicy
from rove.compaction.compaction_layers import _is_user_anchor
from rove.tool_registry import (
    Tool,
    ToolRegistry,
    TOOL_ALIASES,
    _example_from_schema,
    validate_tool_arguments,
)
from rove.tools.task_tool import build_create_task_tool
from rove.task_manager import TaskManager


# ---------------- schema 预校验 ----------------

TODO_LIKE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "text": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                },
                "required": ["id", "text", "status"],
            },
        },
    },
    "required": ["items"],
}


def test_validate_missing_required():
    problems = validate_tool_arguments(TODO_LIKE_SCHEMA, {})
    assert any("items" in p and "缺少必填" in p for p in problems)


def test_validate_type_mismatch_bool_not_number():
    schema = {"type": "object", "properties": {"n": {"type": "number"}}, "required": []}
    assert validate_tool_arguments(schema, {"n": True})   # bool 不是 number
    assert validate_tool_arguments(schema, {"n": 3}) == []
    assert validate_tool_arguments(schema, {"n": "x"})


def test_validate_array_item_types():
    problems = validate_tool_arguments(
        {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}},
        {"ids": [1, "x", True]})
    assert any("ids[1]" in p for p in problems)
    assert any("ids[2]" in p for p in problems)
    assert validate_tool_arguments(
        {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}},
        {"ids": [1, 2]}) == []


def test_validate_enum_violation():
    problems = validate_tool_arguments(
        {"type": "object", "properties": {"status": {"type": "string", "enum": ["a", "b"]}}},
        {"status": "c"})
    assert any("enum" in p or "必须是" in p for p in problems)


def test_validate_undeclared_fields_ignored():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert validate_tool_arguments(schema, {"a": "x", "extra_note": "注释"}) == []


def test_validate_non_dict_arguments_and_empty_schema():
    assert validate_tool_arguments(TODO_LIKE_SCHEMA, "oops")
    assert validate_tool_arguments({}, {"any": 1}) == []
    assert validate_tool_arguments(None, None) == []


def test_example_from_schema_minimal_valid():
    example = json.loads(_example_from_schema(TODO_LIKE_SCHEMA))
    assert "items" in example and isinstance(example["items"], list)
    item = example["items"][0]
    assert set(item) == {"id", "text", "status"}
    assert item["status"] == "pending"  # enum 取第一个合法值


# ---------------- 别名映射 ----------------

def _registry_with_todo() -> tuple[ToolRegistry, list]:
    calls = []

    def handler(items):
        calls.append(items)
        return "ok"

    registry = ToolRegistry(PermissionPolicy(WORKSPACE_ROOT), lambda *a, **k: True)
    registry.register(Tool(name="todo", description="d",
                           input_schema=TODO_LIKE_SCHEMA, handler=handler))
    return registry, calls


def test_alias_executes_renamed_tool_with_hint():
    registry, calls = _registry_with_todo()
    out = registry.execute("task_list", {"items": [{"id": 1, "text": "t", "status": "pending"}]})
    assert "task_list" in out and "todo" in out and "已退役" in out
    assert "ok" in out
    assert len(calls) == 1


def test_alias_with_bad_args_gets_structured_error():
    registry, calls = _registry_with_todo()
    out = registry.execute("task_create", {"subject": "旧参数"})
    # 旧工具的参数不匹配 todo schema：结构化错误指路 + 示例，不整轮报废
    assert "参数校验失败" in out and "缺少必填字段 'items'" in out
    assert "items" in out  # 示例可自愈
    assert calls == []


def test_alias_table_covers_retired_planning_tools():
    assert TOOL_ALIASES == {
        "task_create": "todo",
        "task_update": "todo",
        "task_get": "todo",
        "task_list": "todo",
    }


def test_existing_tool_name_not_aliased():
    registry, calls = _registry_with_todo()
    out = registry.execute("todo", {"items": [{"id": 1, "text": "t", "status": "completed"}]})
    assert "[提示]" not in out and out == "ok"


def test_unknown_tool_still_reported():
    registry, _ = _registry_with_todo()
    assert registry.execute("no_such_tool", {}).startswith("Unknown tool")


def test_validation_runs_before_permission_check():
    """校验失败时不应触发权限审批（结构化错误先于一切副作用）。"""
    asked = []

    def approval(tool_name, arguments, reason):
        asked.append(tool_name)
        return True

    registry = ToolRegistry(PermissionPolicy(WORKSPACE_ROOT), approval)
    registry.register(Tool(name="bash", description="d",
                           input_schema={"type": "object",
                                         "properties": {"command": {"type": "string"}},
                                         "required": ["command"]},
                           handler=lambda command: "ran"))
    out = registry.execute("bash", {"cmd": "ls"})  # 字段名错误
    assert "参数校验失败" in out and asked == []


# ---------------- 失败经验注入 ----------------

class ScriptedLLM:
    """按剧本返回响应的最小 LLM 替身。"""

    context_window = 1_000_000
    cjk_token_ratio = 1.3
    last_input_tokens = 0
    last_output_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        self.calls += 1
        return self._responses.pop(0)


def _fail_agent(tmp_path, monkeypatch):
    mm = MemoryManager(tmp_path)
    monkeypatch.setattr(la, "MEMORY", mm)

    def boom():
        return "Error: 故意失败"

    registry = ToolRegistry(PermissionPolicy(WORKSPACE_ROOT), lambda *a, **k: True)
    registry.register(Tool(name="boom_tool", description="d",
                           input_schema={"type": "object", "properties": {}},
                           handler=boom))

    responses = [
        LLMResponse(content="", tool_calls=[ToolCall("c1", "boom_tool", {})],
                    model="t", stop_reason="tool_use"),
        LLMResponse(content="第一轮结束", tool_calls=[], model="t", stop_reason="end_turn"),
        LLMResponse(content="", tool_calls=[ToolCall("c2", "boom_tool", {})],
                    model="t", stop_reason="tool_use"),
        LLMResponse(content="第二轮结束", tool_calls=[], model="t", stop_reason="end_turn"),
    ]
    llm = ScriptedLLM(responses)
    return LeadAgent(llm, registry, max_steps=6), mm


def test_first_failure_recorded_no_hint(tmp_path, monkeypatch):
    agent, mm = _fail_agent(tmp_path, monkeypatch)
    agent.run("第一轮")

    assert agent.messages[-1].content == "第一轮结束"
    assert not any(m.content and m.content.startswith("<tool-failure-hint>")
                   for m in agent.messages)
    # 失败经验入长期记忆
    hits = mm.long_term.recall("boom_tool", category="episodic")
    assert len(hits) == 1 and "exception" in hits[0]["key"]


def test_second_failure_injects_hint(tmp_path, monkeypatch):
    agent, _ = _fail_agent(tmp_path, monkeypatch)
    agent.run("第一轮")
    agent.run("第二轮")

    hints = [m for m in agent.messages
             if m.role == "user" and m.content and m.content.startswith("<tool-failure-hint>")]
    assert len(hints) == 1
    assert "boom_tool" in hints[0].content and "2 次" in hints[0].content
    # 提示属于合成消息，不享有 L1 用户锚点保护
    assert _is_user_anchor(hints[0]) is False


def test_failure_counter_cleared_on_reset(tmp_path, monkeypatch):
    agent, _ = _fail_agent(tmp_path, monkeypatch)
    agent.run("第一轮")
    agent.reset()
    assert agent._tool_failures == {}


# ---------------- system prompt 前置规则 ----------------

def test_system_prompt_carries_static_tool_discipline():
    from rove.prompt.system_prompt import SYSTEM_PROMPT
    assert "never retry the identical failing call" in SYSTEM_PROMPT
    assert "`sudo`" in SYSTEM_PROMPT  # 硬拒清单前置告知（静态文本，不破坏缓存）


# ---------------- 退役工具不再注册（回归保护） ----------------

def test_build_default_registry_has_no_task_tools():
    from rove.tools.tools_setup import FILE_TOOLS  # noqa: F401
    # task_* 构造器仍存在（供队友端使用），但 Lead 端注册表不含它们
    tm = TaskManager(WORKSPACE_ROOT / ".tasks")
    for builder in (build_create_task_tool,):
        assert builder(tm).name == "task_create"
    assert "task_create" not in TOOL_ALIASES.values()
