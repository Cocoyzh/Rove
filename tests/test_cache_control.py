"""context-optimize P2：显式缓存断点测试。

覆盖 LLM_CACHE_MODE 解析、端点放行门控、断点放置与请求装配接线。
"""

import pytest
from unittest.mock import MagicMock

from rove.llm import LLMRequest
from rove.llm_adapters import (
    AnthropicLLMAdapter,
    resolve_cache_mode,
    DEFAULT_CACHE_MODE,
)
from rove.messages import Message


# ---------------- 配置解析 ----------------

def test_resolve_cache_mode_defaults():
    assert resolve_cache_mode(None) == "auto"
    assert resolve_cache_mode("explicit") == "explicit"
    assert resolve_cache_mode("OFF") == "off"
    assert resolve_cache_mode("  auto  ") == "auto"


def test_resolve_cache_mode_invalid_falls_back(capfd):
    assert resolve_cache_mode("yolo") == DEFAULT_CACHE_MODE == "auto"
    assert "LLM_CACHE_MODE" in capfd.readouterr().out


def test_adapter_cache_mode_from_param_and_env(monkeypatch):
    a = AnthropicLLMAdapter(model="m", api_key="k", base_url=None, timeout=5, cache_mode="explicit")
    assert a.cache_mode == "explicit"

    monkeypatch.setenv("LLM_CACHE_MODE", "off")
    b = AnthropicLLMAdapter(model="m", api_key="k", base_url=None, timeout=5)
    assert b.cache_mode == "off"

    monkeypatch.setenv("LLM_CACHE_MODE", "bogus")
    c = AnthropicLLMAdapter(model="m", api_key="k", base_url=None, timeout=5)
    assert c.cache_mode == "auto"


# ---------------- 端点放行门控 ----------------

def test_endpoint_gate():
    assert AnthropicLLMAdapter(model="m", api_key="k", base_url=None, timeout=5)._explicit_cache_allowed
    assert AnthropicLLMAdapter(
        model="m", api_key="k", base_url="https://api.anthropic.com", timeout=5)._explicit_cache_allowed
    assert not AnthropicLLMAdapter(
        model="m", api_key="k", base_url="https://open.bigmodel.cn/api/anthropic", timeout=5)._explicit_cache_allowed


def test_endpoint_gate_env_attest(monkeypatch):
    monkeypatch.setenv("LLM_CACHE_EXPLICIT_ALLOWED", "1")
    a = AnthropicLLMAdapter(model="m", api_key="k",
                            base_url="https://open.bigmodel.cn/api/anthropic", timeout=5)
    assert a._explicit_cache_allowed


# ---------------- 断点放置 ----------------

def _req():
    return LLMRequest(messages=[Message(role="user", content="hi")],
                      tools=[{"name": "alpha"}, {"name": "beta"}],
                      max_tokens=10, system_prompt="sys")


def test_apply_breakpoints_marks_tools_system_and_tail_message():
    msgs = [{"role": "user", "content": "hi"}]
    system, tools, out = AnthropicLLMAdapter._apply_explicit_cache_control("sys", msgs, _req().tools)

    # 断点 1：system 块化打点 + 仅最后一个工具打点
    assert system == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert "cache_control" not in tools[0]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}

    # 断点 2：短历史打在第一条消息，字符串内容转为 text 块
    assert out[0]["content"] == [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]


def test_apply_breakpoints_large_history_index():
    # 40 条消息：断点打在倒数第 35 条（保留窗口边缘）
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(40)]
    _, _, out = AnthropicLLMAdapter._apply_explicit_cache_control(None, msgs, [])

    marked = [i for i, m in enumerate(out)
              if isinstance(m["content"], list) and "cache_control" in m["content"][0]]
    assert marked == [40 - 35]


def test_apply_breakpoints_marks_last_block_of_list_content():
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "out"},
        {"type": "text", "text": "tail"},
    ]}]
    _, _, out = AnthropicLLMAdapter._apply_explicit_cache_control(None, msgs, [])
    blocks = out[0]["content"]
    assert "cache_control" not in blocks[0]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    # 原列表未被改动（入参保护）
    assert "cache_control" not in msgs[0]["content"][-1]


# ---------------- 请求装配接线 ----------------

def _stream_adapter(base_url=None, cache_mode=None) -> AnthropicLLMAdapter:
    adapter = AnthropicLLMAdapter(model="m", api_key="k", base_url=base_url,
                                  timeout=5, cache_mode=cache_mode)
    adapter._client = MagicMock()
    final = MagicMock()
    final.content = []
    final.model = "m"
    final.stop_reason = "end_turn"
    final.usage = None
    ctx = adapter._client.messages.stream.return_value.__enter__.return_value
    ctx.get_final_message.return_value = final
    return adapter


def test_stream_auto_mode_sends_no_cache_control():
    adapter = _stream_adapter(cache_mode="auto")
    adapter.stream(_req())
    kwargs = adapter._client.messages.stream.call_args.kwargs
    assert isinstance(kwargs["system"], str)
    assert all("cache_control" not in t for t in kwargs["tools"])
    assert all("cache_control" not in str(m) for m in kwargs["messages"])


def test_stream_explicit_mode_on_allowed_endpoint():
    adapter = _stream_adapter(cache_mode="explicit")  # base_url=None → 放行
    adapter.stream(_req())
    kwargs = adapter._client.messages.stream.call_args.kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" in str(kwargs["messages"])


def test_stream_explicit_mode_falls_back_on_unverified_endpoint():
    adapter = _stream_adapter(base_url="https://example.com/anthropic", cache_mode="explicit")
    adapter.stream(_req())
    kwargs = adapter._client.messages.stream.call_args.kwargs
    # 未验证端点：回退 auto，载荷无断点
    assert isinstance(kwargs["system"], str)
    assert all("cache_control" not in t for t in kwargs["tools"])
