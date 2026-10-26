"""CallTrace 调用追踪：事件模型、落盘、埋点与回放端点单测。"""

import json
from pathlib import Path

import pytest
from rove.call_trace import (
    CallTracer,
    load_block_full,
    load_trace_events,
    new_session_id,
)
from rove.llm import LLMResponse, LLMRequest, Usage
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import Message, ToolCall
from rove.tool_registry import ToolRegistry, Tool
from rove.permissions import PermissionPolicy
from rove.lead_agent import LeadAgent
from rove.tools.todo import build_todo_tool
from rove.task_manager import TaskManager


class MockLLM(BaseLLMAdapter):
    def __init__(self, responses):
        super().__init__(model="mock-model", api_key="", base_url=None, timeout=None, context_window=100000)
        self.responses = list(responses)
        self.calls: list[LLMRequest] = []

    def _create_client(self):
        return None

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return self.responses.pop(0)

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        return self.complete(request)


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ==================== 事件模型与落盘 ====================


def test_tracer_llm_call_lifecycle(tmp_path):
    events_ws = []
    tracer = CallTracer("s_trace1", trace_dir=tmp_path, on_event=events_ws.append)
    agent = tracer.for_agent("lead")

    seq = agent.llm_start(query="帮我读取文件")
    assert seq == 1

    response = LLMResponse(
        content="好的",
        tool_calls=[ToolCall(tool_id="t1", tool_name="read_file", tool_args={"path": "a.txt"})],
        model="mock-model",
        stop_reason="tool_use",
        latency_ms=1200,
        # Usage.input_tokens 为未命中部分：20 未命中 + 80 命中 = 全部输入 100
        usage=Usage(input_tokens=20, output_tokens=30, cache_read_tokens=80),
        thinking="先读取文件内容",
    )
    agent.llm_end(seq, response)

    # 文件只落终态事件，且包含 full 全文
    path = tmp_path / "s_trace1.jsonl"
    persisted = _read_jsonl(path)
    assert len(persisted) == 1
    ev = persisted[0]
    assert ev["kind"] == "llm_call"
    assert ev["status"] == "tool_use"
    assert ev["agent_name"] == "lead"
    assert ev["session_id"] == "s_trace1"
    assert ev["model"] == "mock-model"
    assert ev["input_tokens"] == 100
    assert ev["output_tokens"] == 30
    assert ev["cache_read_tokens"] == 80
    assert ev["latency_ms"] == 1200
    types = [b["type"] for b in ev["blocks"]]
    assert types == ["user_message", "thinking", "text", "tool_use"]
    assert ev["blocks"][0]["full"] == "帮我读取文件"
    assert ev["blocks"][3]["tool_name"] == "read_file"
    assert ev["blocks"][3]["tool_id"] == "t1"
    # full_ref 形如 session:seq:index
    assert ev["blocks"][3]["full_ref"] == "s_trace1:1:3"

    # WS 预览形态：无 full 字段，其余字段一致
    assert len(events_ws) == 2  # running + 终态
    running, final = events_ws
    assert running["status"] == "running"
    assert running["blocks"][0]["type"] == "user_message"
    assert "full" not in final["blocks"][0]
    assert final["status"] == "tool_use"
    assert final["input_tokens"] == 100


def test_tracer_tool_exec_and_pending_input_chain(tmp_path):
    tracer = CallTracer("s_trace2", trace_dir=tmp_path)
    agent = tracer.for_agent("lead")

    # 第一次 LLM 调用（发出工具调用）
    seq1 = agent.llm_start(query="执行任务")
    agent.llm_end(seq1, LLMResponse(
        content="", tool_calls=[ToolCall(tool_id="t9", tool_name="bash", tool_args={"command": "ls"})],
        model="m", stop_reason="tool_use", latency_ms=10,
        usage=Usage(input_tokens=10, output_tokens=5),
    ))

    # 工具执行
    tseq = agent.tool_start("t9", "bash", {"command": "ls"})
    assert tseq == 2
    long_output = "x" * 500
    agent.tool_end(tseq, "t9", "bash", long_output, is_error=False, cost_ms=321)

    # 第二次 LLM 调用应把工具结果作为输入块
    seq2 = agent.llm_start()
    agent.llm_end(seq2, LLMResponse(
        content="完成", tool_calls=[], model="m", stop_reason="end_turn", latency_ms=5,
        usage=Usage(input_tokens=20, output_tokens=2),
    ))

    persisted = _read_jsonl(tmp_path / "s_trace2.jsonl")
    assert [e["kind"] for e in persisted] == ["llm_call", "tool_exec", "llm_call"]
    assert [e["status"] for e in persisted] == ["tool_use", "ok", "ok"]
    assert [e["seq"] for e in persisted] == [1, 2, 3]

    tool_ev = persisted[1]
    assert tool_ev["latency_ms"] == 321
    assert [b["type"] for b in tool_ev["blocks"]] == ["tool_use", "tool_result"]
    assert tool_ev["blocks"][1]["full"] == long_output
    assert len(tool_ev["blocks"][1]["preview"]) <= 202  # 200 + 空格 + 省略号
    assert tool_ev["blocks"][1]["preview"].endswith("…")

    second = persisted[2]
    input_types = [b["type"] for b in second["blocks"]]
    assert input_types == ["tool_result", "text"]
    # 输入块无 full，但保留指向原 tool_exec 事件块的 full_ref
    assert "full" not in second["blocks"][0]
    assert second["blocks"][0]["full_ref"] == tool_ev["blocks"][1]["full_ref"]


def test_tracer_error_and_multi_agent(tmp_path):
    events_ws = []
    tracer = CallTracer("s_trace3", trace_dir=tmp_path, on_event=events_ws.append)
    lead = tracer.for_agent("lead")
    mate = tracer.for_agent("alice")

    seq = lead.llm_start(query="hi")
    lead.llm_error(seq, "boom: context overflow")
    mseq = mate.llm_start(query="teammate task")
    mate.llm_end(mseq, LLMResponse(
        content="done", tool_calls=[], model="m", stop_reason="end_turn", latency_ms=1,
        usage=Usage(input_tokens=1, output_tokens=1),
    ))

    persisted = _read_jsonl(tmp_path / "s_trace3.jsonl")
    assert persisted[0]["status"] == "error"
    assert "overflow" in persisted[0]["error"]
    assert persisted[1]["agent_name"] == "alice"
    # seq 会话内全局递增
    assert [e["seq"] for e in persisted] == [1, 2]
    assert new_session_id().startswith("repl-")


def test_load_trace_events_and_block_full(tmp_path):
    tracer = CallTracer("s_trace4", trace_dir=tmp_path)
    agent = tracer.for_agent("lead")
    seq = agent.llm_start(query="问个问题")
    agent.llm_end(seq, LLMResponse(
        content="答" * 300, tool_calls=[], model="m", stop_reason="end_turn", latency_ms=1,
        usage=Usage(input_tokens=1, output_tokens=1),
    ))

    events = load_trace_events("s_trace4", trace_dir=tmp_path)
    assert len(events) == 1
    assert "full" not in events[0]["blocks"][1]
    assert events[0]["blocks"][1]["preview"].endswith("…")

    full = load_block_full("s_trace4", seq, 1, trace_dir=tmp_path)
    assert full == "答" * 300
    assert load_block_full("s_trace4", 99, 0, trace_dir=tmp_path) is None
    assert load_block_full("s_trace4", seq, 7, trace_dir=tmp_path) is None
    assert load_trace_events("missing-session", trace_dir=tmp_path) == []


# ==================== LeadAgent 埋点（REPL 运行时同样落盘） ====================


def test_lead_agent_emits_trace_events(tmp_path):
    registry = ToolRegistry(PermissionPolicy(tmp_path), lambda *a, **k: True)
    registry.register(Tool(
        name="do_work",
        description="do something",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kw: "work done",
    ))

    responses = [
        LLMResponse(
            content="开始干活",
            tool_calls=[ToolCall(tool_id="c1", tool_name="do_work", tool_args={})],
            model="mock-model",
            stop_reason="tool_use",
            latency_ms=100,
            usage=Usage(input_tokens=50, output_tokens=10),
        ),
        LLMResponse(
            content="All done",
            tool_calls=[],
            model="mock-model",
            stop_reason="end_turn",
            latency_ms=80,
            usage=Usage(input_tokens=60, output_tokens=5),
        ),
    ]
    llm = MockLLM(responses)
    tracer = CallTracer(new_session_id("repl"), trace_dir=tmp_path)
    lead = LeadAgent(llm, registry, max_steps=5, tracer=tracer.for_agent("lead"))

    result = lead.run("Start working")
    assert result == "All done"

    session_id = tracer.session_id
    persisted = _read_jsonl(tmp_path / f"{session_id}.jsonl")
    assert [e["kind"] for e in persisted] == ["llm_call", "tool_exec", "llm_call"]

    first_llm = persisted[0]
    assert first_llm["blocks"][0]["type"] == "user_message"
    assert first_llm["blocks"][0]["full"] == "Start working"
    assert first_llm["blocks"][-1]["type"] == "tool_use"
    assert first_llm["blocks"][-1]["tool_name"] == "do_work"

    assert persisted[1]["status"] == "ok"
    assert persisted[1]["blocks"][1]["full"] == "work done"

    final_llm = persisted[2]
    assert final_llm["status"] == "ok"
    assert final_llm["blocks"][0]["type"] == "tool_result"
    assert final_llm["blocks"][-1]["full"] == "All done"
    # 两次调用的 usage 都进了 trace
    assert first_llm["input_tokens"] == 50 and final_llm["output_tokens"] == 5


def test_teammate_tracer_view_via_team_manager(tmp_path):
    from rove.tools.agent_teams import TeammateManager

    tm = TeammateManager(tmp_path / "team", tracer=CallTracer("s_team", trace_dir=tmp_path))
    mate = tm.tracer.for_agent("bob")
    mseq = mate.llm_start(query="teammate prompt")
    mate.llm_end(mseq, LLMResponse(
        content="ok", tool_calls=[], model="m", stop_reason="end_turn", latency_ms=1,
        usage=Usage(input_tokens=3, output_tokens=1),
    ))
    persisted = _read_jsonl(tmp_path / "s_team.jsonl")
    assert persisted[0]["agent_name"] == "bob"


# ==================== Web 回放端点与 WS call_event 通道 ====================


def test_trace_api_endpoints(tmp_path, monkeypatch):
    import rove.web.app as web_app
    from starlette.testclient import TestClient
    from rove.paths import TRACE_DIR

    client = TestClient(web_app.app)
    res = client.post("/api/sessions", json={"title": "trace 回放测试"})
    sid = res.json()["id"]
    try:
        tracer = CallTracer(sid, trace_dir=TRACE_DIR)
        agent = tracer.for_agent("lead")
        seq = agent.llm_start(query="回放我")
        agent.llm_end(seq, LLMResponse(
            content="回" * 300, tool_calls=[], model="m", stop_reason="end_turn", latency_ms=1,
            usage=Usage(input_tokens=7, output_tokens=2),
        ))

        res_events = client.get(f"/api/traces/{sid}")
        assert res_events.status_code == 200
        events = res_events.json()["events"]
        assert len(events) == 1
        assert events[0]["seq"] == seq
        assert "full" not in events[0]["blocks"][1]

        res_block = client.get(f"/api/traces/{sid}/blocks/{seq}/1")
        assert res_block.status_code == 200
        assert res_block.json()["full"] == "回" * 300

        assert client.get(f"/api/traces/{sid}/blocks/{seq}/9").status_code == 404
        assert client.get("/api/traces/no-such-session").json()["events"] == []
    finally:
        trace_file = TRACE_DIR / f"{sid}.jsonl"
        if trace_file.exists():
            trace_file.unlink()
        client.delete(f"/api/sessions/{sid}")


def test_websocket_call_event_stream():
    import rove.web.app as web_app
    from starlette.testclient import TestClient
    from rove.paths import TRACE_DIR

    class _NoopSidePrompts:
        def generate_title(self, query, session_id=None):
            return None

    llm = MockLLM([
        LLMResponse(
            content="任务完成",
            tool_calls=[],
            model="mock-model",
            stop_reason="end_turn",
            latency_ms=15,
            usage=Usage(input_tokens=42, output_tokens=7),
        ),
    ])
    original_llm = web_app.app.state.llm
    original_side_prompts = web_app.app.state.side_prompts
    web_app.app.state.llm = llm
    web_app.app.state.side_prompts = _NoopSidePrompts()
    client = TestClient(web_app.app)
    res = client.post("/api/sessions", json={"title": "WS call_event 测试"})
    sid = res.json()["id"]
    try:
        with client.websocket_connect(f"/ws/{sid}") as ws:
            history = ws.receive_json()
            assert history["type"] == "history"

            ws.send_json({"type": "chat", "query": "你好"})
            call_events = []
            while True:
                msg = ws.receive_json()
                if msg["type"] == "call_event":
                    call_events.append(msg["event"])
                elif msg["type"] == "done":
                    break

        assert [e["status"] for e in call_events] == ["running", "ok"]
        final_ev = call_events[-1]
        assert final_ev["kind"] == "llm_call"
        assert final_ev["agent_name"] == "lead"
        assert final_ev["session_id"] == sid
        assert final_ev["input_tokens"] == 42
        assert final_ev["output_tokens"] == 7
        assert final_ev["blocks"][0]["type"] == "user_message"
        assert "full" not in final_ev["blocks"][0]
        assert final_ev["blocks"][-1]["type"] == "text"
        assert "full" not in final_ev["blocks"][-1]
    finally:
        web_app.app.state.llm = original_llm
        web_app.app.state.side_prompts = original_side_prompts
        trace_file = TRACE_DIR / f"{sid}.jsonl"
        if trace_file.exists():
            trace_file.unlink()
        client.delete(f"/api/sessions/{sid}")
