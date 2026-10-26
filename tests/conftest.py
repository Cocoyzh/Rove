"""全局测试夹具：把逐调用用量台账隔离到 tmp_path，
避免测试流量写入真实工作区 .rove/sessions/usage_ledger.jsonl。"""

import pytest

from rove import llm_adapters
from rove.usage_ledger import UsageLedger


@pytest.fixture(autouse=True)
def isolated_usage_ledger(tmp_path, monkeypatch):
    ledger = UsageLedger(sessions_dir=tmp_path / "sessions")
    monkeypatch.setattr(llm_adapters, "LEDGER", ledger)
    yield ledger
