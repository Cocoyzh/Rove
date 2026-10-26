"""context-optimize P0：缓存用量可观测性测试。

覆盖 Usage 缓存三元组、GLM cached_tokens 形态归一化、
逐调用 JSONL 台账与成本估算。台账经 conftest 隔离到 tmp_path。
"""

import json
from types import SimpleNamespace

from rove.llm import LLMResponse, Usage
from rove.llm_adapters import AnthropicLLMAdapter, _normalize_usage
from rove.usage_ledger import UsageLedger, price_for, MODEL_PRICING


# ---------------- Usage 三元组 ----------------

def test_usage_context_input_and_hit_rate():
    u = Usage(input_tokens=100, output_tokens=50,
              cache_read_tokens=600, cache_creation_tokens=300)
    assert u.context_input_tokens == 1000
    assert u.cache_hit_rate == 0.6
    assert u.total == 150


def test_usage_zero_denominator_hit_rate():
    u = Usage(input_tokens=0, output_tokens=10)
    assert u.cache_hit_rate == 0.0
    assert u.context_input_tokens == 0


# ---------------- usage 归一化 ----------------

def test_normalize_anthropic_style_object():
    raw = SimpleNamespace(input_tokens=120, output_tokens=80,
                          cache_read_input_tokens=700, cache_creation_input_tokens=180)
    u = _normalize_usage(raw)
    # Anthropic 口径：input_tokens 已是未命中输入，缓存读/写原样保留
    assert (u.input_tokens, u.output_tokens) == (120, 80)
    assert u.cache_read_tokens == 700
    assert u.cache_creation_tokens == 180


def test_normalize_glm_prompt_tokens_details():
    # GLM(OpenAI 风格)：prompt 总量含命中部分，未命中 = 总量 - cached_tokens
    raw = {"prompt_tokens": 1000, "completion_tokens": 50,
           "prompt_tokens_details": {"cached_tokens": 800}}
    u = _normalize_usage(raw)
    assert u.input_tokens == 200
    assert u.output_tokens == 50
    assert u.cache_read_tokens == 800
    assert u.cache_creation_tokens == 0


def test_normalize_glm_details_as_object():
    details = SimpleNamespace(cached_tokens=300)
    raw = SimpleNamespace(prompt_tokens=500, completion_tokens=20,
                          prompt_tokens_details=details)
    u = _normalize_usage(raw)
    assert u.input_tokens == 200
    assert u.cache_read_tokens == 300


def test_normalize_anthropic_fields_win_over_details():
    # 两种形态同时出现时按 Anthropic 口径解析，不做二次扣减
    raw = SimpleNamespace(input_tokens=120, output_tokens=80,
                          cache_read_input_tokens=700, cache_creation_input_tokens=0,
                          prompt_tokens_details=SimpleNamespace(cached_tokens=999))
    u = _normalize_usage(raw)
    assert u.input_tokens == 120
    assert u.cache_read_tokens == 700


def test_normalize_missing_fields_default_zero():
    u = _normalize_usage(SimpleNamespace(input_tokens=10, output_tokens=5))
    assert u.cache_read_tokens == 0
    assert u.cache_creation_tokens == 0
    assert _normalize_usage(None).input_tokens == 0


# ---------------- _build_response 保留原始值 ----------------

def test_build_response_keeps_cache_fields():
    message = SimpleNamespace(
        content=[],
        model="glm-4.6",
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=120, output_tokens=80,
                              cache_read_input_tokens=700, cache_creation_input_tokens=180),
    )
    resp = AnthropicLLMAdapter._build_response(message)
    # 不再把缓存合并进 input_tokens：三元组各自保留原始值
    assert resp.usage.input_tokens == 120
    assert resp.usage.cache_read_tokens == 700
    assert resp.usage.cache_creation_tokens == 180
    assert resp.usage.context_input_tokens == 1000


# ---------------- adapter 累计与台账挂钩 ----------------

def _adapter() -> AnthropicLLMAdapter:
    return AnthropicLLMAdapter(model="glm-4.6", api_key="k", base_url=None, timeout=5)


def test_record_usage_accumulates_cache_totals():
    llm = _adapter()
    resp = LLMResponse(content="ok", tool_calls=[], model="glm-4.6", stop_reason="end_turn",
                       latency_ms=100,
                       usage=Usage(input_tokens=100, output_tokens=20,
                                   cache_read_tokens=600, cache_creation_tokens=300))
    llm._record_usage(resp)
    # total_input_tokens 维持"全部输入规模"口径
    assert llm.total_input_tokens == 1000
    assert llm.total_output_tokens == 20
    assert llm.total_cache_read_tokens == 600
    assert llm.total_cache_creation_tokens == 300
    assert llm.last_input_tokens == 1000
    assert llm.call_count == 1


def test_record_usage_writes_ledger(isolated_usage_ledger):
    llm = _adapter()
    resp = LLMResponse(content="ok", tool_calls=[], model="glm-4.6", stop_reason="end_turn",
                       latency_ms=100,
                       usage=Usage(input_tokens=100, output_tokens=20, cache_read_tokens=600))
    llm._record_usage(resp)

    recs = isolated_usage_ledger.records
    assert len(recs) == 1
    assert recs[0]["model"] == "glm-4.6"
    assert recs[0]["hit_rate"] == round(600 / 700, 4)
    assert recs[0]["latency_ms"] == 100
    # JSONL 已落盘到隔离目录
    lines = isolated_usage_ledger.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["cache_read_tokens"] == 600


# ---------------- UsageLedger 聚合 ----------------

def test_ledger_record_none_usage_is_noop(tmp_path):
    ledger = UsageLedger(sessions_dir=tmp_path)
    assert ledger.record(model="m", usage=None) is None
    assert ledger.records == []
    assert ledger.summary()["calls"] == 0


def test_ledger_summary_and_cost(tmp_path):
    ledger = UsageLedger(sessions_dir=tmp_path)
    ledger.record(model="glm-4.6", latency_ms=10,
                  usage=Usage(input_tokens=100, output_tokens=50,
                              cache_read_tokens=700, cache_creation_tokens=200))
    ledger.record(model="unknown-model", latency_ms=10,
                  usage=Usage(input_tokens=1000, output_tokens=100))

    s = ledger.summary()
    assert s["calls"] == 2
    assert s["context_input_tokens"] == 2000
    assert s["hit_rate"] == 700 / 2000
    # 成本按模型分桶：glm-4.6 命中价 + unknown 回退 default 价
    assert set(s["by_model"]) == {"glm-4.6", "unknown-model"}
    assert s["estimated_cost_usd"] > 0
    glm = s["by_model"]["glm-4.6"]
    assert glm["calls"] == 1
    assert glm["cache_read_tokens"] == 700


def test_ledger_appends_to_existing_garbage_file(tmp_path):
    # 已存在损坏内容时追加写不抛异常（台账绝不影响主流程）
    ledger = UsageLedger(sessions_dir=tmp_path)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("{broken json\n", encoding="utf-8")
    ledger.record(model="m", usage=Usage(input_tokens=1, output_tokens=1))
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["input_tokens"] == 1


def test_price_for_fallback():
    assert price_for("nope-not-here") is MODEL_PRICING["default"]
    assert price_for("glm-4.6")["cache_read"] < price_for("glm-4.6")["input"]
