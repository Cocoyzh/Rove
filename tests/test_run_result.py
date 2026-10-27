import pytest

from rove.lead_agent import LeadAgent
from rove.llm import LLMResponse
from rove.messages import ToolCall
from rove.observers import RunObserver
from rove.permissions import PermissionPolicy
from rove.tool_registry import Tool, ToolRegistry
from test_observers import ScriptedLLM


@pytest.fixture
def make_agent(tmp_path, monkeypatch):
    from rove.tools.background import BackGroundManager
    from rove.tools.message_bus import MessageBus
    monkeypatch.setattr("rove.lead_agent.BUS", MessageBus(tmp_path / "inbox"))
    monkeypatch.setattr("rove.lead_agent.bg_manager", BackGroundManager())

    def _make(responses, max_steps=5):
        registry = ToolRegistry(PermissionPolicy(tmp_path), lambda *a, **k: True)
        registry.register(Tool(
            name="do_work",
            description="do something",
            input_schema={"type": "object", "properties": {}},
            handler=lambda **kw: "work done",
        ))
        return LeadAgent(ScriptedLLM(responses), registry, max_steps=max_steps, observer=RunObserver())
    return _make


def _final(stop_reason, content="partial answer"):
    return LLMResponse(content=content, tool_calls=[], model="mock", stop_reason=stop_reason)


@pytest.mark.parametrize("stop_reason, reason", [
    ("end_turn", "completed"),
    ("max_tokens", "max_tokens"),           # 改之前：被截断的回复会被当成成功返回
    ("refusal", "refusal"),
    ("pause_turn", "unexpected_stop"),      # 不认识的一律算失败（fail closed）
    ("some_future_reason", "unexpected_stop"),
])
def test_stop_reason_maps_to_run_reason(make_agent, stop_reason, reason):
    result = make_agent([_final(stop_reason)]).run("go")
    assert result.reason == reason
    assert result.content == "partial answer"
    assert result.ok is (reason == "completed")
    if reason != "completed":
        assert stop_reason in result.detail


def test_max_steps_is_not_success(make_agent):
    looping = [
        LLMResponse(content="", tool_calls=[ToolCall(f"c_{i}", "do_work", {})],
                    model="mock", stop_reason="tool_use")
        for i in range(3)
    ]
    result = make_agent(looping, max_steps=3).run("go")
    assert result.reason == "max_steps"
    assert not result.ok
    assert "max_steps=3" in result.detail


def test_unexpected_stop_warns_on_stderr(make_agent, capsys):
    make_agent([_final("pause_turn")]).run("go")
    captured = capsys.readouterr()
    assert "pause_turn" in captured.err
    assert "pause_turn" not in captured.out   # stdout 要留给 JSONL 事件
