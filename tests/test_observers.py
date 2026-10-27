import io
import json

from rich.console import Console

from rove.llm import LLMResponse, LLMRequest, Usage
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import ToolCall
from rove.observers import JsonlObserver, RichObserver, PREVIEW_HEAD, PREVIEW_TAIL
from rove.permissions import PermissionPolicy
from rove.tool_registry import Tool, ToolRegistry
from rove.lead_agent import LeadAgent


class ScriptedLLM(BaseLLMAdapter):
    """按顺序返回预设响应；stream 会把 content 拆成两段回调 on_text，模拟流式。"""

    def __init__(self, responses):
        super().__init__(model="mock", api_key="mock", base_url=None, timeout=None)
        self.responses = iter(responses)

    def _create_client(self):
        return None

    def complete(self, request: LLMRequest) -> LLMResponse:
        return next(self.responses)

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        response = self.complete(request)
        if on_text and response.content:
            half = len(response.content) // 2
            on_text(response.content[:half])
            on_text(response.content[half:])
        return response


def _run_agent(tmp_path, monkeypatch, observer, tool_output="work done"):
    from rove.tools.background import BackGroundManager
    from rove.tools.message_bus import MessageBus
    monkeypatch.setattr("rove.lead_agent.BUS", MessageBus(tmp_path / "inbox"))
    monkeypatch.setattr("rove.lead_agent.bg_manager", BackGroundManager())

    registry = ToolRegistry(PermissionPolicy(tmp_path), lambda *a, **k: True)
    registry.register(Tool(
        name="do_work",
        description="do something",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kw: tool_output,
    ))
    llm = ScriptedLLM([
        LLMResponse(content="先干活", tool_calls=[ToolCall("c_1", "do_work", {})],
                    model="mock", stop_reason="tool_use", usage=Usage(10, 5)),
        LLMResponse(content="All done", tool_calls=[], model="mock",
                    stop_reason="end_turn", usage=Usage(20, 7)),
    ])
    return LeadAgent(llm, registry, max_steps=5, observer=observer).run("go")


def test_jsonl_every_line_is_json_and_call_ids_pair(tmp_path, monkeypatch):
    buf = io.StringIO()
    result = _run_agent(tmp_path, monkeypatch, JsonlObserver(buf))
    assert result.content == "All done"

    events = [json.loads(line) for line in buf.getvalue().splitlines()]
    types = [e["type"] for e in events]
    assert types == ["assistant_text", "tool_call", "tool_result", "assistant_text", "usage"]
    assert all(e["v"] == 1 and isinstance(e["ts"], float) for e in events)

    call, res = events[1], events[2]
    assert call["call_id"] == res["call_id"] == "c_1"
    assert res["is_error"] is False and res["preview"] == "work done"
    # 流式 delta 不单独成行，一轮只发一条完整 assistant_text
    assert events[0]["text"] == "先干活"
    assert events[-1] == {**events[-1], "input_tokens": 30, "output_tokens": 12}


def test_jsonl_large_output_head_tail_preview(tmp_path, monkeypatch):
    big = "头" * 3000 + "尾" * 3000   # 多字节字符：确认按字符切不会产生非法 UTF-8
    buf = io.StringIO()
    _run_agent(tmp_path, monkeypatch, JsonlObserver(buf), tool_output=big)

    res = next(json.loads(l) for l in buf.getvalue().splitlines() if '"tool_result"' in l)
    assert res["truncated"] is True
    assert res["original_bytes"] == len(big.encode("utf-8"))
    assert res["preview"].startswith("头" * PREVIEW_HEAD)
    assert res["preview"].endswith("尾" * PREVIEW_TAIL)


def test_rich_observer_keeps_repl_output(tmp_path, monkeypatch):
    console = Console(file=io.StringIO(), force_terminal=False, width=200)
    _run_agent(tmp_path, monkeypatch, RichObserver(console))
    out = console.file.getvalue()
    assert "先干活\n" in out
    assert "▸ do_work {}" in out
    assert "output: work done" in out
    assert "⚡ In: 30 · Out: 12" in out
