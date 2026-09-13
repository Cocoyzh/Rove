"""P3 Prompt 拆分：system prompt 分段化、request_composition 记录与 TitleService 单测。"""

import pytest

from rove.llm import LLMResponse, LLMRequest, Usage
from rove.messages import ToolCall
from rove.tool_registry import ToolRegistry, Tool
from rove.permissions import PermissionPolicy
from rove.web.session_manager import SessionManager


# ==================== SYSTEM_PROMPT 分段化 ====================


def test_system_prompt_assembly_byte_stable():
    from rove.prompt.system_prompt import (
        SYSTEM_PROMPT,
        SEGMENT_ORDER,
        assemble_system_prompt,
    )

    # 同参数两次拼装逐字节一致（前缀缓存的前提）
    assert assemble_system_prompt() == assemble_system_prompt()
    # 默认拼装 = 模块级 SYSTEM_PROMPT（与历史单体等价）
    assert assemble_system_prompt() == SYSTEM_PROMPT
    # 动态记忆块走尾部注入
    assert assemble_system_prompt("<memory/>") == SYSTEM_PROMPT + "\n\n<memory/>"
    # 分段完整性：每个段的关键锚点在拼装结果中出现且仅出现一次
    anchors = ["You are Rove", "Environment:", "Workflow:", "Memory:", "Team:", "Available skills:"]
    for anchor in anchors:
        assert SYSTEM_PROMPT.count(anchor) == 1, anchor
    assert len(SEGMENT_ORDER) == 6


def test_lead_agent_records_request_composition(tmp_path):
    from rove.lead_agent import LeadAgent
    from rove.call_trace import CallTracer, new_session_id
    from rove.llm_adapters import BaseLLMAdapter

    class MockLLM(BaseLLMAdapter):
        def __init__(self):
            super().__init__(model="mock", api_key="k", base_url=None, timeout=None, context_window=100000)
            self.responses = [
                LLMResponse(content="完成", tool_calls=[], model="mock", stop_reason="end_turn",
                            latency_ms=1, usage=Usage(input_tokens=5, output_tokens=1)),
            ]

        def _create_client(self):
            return None

        def complete(self, request: LLMRequest) -> LLMResponse:
            return self.responses.pop(0)

        def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
            return self.complete(request)

    registry = ToolRegistry(PermissionPolicy(tmp_path), lambda *a, **k: True)
    registry.register(Tool(name="do_work", description="d",
                           input_schema={"type": "object", "properties": {}},
                           handler=lambda **kw: "ok"))

    tracer = CallTracer(new_session_id("repl"), trace_dir=tmp_path)
    lead = LeadAgent(MockLLM(), registry, max_steps=3, tracer=tracer.for_agent("lead"))
    lead.run("做个任务")

    import json
    path = tmp_path / f"{tracer.session_id}.jsonl"
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    comp = event["request_composition"]
    assert comp["system_segments"] == ["identity", "environment", "tool_policy",
                                       "memory_policy", "team_policy", "skills"]
    assert comp["memory_block"] is False
    assert comp["tool_count"] >= 1
    assert comp["message_count"] >= 1


# ==================== SessionManager title_source ====================


def test_side_title_never_overrides_manual():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        sm = SessionManager(sessions_dir=Path(d))
        sid = sm.create_session()["id"]

        # 旁路命名成功写入并标记来源
        assert sm.set_side_title(sid, "自动标题A")["title"] == "自动标题A"
        assert sm.get_session(sid)["title_source"] == "side_prompt"

        # 用户手动改名
        sm.update_session_title(sid, "手动标题B")
        assert sm.get_session(sid)["title_source"] == "manual"

        # 旁路命名绝不清除手动标题
        sm.set_side_title(sid, "自动标题C")
        data = sm.get_session(sid)
        assert data["title"] == "手动标题B"
        assert data["title_source"] == "manual"


# ==================== SidePromptService / TitleService ====================


class TitleMockLLM:
    """最小适配器桩：按序返回固定标题。"""

    def __init__(self, titles):
        self.api_key = "test-key"
        self.titles = list(titles)
        self.prompts: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.prompts.append(request)
        content = self.titles.pop(0) if self.titles else "兜底标题"
        return LLMResponse(content=content, tool_calls=[], model="mock",
                           stop_reason="end_turn", latency_ms=1)


def test_title_generated_once_per_session():
    from rove.side_prompts import SidePromptService

    llm1 = TitleMockLLM(["修复登录超时 Bug"])
    svc = SidePromptService(llm1)
    assert svc.generate_title("登录总是超时，帮我排查", session_id="s_1") == "修复登录超时 Bug"

    # 同一会话第二次调用不再生成（中途不改名），且不再发起旁路 LLM 调用
    assert svc.generate_title("换个话题聊聊天气", session_id="s_1") is None
    assert len(llm1.prompts) == 1

    # 其他会话仍可各自生成
    llm2 = TitleMockLLM(["数据管道重构", "第二步标题"])
    svc2 = SidePromptService(llm2)
    assert svc2.generate_title("重构数据管道", session_id="s_2") == "数据管道重构"
    assert svc2.generate_title("另一个任务", session_id="s_3") == "第二步标题"
    assert len(llm2.prompts) == 2


def test_title_prompt_contract():
    from rove.side_prompts import SidePromptService, TITLE_MAX_CHARS

    llm = TitleMockLLM(["  \"清理构建缓存\"  \n"])
    svc = SidePromptService(llm)
    title = svc.generate_title("帮我清理构建缓存")
    assert title == "清理构建缓存"  # 去引号与空白
    prompt = llm.prompts[0].messages[0].content
    assert f"{TITLE_MAX_CHARS}个字的中文标题" in prompt
    assert "帮我清理构建缓存" in prompt
    assert llm.prompts[0].max_tokens == 64


def test_title_skipped_without_api_key():
    from rove.side_prompts import SidePromptService

    llm = TitleMockLLM(["不应该被调用"])
    llm.api_key = ""
    svc = SidePromptService(llm)
    assert svc.generate_title("任何请求", session_id="s_x") is None
    assert llm.prompts == []


def test_web_autotitle_flow(tmp_path):
    """Web 端到端：首轮 done 后旁路标题写入会话元数据并推送 session_title。"""
    import rove.web.app as web_app
    from starlette.testclient import TestClient
    from rove.llm_adapters import BaseLLMAdapter

    class ChatMockLLM(BaseLLMAdapter):
        def __init__(self):
            super().__init__(model="mock", api_key="k", base_url=None, timeout=None, context_window=100000)

        def _create_client(self):
            return None

        def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(content="收到", tool_calls=[], model="mock",
                               stop_reason="end_turn", latency_ms=1,
                               usage=Usage(input_tokens=1, output_tokens=1))

        def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
            return self.complete(request)

    class TitleStub:
        def __init__(self):
            self.calls = []

        def generate_title(self, query, session_id=None):
            self.calls.append((query, session_id))
            return "端到端标题"

    original_llm = web_app.app.state.llm
    original_sp = web_app.app.state.side_prompts
    title_stub = TitleStub()
    web_app.app.state.llm = ChatMockLLM()
    web_app.app.state.side_prompts = title_stub

    client = TestClient(web_app.app)
    sid = client.post("/api/sessions", json={"title": "新会话"}).json()["id"]
    try:
        with client.websocket_connect(f"/ws/{sid}") as ws:
            assert ws.receive_json()["type"] == "history"
            ws.send_json({"type": "chat", "query": "帮我写个爬虫"})
            got_title_event = False
            while True:
                msg = ws.receive_json()
                if msg["type"] == "session_title":
                    assert msg["title"] == "端到端标题"
                    got_title_event = True
                elif msg["type"] == "done":
                    break
        assert got_title_event
        assert title_stub.calls == [("帮我写个爬虫", sid)]
        session = web_app.app.state.session_manager.get_session(sid)
        assert session["title"] == "端到端标题"
        assert session["title_source"] == "side_prompt"
    finally:
        web_app.app.state.llm = original_llm
        web_app.app.state.side_prompts = original_sp
        client.delete(f"/api/sessions/{sid}")
