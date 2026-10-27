import pytest

from rove.permissions import PermissionPolicy
from rove.tool_registry import Tool, ToolRegistry


def _registry(tmp_path, handler, approve=True):
    registry = ToolRegistry(PermissionPolicy(tmp_path), lambda *a, **k: approve)
    # read_file 在白名单里，不走审批；用它承载各种 handler 行为
    registry.register(Tool("read_file", "read", {"type": "object", "properties": {}}, handler=handler))
    return registry


def _raise(**kw):
    raise ValueError("boom")


@pytest.mark.parametrize("handler, is_error, kind", [
    (lambda **kw: "file content", False, None),
    # 旧逻辑按前缀猜：文件内容恰好以 Traceback / error 开头会被误判成工具失败
    (lambda **kw: "Traceback (most recent call last): 这是日志文件的内容", False, None),
    (lambda **kw: "error rate is 3%", False, None),
    (lambda **kw: "Error: Timeout expired(30s).", True, "tool_reported"),
    (_raise, True, "exception"),
])
def test_handler_outcomes(tmp_path, handler, is_error, kind):
    outcome = _registry(tmp_path, handler).run("read_file", {"path": "a.txt"})
    assert (outcome.is_error, outcome.error_kind) == (is_error, kind)


def test_registry_level_errors_are_flagged(tmp_path):
    registry = _registry(tmp_path, lambda **kw: "x")
    assert registry.run("nope", {}).error_kind == "unknown_tool"
    # 越出工作区：策略层 DENY，旧逻辑下 "Permission Denied" 不以 error 开头 → is_error=False
    denied = registry.run("read_file", {"path": "../../etc/passwd"})
    assert denied.is_error and denied.error_kind == "permission_denied"


def test_execute_keeps_returning_plain_text(tmp_path):
    # subagent / teammate 依赖 execute() 返回字符串
    assert _registry(tmp_path, lambda **kw: "hi").execute("read_file", {"path": "a.txt"}) == "hi"
