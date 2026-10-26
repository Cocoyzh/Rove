"""逐调用用量台账（context-optimize P0 可观测性）。

每次 LLM 调用后在 SESSIONS_DIR 追加一条 JSONL 记录（复用会话目录，不新造目录），
并提供进程内聚合：缓存命中率、按定价表估算成本。
台账绝不能影响主流程：任何落盘/聚合异常都静默吞掉。
"""

import json
import threading
import time
from pathlib import Path
from typing import List, Optional

from rove.llm import Usage
from rove.paths import SESSIONS_DIR

LEDGER_FILENAME = "usage_ledger.jsonl"

# 估算用定价表（USD / 1M tokens）。cache_read 取各家公开的缓存读折扣价；
# cache_creation 近似按未命中输入价计（GLM 隐式缓存半价口径的保守估计）。
# 以服务商官网最新价为准，后续可改为环境变量覆盖。
MODEL_PRICING = {
    "glm-4.6": {"input": 0.6, "cache_read": 0.3, "output": 2.2},
    "glm-4.5": {"input": 0.6, "cache_read": 0.3, "output": 2.2},
    "glm-4.5-air": {"input": 0.2, "cache_read": 0.1, "output": 1.1},
    "claude-sonnet-4-5": {"input": 3.0, "cache_read": 0.3, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "cache_read": 0.1, "output": 5.0},
    "default": {"input": 0.6, "cache_read": 0.3, "output": 2.2},
}


def price_for(model: str) -> dict:
    """精确匹配模型价目，未收录模型回退 default。"""
    return MODEL_PRICING.get(model) or MODEL_PRICING["default"]


class UsageLedger:
    """逐调用用量台账：内存聚合 + SESSIONS_DIR 下 JSONL 追加落盘。"""

    def __init__(self, sessions_dir: Optional[Path] = None):
        self.dir = Path(sessions_dir) if sessions_dir else SESSIONS_DIR
        self._lock = threading.Lock()
        self.records: List[dict] = []

    @property
    def path(self) -> Path:
        return self.dir / LEDGER_FILENAME

    def record(self, model: str, usage: Optional[Usage], latency_ms: int = 0) -> Optional[dict]:
        if usage is None:
            return None
        rec = {
            "ts": time.time(),
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_creation_tokens": usage.cache_creation_tokens,
            "context_input_tokens": usage.context_input_tokens,
            "latency_ms": latency_ms,
            "hit_rate": round(usage.cache_hit_rate, 4),
        }
        with self._lock:
            self.records.append(rec)
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass  # 落盘失败不影响 agent 主流程
        return rec

    def summary(self, records: Optional[List[dict]] = None) -> dict:
        """聚合命中率与估算成本；cost 按模型分桶后求和。"""
        recs = self.records if records is None else records
        inp = sum(r["input_tokens"] for r in recs)
        out = sum(r["output_tokens"] for r in recs)
        read = sum(r["cache_read_tokens"] for r in recs)
        create = sum(r["cache_creation_tokens"] for r in recs)
        full_input = inp + read + create

        cost = 0.0
        by_model: dict = {}
        for r in recs:
            m = r["model"] or "default"
            p = price_for(m)
            m_cost = (
                r["input_tokens"] * p["input"]
                + r["cache_read_tokens"] * p["cache_read"]
                + r["cache_creation_tokens"] * p["input"]
                + r["output_tokens"] * p["output"]
            ) / 1_000_000
            cost += m_cost
            agg = by_model.setdefault(m, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "estimated_cost_usd": 0.0,
            })
            agg["calls"] += 1
            agg["input_tokens"] += r["input_tokens"]
            agg["output_tokens"] += r["output_tokens"]
            agg["cache_read_tokens"] += r["cache_read_tokens"]
            agg["cache_creation_tokens"] += r["cache_creation_tokens"]
            agg["estimated_cost_usd"] = round(agg["estimated_cost_usd"] + m_cost, 6)

        return {
            "calls": len(recs),
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_tokens": read,
            "cache_creation_tokens": create,
            "context_input_tokens": full_input,
            "hit_rate": read / full_input if full_input else 0.0,
            "estimated_cost_usd": round(cost, 4),
            "by_model": by_model,
        }


LEDGER = UsageLedger()
