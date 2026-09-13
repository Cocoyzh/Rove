"""P1 验收：前缀缓存命中率度量脚本（探针式，跨分支可用）。

原理：monkeypatch AnthropicLLMAdapter._build_response，在响应解析前截获
原始 usage（cache_read_input_tokens / cache_creation_input_tokens /
prompt_tokens_details.cached_tokens / prompt_cache_hit_tokens），与
rove 自身的 Usage 解析完全解耦——同一份脚本可在基线分支与本分支运行，
得到口径一致的改造前后对比。

用法（需 .env 提供 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL）：

    PYTHONPATH=src python scripts/cache_hitrate_bench.py [轮数，默认 10]
    ROVE_BENCH_DOTENV=/path/to/.env   显式指定环境文件（默认当前目录 .env）

基线测量：在基线提交的 worktree 里放入本脚本后同样运行即可。
脚本只输出 token 数值与模型名，绝不打印密钥与端点地址。
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.getenv("ROVE_BENCH_DOTENV", ".env"))

from rove.lead_agent import LeadAgent  # noqa: E402
from rove.llm_adapters import AnthropicLLMAdapter  # noqa: E402
from rove.paths import SKILL_DIR, TASK_DIR, TEAM_DIR  # noqa: E402
from rove.skill_loader import SkillLoader  # noqa: E402
from rove.task_manager import TaskManager  # noqa: E402
from rove.tools.agent_teams import TeammateManger  # noqa: E402
from rove.tools.tools_setup import build_default_registry  # noqa: E402

# 固定任务脚本：模拟"读取文件并总结"的稳态多轮会话，逐轮在既有历史上追加。
# 只引用基线分支与本分支都存在的文件，保证两边可跑。
# 第 3、7 轮强制调用 memory_update 改变黑板：旧设计（黑板进 system prompt）下
# 这两轮会使整个前缀缓存失效，P1 之后黑板走尾部消息、缓存不受影响。
FIXED_QUERIES = [
    "读取 README.md，用一句话概括这个项目是做什么的。",
    "用 memory_update 记录 finding『项目概况调研完成』，然后列出项目的主要技术栈。",
    "读取 src/rove/llm.py，列出其中定义的类名。",
    "对上一步列出的每个类，用一句话说明其职责。",
    "读取 src/rove/lead_agent.py，说明其注入机制的设计（reminder/inbox 等合成消息）。",
    "结合前面的发现，用三句话总结本项目的上下文管理设计。",
    "用 memory_update 记录 finding『上下文设计已梳理完毕』，然后读取 pyproject.toml 报告 Python 版本要求。",
    "检查前面总结的设计与 pyproject 声明的依赖是否一致，给出结论。",
    "把前八轮的关键结论整理成一份不超过 5 条的要点清单。",
    "最后：用一段话向新同事介绍这个仓库，复用前面所有结论，不要重新读文件。",
]


class UsageProbe:
    """在 _build_response 之前截获原始 usage，兼容 Anthropic / OpenAI 两种形态。"""

    def __init__(self):
        self.records: list[dict] = []

    @staticmethod
    def _get(raw, name):
        if raw is None:
            return 0
        if isinstance(raw, dict):
            return raw.get(name) or 0
        return getattr(raw, name, 0) or 0

    def record(self, raw) -> None:
        if raw is None:
            return
        input_raw = self._get(raw, "input_tokens") or self._get(raw, "prompt_tokens")
        output = self._get(raw, "output_tokens") or self._get(raw, "completion_tokens")
        read = self._get(raw, "cache_read_input_tokens")
        create = self._get(raw, "cache_creation_input_tokens")
        if not read:
            # OpenAI/DeepSeek 口径：prompt 总量含命中部分
            cached = self._get(self._get(raw, "prompt_tokens_details"), "cached_tokens") \
                or self._get(raw, "prompt_cache_hit_tokens")
            if cached:
                read = cached
                input_raw = max(0, input_raw - cached)
        self.records.append({
            "ts": time.time(),
            "input": int(input_raw),
            "output": int(output),
            "read": int(read),
            "create": int(create),
        })


PROBE = UsageProbe()
_ORIG_BUILD = AnthropicLLMAdapter.__dict__["_build_response"].__func__


def _probed_build_response(message, latency_ms: int = 0):
    PROBE.record(getattr(message, "usage", None))
    return _ORIG_BUILD(message, latency_ms=latency_ms)


AnthropicLLMAdapter._build_response = staticmethod(_probed_build_response)


def _print_summary(tag: str) -> None:
    recs = PROBE.records
    inp = sum(r["input"] for r in recs)
    out = sum(r["output"] for r in recs)
    read = sum(r["read"] for r in recs)
    create = sum(r["create"] for r in recs)
    full_input = inp + read + create
    hit = read / full_input if full_input else 0.0
    print(f"\n===== {tag} =====")
    print(f"calls                {len(recs)}")
    print(f"input(uncached)      {inp}")
    print(f"cache_read           {read}")
    print(f"cache_creation       {create}")
    print(f"hit_rate             {hit:.2%}   (验收口径: cache_read / 全部输入)")
    return hit


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else len(FIXED_QUERIES)
    queries = FIXED_QUERIES[:rounds]

    ctx_override = os.getenv("LLM_CONTEXT_WINDOW")
    llm = AnthropicLLMAdapter(
        model=os.getenv("LLM_MODEL_ID"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        timeout=int(os.getenv("LLM_TIMEOUT", "60")),
        context_window=int(ctx_override) if ctx_override else None,
    )
    print(f"model={llm.model} rounds={len(queries)}\n")

    registry = build_default_registry(
        skill_loader=SkillLoader(SKILL_DIR),
        task_manager=TaskManager(TASK_DIR),
        team_manager=TeammateManger(TEAM_DIR, TaskManager(TASK_DIR), llm),
    )
    lead = LeadAgent(llm, registry, 50)

    for i, query in enumerate(queries, 1):
        mark = len(PROBE.records)
        print(f"--- Round {i}/{len(queries)} ---")
        lead.run(query)
        recs = PROBE.records[mark:]
        r_in = sum(r["input"] for r in recs)
        r_read = sum(r["read"] for r in recs)
        r_full = sum(r["input"] + r["read"] + r["create"] for r in recs)
        hit = r_read / r_full if r_full else 0.0
        print(f"  calls={len(recs)} in={r_in} cache_read={r_read} round_hit={hit:.2%}")

    hit = _print_summary("Aggregate")
    if len(PROBE.records) >= 3 and hit >= 0.7:
        print("PASS")
    elif len(PROBE.records) >= 3:
        print("BELOW TARGET: 检查前缀是否仍每回合变化（system prompt / 工具顺序 / 历史改写）")


if __name__ == "__main__":
    main()
