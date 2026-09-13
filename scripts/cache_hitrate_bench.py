"""P1 验收：前缀缓存命中率度量脚本（plans/context-optimize.md §4 P1-4）。

用法（需与 main.py 相同的 LLM_* 环境变量，GLM/Anthropic 兼容端点均可）：

    PYTHONPATH=src python scripts/cache_hitrate_bench.py [轮数，默认 10]

以固定任务脚本驱动真实 LeadAgent 多轮会话（读文件 → 归纳 → 追问的稳态
工作负载），逐调用命中率来自 usage_ledger 台账。

改造前后对比：分别在基线分支与本分支上运行本脚本（脚本不依赖本分支
新增逻辑之外的行为），对比输出的聚合命中率即可作为 P1 验收证据。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from rove.lead_agent import LeadAgent  # noqa: E402
from rove.llm_adapters import AnthropicLLMAdapter  # noqa: E402
from rove.paths import SKILL_DIR, TASK_DIR, TEAM_DIR  # noqa: E402
from rove.skill_loader import SkillLoader  # noqa: E402
from rove.task_manager import TaskManager  # noqa: E402
from rove.tools.agent_teams import TeammateManger  # noqa: E402
from rove.tools.tools_setup import build_default_registry  # noqa: E402
from rove.usage_ledger import LEDGER  # noqa: E402

load_dotenv()

# 固定任务脚本：模拟"读取文件并总结"的稳态多轮会话，逐轮在既有历史上追加
FIXED_QUERIES = [
    "读取 README.md，用一句话概括这个项目是做什么的。",
    "基于上一步，列出项目的主要技术栈，不要重复读文件。",
    "读取 src/rove/llm.py，列出其中定义的类名。",
    "对上一步列出的每个类，用一句话说明其职责。",
    "读取 src/rove/usage_ledger.py，说明台账落盘到哪个文件。",
    "结合前面的发现，用三句话总结本项目的上下文管理设计。",
    "读取 pyproject.toml，报告项目的 Python 版本要求。",
    "检查前面总结的设计与 pyproject 声明的依赖是否一致，给出结论。",
    "把前八轮的关键结论整理成一份不超过 5 条的要点清单。",
    "最后：用一段话向新同事介绍这个仓库，复用前面所有结论，不要重新读文件。",
]


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

    registry = build_default_registry(
        skill_loader=SkillLoader(SKILL_DIR),
        task_manager=TaskManager(TASK_DIR),
        team_manager=TeammateManger(TEAM_DIR, TaskManager(TASK_DIR), llm),
    )
    lead = LeadAgent(llm, registry, 50)

    print(f"model={llm.model} rounds={len(queries)}\n")
    start = len(LEDGER.records)
    for i, query in enumerate(queries, 1):
        print(f"--- Round {i}/{len(queries)} ---")
        lead.run(query)
        recs = LEDGER.records[start:]
        start = len(LEDGER.records)
        for r in recs:
            print(f"  call: in={r['input_tokens']} cache_read={r['cache_read_tokens']} "
                  f"cache_creation={r['cache_creation_tokens']} hit_rate={r['hit_rate']:.2%}")

    s = LEDGER.summary()
    print("\n===== Aggregate (P1 Acceptance Metric) =====")
    print(f"calls                {s['calls']}")
    print(f"input(uncached)      {s['input_tokens']}")
    print(f"cache_read           {s['cache_read_tokens']}")
    print(f"cache_creation       {s['cache_creation_tokens']}")
    print(f"hit_rate             {s['hit_rate']:.2%}   (acceptance criteria: >= 70% after reaching steady state)")
    print(f"estimated_cost_usd   {s['estimated_cost_usd']:.4f}")
    if s["calls"] >= 3 and s["hit_rate"] >= 0.7:
        print("PASS")
    elif s["calls"] >= 3:
        print("BELOW TARGET: Check whether the prefix still changes every turn (system prompt / tool order / historical rewriting).")


if __name__ == "__main__":
    main()
