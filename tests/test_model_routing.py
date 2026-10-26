"""模型路由（plans/subagent-optimize.md P3）单测。

覆盖验收标准：
- LLM_FAST_MODEL_ID 未配置时行为与现在完全一致（回归保障）；
- 配置后 dispatch_subagent 默认路由 fast 模型，显式 "fast"/具体 id 亦可；
- 适配器工厂按 model id 缓存实例；成本按模型分别计价。
"""

import pytest

from rove.llm_adapters import AdapterFactory, estimate_cost_usd
from rove.tools.subagent import SubagentManager
from test_subagent import FakeLLM, final_response


def deny_all(tool_name, arguments, reason) -> bool:
    return False


class FakeFactory:
    """按 id 产出现成的 FakeLLM，并记录取用轨迹。"""

    def __init__(self):
        self.adapters = {}
        self.requested = []

    def get(self, model_id):
        self.requested.append(model_id)
        if model_id not in self.adapters:
            self.adapters[model_id] = FakeLLM(
                [final_response(f"报告 by {model_id}", usage=(10, 10))])
        return self.adapters[model_id]


# ---------- 工厂与角色解析 ----------

def test_factory_caches_per_model_id():
    f = AdapterFactory()
    a1 = f.get("model-a")
    a2 = f.get("model-a")
    a3 = f.get("model-b")
    assert a1 is a2
    assert a1 is not a3
    assert a1.model == "model-a"


def test_factory_resolve_roles(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_ID", "main-model")
    monkeypatch.delenv("LLM_FAST_MODEL_ID", raising=False)

    assert AdapterFactory.resolve("main") == "main-model"
    # 未配置 LLM_FAST_MODEL_ID 时 fast 回落主模型（回归保障的关键语义）
    assert AdapterFactory.resolve("fast") == "main-model"

    monkeypatch.setenv("LLM_FAST_MODEL_ID", "fast-model")
    assert AdapterFactory.resolve("fast") == "fast-model"


# ---------- 成本计价 ----------

def test_estimate_cost_usd_per_model():
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 0) == pytest.approx(0.8)
    assert estimate_cost_usd("claude-opus-5", 0, 1_000_000) == pytest.approx(75.0)
    # 未知模型走默认计价
    assert estimate_cost_usd("totally-unknown", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_report_usage_footer_contains_cost(tmp_path):
    llm = FakeLLM([final_response("## 结论摘要\nok\n\n## 关键文件与证据\n- x\n\n## 遇到的障碍\n无",
                                  usage=(1000, 500))])
    mgr = SubagentManager(llm, results_dir=tmp_path, approval_handler=deny_all)
    report = mgr.dispatch(objective="o", context="c")
    assert "cost_usd=" in report
    # fake-main 未知模型走默认计价：(1000*3 + 500*15) / 1e6 = 0.0105
    assert "cost_usd=0.010500" in report


# ---------- dispatch_subagent 路由 ----------

def test_dispatch_without_fast_model_uses_lead_adapter(tmp_path):
    llm = FakeLLM([final_response("主模型报告", usage=(10, 10))])
    factory = FakeFactory()
    mgr = SubagentManager(llm, results_dir=tmp_path, approval_handler=deny_all,
                          fast_model_id=None, adapter_factory=factory)

    report = mgr.dispatch(objective="o", context="c")

    assert "主模型报告" in report
    assert len(llm.calls) == 1
    assert factory.requested == [], "未配置 fast model 时不得触碰工厂（行为与现在一致）"


def test_dispatch_defaults_to_fast_model_when_configured(tmp_path):
    lead_llm = FakeLLM()  # lead 适配器不应被消耗
    factory = FakeFactory()
    mgr = SubagentManager(lead_llm, results_dir=tmp_path, approval_handler=deny_all,
                          fast_model_id="cheap-x", adapter_factory=factory)

    report = mgr.dispatch(objective="o", context="c")

    assert "报告 by cheap-x" in report
    assert factory.requested == ["cheap-x"]
    assert len(lead_llm.calls) == 0


def test_dispatch_model_fast_alias_routes_to_fast(tmp_path):
    lead_llm = FakeLLM()
    factory = FakeFactory()
    mgr = SubagentManager(lead_llm, results_dir=tmp_path, approval_handler=deny_all,
                          fast_model_id="cheap-x", adapter_factory=factory)

    report = mgr.dispatch(objective="o", context="c", model="fast")

    assert "报告 by cheap-x" in report
    assert factory.requested == ["cheap-x"]


def test_dispatch_explicit_model_id_uses_factory_cache(tmp_path):
    factory = FakeFactory()
    mgr = SubagentManager(FakeLLM(), results_dir=tmp_path, approval_handler=deny_all,
                          fast_model_id=None, adapter_factory=factory)

    mgr.dispatch(objective="o", context="c", model="model-b")
    mgr.dispatch(objective="o", context="c", model="model-b")

    assert factory.requested == ["model-b", "model-b"]
    # 同 id 复用同一适配器实例（其 usage 计数持续累加）
    assert len(factory.adapters["model-b"].calls) == 2


def test_dispatch_model_fast_without_config_falls_back_to_lead(tmp_path):
    llm = FakeLLM([final_response("回落主模型", usage=(10, 10))])
    factory = FakeFactory()
    mgr = SubagentManager(llm, results_dir=tmp_path, approval_handler=deny_all,
                          fast_model_id=None, adapter_factory=factory)

    report = mgr.dispatch(objective="o", context="c", model="fast")

    assert "回落主模型" in report
    assert len(llm.calls) == 1
    assert factory.requested == []
