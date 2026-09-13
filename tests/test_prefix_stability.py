"""context-optimize P1：前缀稳定化测试。

黑板迁出 system prompt（尾部合成消息注入 + 去重）、工具 schema 排序、
消息序列化确定性。命中率 ≥70% 的线上验收见 plans/context-optimize.md。
"""

import json

from rove.compaction.compaction_layers import run_pipeline
from rove.lead_agent import LeadAgent, _build_system_prompt, _inject_synthetic, MEMORY_TURN_PREFIXES
from rove.llm import LLMResponse, LLMRequest
from rove.memory import MemoryManager
from rove.messages import Message, ToolCall
from rove.paths import WORKSPACE_ROOT
from rove.permissions import PermissionPolicy
from rove.prompt.system_prompt import SYSTEM_PROMPT
from rove.tool_registry import Tool, ToolRegistry
from rove.llm_adapters import AnthropicLLMAdapter
from rove import lead_agent


class RecordingLLM:
    """记录每次请求的最小 LLM 替身（不触发压缩，直接正常收尾）。"""

    context_window = 1_000_000
    cjk_token_ratio = 1.3
    last_input_tokens = 0
    last_output_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0

    def __init__(self):
        self.calls = []

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        # 快照当时看到的消息序列，避免后续共享列表变异影响断言
        self.calls.append({
            "system_prompt": request.system_prompt,
            "messages": [m.to_dict() for m in request.messages],
            "raw_messages": list(request.messages),
        })
        return LLMResponse(content="done", tool_calls=[], model="test-model",
                           stop_reason="end_turn", latency_ms=1, usage=None)


def _registry() -> ToolRegistry:
    return ToolRegistry(PermissionPolicy(WORKSPACE_ROOT), lambda *a, **k: True)


def _agent(tmp_path, monkeypatch, **wm):
    mm = MemoryManager(tmp_path)
    if wm:
        mm.update_working(**wm)
    monkeypatch.setattr(lead_agent, "MEMORY", mm)
    llm = RecordingLLM()
    return LeadAgent(llm, _registry(), max_steps=3), llm, mm


def test_build_system_prompt_is_static():
    assert _build_system_prompt() == SYSTEM_PROMPT


def test_blackboard_injected_as_tail_message_not_system_prompt(tmp_path, monkeypatch):
    agent, llm, _ = _agent(tmp_path, monkeypatch, goal="修复缓存命中率")

    agent.run("请开始")

    assert llm.calls, "应至少发起一次 LLM 调用"
    # system prompt 逐字节稳定且不含黑板渲染内容
    assert all(c["system_prompt"] == SYSTEM_PROMPT for c in llm.calls)
    assert "修复缓存命中率" not in llm.calls[0]["system_prompt"]
    # 黑板以尾部合成 user 消息出现，且是当次请求的最后一条
    mem = [d for d in llm.calls[0]["messages"]
           if d["role"] == "user" and str(d.get("content", "")).startswith(MEMORY_TURN_PREFIXES)]
    assert len(mem) == 1
    assert "修复缓存命中率" in mem[0]["content"]
    assert llm.calls[0]["messages"][-1] is mem[0] or llm.calls[0]["messages"][-1] == mem[0]


def test_memory_turn_deduped_when_unchanged(tmp_path, monkeypatch):
    agent, llm, _ = _agent(tmp_path, monkeypatch, goal="目标不变")

    agent.run("第一轮")
    agent.run("第二轮")

    def mem_count(call):
        return sum(1 for d in call["messages"]
                   if d["role"] == "user" and str(d.get("content", "")).startswith(MEMORY_TURN_PREFIXES))

    assert mem_count(llm.calls[0]) == 1
    # 黑板未变：第二轮不追加重复副本
    assert mem_count(llm.calls[1]) == 1


def test_memory_turn_reinjected_after_change(tmp_path, monkeypatch):
    agent, llm, mm = _agent(tmp_path, monkeypatch, goal="旧目标")

    agent.run("第一轮")
    mm.update_working(goal="新目标")
    agent.run("第二轮")

    def mem_contents(call):
        return [d["content"] for d in call["messages"]
                if d["role"] == "user" and str(d.get("content", "")).startswith(MEMORY_TURN_PREFIXES)]

    first, second = mem_contents(llm.calls[0]), mem_contents(llm.calls[1])
    assert "旧目标" in first[0] and len(first) == 1
    assert len(second) == 2 and "新目标" in second[-1]


def test_no_memory_message_when_memory_empty(tmp_path, monkeypatch):
    agent, llm, _ = _agent(tmp_path, monkeypatch)

    agent.run("空白黑板")

    assert all(not str(d.get("content", "")).startswith(MEMORY_TURN_PREFIXES)
               for c in llm.calls for d in c["messages"])


def test_inject_synthetic_skips_empty_content():
    msgs: list = []
    _inject_synthetic(msgs, "")
    assert msgs == []
    _inject_synthetic(msgs, "<reminder>hi</reminder>")
    assert len(msgs) == 1 and msgs[0].role == "user"


def test_memory_turn_survives_pipeline_rerun(tmp_path, monkeypatch):
    """压缩管线每回合先跑、记忆后注入：注入内容不应被本轮管线改写。"""
    agent, llm, mm = _agent(tmp_path, monkeypatch, goal="管线协同")
    before = len(agent.messages)

    agent.run("协同")

    # 注入发生在 run_pipeline 之后：本轮新增的只有真实 query + 记忆消息 + 助手回复
    assert len(agent.messages) == before + 3
    memory_msg = agent.messages[-2]
    assert memory_msg.content.startswith(MEMORY_TURN_PREFIXES)
    assert "管线协同" in memory_msg.content


# ---------------- P1-2 工具 schema 排序 ----------------

def test_get_schemas_sorted_by_name():
    registry = _registry()
    for name in ("zebra_tool", "alpha_tool", "mid_tool"):
        registry.register(Tool(name=name, description="d", input_schema={"type": "object"}))

    names = [s["name"] for s in registry.get_schemas()]
    assert names == sorted(names)
    # 重复导出逐字节稳定
    assert registry.get_schemas() == registry.get_schemas()


# ---------------- P1-3 序列化确定性 ----------------

def test_convert_messages_serialization_deterministic():
    msgs = [
        Message(role="user", content="目标"),
        Message(role="assistant", content="收到",
                tool_calls=[ToolCall(tool_id="c1", tool_name="read_file",
                                     tool_args={"path": "a.py", "lines": [1, 2]})]),
        Message(role="tool", tool_call_id="c1", content="out"),
        Message(role="user", content="<working-memory>\nGoal: x\n</working-memory>"),
    ]
    a = AnthropicLLMAdapter._convert_messages(msgs)
    b = AnthropicLLMAdapter._convert_messages(msgs)
    # dict 相等不含键序，json.dumps 键序敏感：双保险确认无随机/时间因素混入
    assert a == b
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)


def test_run_pipeline_keeps_memory_message_intact(tmp_path, monkeypatch):
    """水位之内 run_pipeline 不改写消息，历史前缀保持只增不改。"""
    messages = [
        Message(role="user", content="任务"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="<working-memory>\nGoal: 保持原样\n</working-memory>"),
    ]
    snapshot = json.dumps([m.to_dict() for m in messages], ensure_ascii=False)

    class NoopLLM:
        context_window = 1_000_000
        cjk_token_ratio = 1.3
        last_input_tokens = 0
        last_output_tokens = 0

    run_pipeline(NoopLLM(), messages, on_summary=lambda s: None)
    assert json.dumps([m.to_dict() for m in messages], ensure_ascii=False) == snapshot


def test_conversation_prefix_append_only_across_turns(tmp_path, monkeypatch):
    """P1 核心断言：稳态多轮会话中，上一轮的完整请求前缀
    （转换后的 system + 消息序列）在下一轮原样保留，历史只增不改。"""
    agent, llm, mm = _agent(tmp_path, monkeypatch, goal="前缀稳定")

    agent.run("第一轮")
    mm.update_working(finding="一个新发现")
    agent.run("第二轮")

    assert len(llm.calls) == 2
    a = AnthropicLLMAdapter._convert_messages(llm.calls[0]["raw_messages"])
    b = AnthropicLLMAdapter._convert_messages(llm.calls[1]["raw_messages"])
    assert len(b) > len(a)
    assert b[:len(a)] == a, "第二回合的前缀必须与第一回合转换结果一致（append-only）"
