# Plan：优化默认 Single Agent + SubAgent 模式（subagent-optimize）

> 分支：`subagent-optimize`（基于 `Optimize` @ c6e62b1 切出；分支名沿用前两个计划的扁平命名风格）
> 状态：P0–P4 已实现（harness 就绪），评测结果待真实跑批回填
> 关联：《git-workflow.md》——实现开工时建议先提 issue 并将分支重命名为 `feat/<n>-subagent-optimize`

## 1. 背景与目标

Rove 当前的多 Agent 形态是"lead 单循环 + 常驻异步 Teammate"：teammate 是后台守护线程里各自跑完整 LLM 循环的长驻协作者，靠文件任务板 + JSONL 消息总线协作。这套设计适合**长时并行作业**，但缺一个业界已验证的核心模式——**一次性 Subagent（Task 工具模式）**：派发一个有边界的子任务 → 子 agent 在**独立上下文窗口**里自己烧 token 去探索 → 只把浓缩后的结论同步返回给 lead。

对 lead 的上下文经济性而言，这是最有价值的缺件：目前探索类工作（翻文件、grep、试运行）产生的全部中间输出都流入 lead 历史，之后要靠 L2 脱水/L1 剪枝在下游擦地板；有了 Subagent，噪音在源头就被隔离在子上下文里。

**目标**：

1. 新增 `dispatch_subagent` 一次性派发工具，lead 上下文只增加最终报告；
2. 修补现有 Teammate 模式的健壮性缺口（崩溃无感知、无取消、结束无总结）；
3. 打通模型路由：探索类 Subagent 可用廉价模型，lead 保持主力模型。

**非目标**：不重写消息总线/任务板的存储格式；不做 teammate 的递归 spawn（维持禁止）；不改动压缩管线（context-optimize 分支负责）。

## 2. 现状分析（代码级）

### 2.1 Teammate 模式（tools/agent_teams.py）

- `TeammateManger`（拼写如此，本分支顺手修正为 `TeammateManager`，全局 grep 改名）负责 spawn：每个 teammate 一个 daemon 线程跑独立 Anthropic 循环，工具是 `_teammate_registry` 受限集（文件工具 + `send_message`/`read_inbox`/`idle`/协议工具 + `scan_tasks`/`claim_task`），**不能**创建/完结任务、不能再 spawn。
- 收件箱 5 秒轮询（`read_inbox` drain 语义），`IDLE_TIMEOUT` 60 秒空闲自杀（`agent_teams.py:104`）。
- 健壮性缺口：
  - **崩溃无感知**：线程异常退出后 lead 不知道，任务板上的 owner 悬空；只有"空闲"有超时，"死亡"没有。
  - **无取消机制**：用户 `Ctrl+C` 或 lead 想中止某个 teammate 时没有信号通道。
  - **结束无总结**：teammate 空闲自杀时最后一刻的工作成果不会主动汇报给 lead。
  - **无轮数/token 预算**：teammate 循环理论上可以无限跑下去烧钱。

### 2.2 已有可复用的机制

- `run_background`/`check_background` 工具（lead 注册表）：后台任务 + 轮询通知的成熟模式，一次性 Subagent 的异步变体可直接复用该通知管道。
- `memory.py` 注入预算、`llm.py` 的 `LLMRequest/Response`（含 usage/latency）可直接用于子代理的预算统计与报告生成。
- `permissions.py` 新工具默认 ASK 的兜底规则已存在，`dispatch_subagent` 注册后自动落入安全默认。

## 3. 调研结论

来源：[Claude Code SDK Subagents 官方文档](https://code.claude.com/docs/en/agent-sdk/subagents)、[Task 工具如何分发工作](https://medium.com/@neonmaxima/claude-code-subagents-how-the-task-tool-actually-distributes-work-e5fe19f48584)、[Context Management with Subagents](https://www.richsnapp.com/article/2025/10-05-context-management-with-subagents-in-claude-code)、[Context Engineering 101（orchestrator 模式）](https://newsletter.victordibia.com/p/context-engineering-101-how-agents)、[Subagent 设计指南](https://claudefa.st/blog/guide/agents/sub-agent-design)

- **Task 模式的四个要素**：① 全新独立上下文（不继承主会话历史，只拿到 orchestrator 显式传给它的内容）；② 有界执行（轮数/工具范围受限）；③ **只回传浓缩结论**——"relevant information is returned to the parent"，这是上下文隔离的意义所在；④ 可并行多实例。
- **Orchestrator-worker 是上下文工程的标准模式**：协调者把重活外包，每个 worker 在自己的窗口里做重 lifting，返回 condensed results。
- **已知短板（HN 讨论与社区复盘）**：子代理拿不到主会话的隐式上下文，任务简报质量决定成败；"context wrangling"依然存在——所以任务简报模板和报告格式必须精心设计，而不是随手传一句话。
- **与 Rove Teammate 的定位区分**：Claude Code 的 subagent ≈ 函数调用（同步语义、一次一报）；Rove teammate ≈ 常驻同事（异步消息、长时协作）。两者互补，不是替代。

## 4. 方案设计（分阶段）

### P0 `dispatch_subagent` 一次性派发（核心交付）

1. 新增 `tools/subagent.py`：`build_subagent_tool()` 产出 `dispatch_subagent` 工具，参数：
   - `objective`（必须）：要完成什么、验收是什么；
   - `context`（必须）：lead 主动摘录的相关事实/文件路径/约束（**简报模板**写进工具描述，强制 lead 给足上下文）；
   - `max_turns`（默认 12）、`token_budget`（默认 100k，超限即终止并返回已得结论）；
   - `model`（可选）：覆盖默认主力模型，探索类任务建议廉价模型（依赖 P3 路由）。
2. 执行体：daemon 线程内独立 LLM 循环 + **专用受限注册表**（读文件/写文件/edit/glob/grep + `execute_python`；**不含** team 工具、memory 写工具、web_search 先不给——防递归、防越权、防外呼）。
3. 返回结构化报告作为 tool_result 进 lead 历史：`结论摘要（≤2000字）/ 关键文件与证据 / 遇到的障碍 / usage 统计（turns, tokens, cost）`。报告超长时截断并附全量路径（落 `.rove/tool-results/`，复用既有持久化）。
4. 同步语义：P0 阶段阻塞等待（lead 一次只跑 1 个）；并发上限常量先留好。
5. 注册进 `build_default_registry`，权限默认 ASK。

### P1 后台化与进度

1. `dispatch_subagent(run_in_background=true)`：复用 `run_background`/`check_background` 通知管道，lead 可继续其他工作。
2. 进度心跳：子代理每完成一轮向 lead inbox 写一条轻量进度（类型 `progress`，`message_bus.py` 扩展合法 msg 类型）；`check_background` 可读到。
3. 前端 TeamMonitor 显示 subagent 运行态（数据经 websocket；详细可视化归 tool-call-visualize 分支）。

### P2 Teammate 健壮性修补

1. **心跳与死亡感知**：teammate 每轮刷新 `.team/heartbeat/<name>`（mtime 即心跳）；`list_teammates` 把"超过 2×轮询周期无心跳且线程已退出"标记为 `crashed`，lead 可据此 reclaim 任务板 owner。
2. **取消协议**：`tools/protocol.py` 扩展 `cancel` 消息类型；teammate 在轮询间隔检查取消标志，收到后收尾退出并在退出前发总结。
3. **优雅结束**：空闲自杀或被取消时，最后动作必须是向 lead 发一条工作总结（做了什么/卡在哪/交接建议）。
4. **预算兜底**：teammate 增加 `max_total_turns`（默认 40），超限走优雅结束路径。
5. 线程异常统一捕获：循环体 try/except，异常写 lead inbox + 标记 crashed，不允许静默死亡。

### P3 模型路由

1. `llm_adapters.py` 增加适配器工厂：按 model id 缓存 client 实例（现每个 adapter 只持有一个 lazy client，扩展为 per-model 注册表）。
2. 配置：`LLM_FAST_MODEL_ID`（缺省回落主模型）；`dispatch_subagent` 与 TitleService 类旁路任务（若 tool-call-visualize 分支已落地）默认用 fast model。
3. 成本核算按模型分别计价（与 context-optimize 的定价表对齐字段口径）。

### P4 效果评测（最小闭环）

固定 10 个探索/修改任务集，对比三种跑法：纯 lead、lead+teammate、lead+dispatch_subagent——记录成功率、lead 上下文峰值 token、总成本、墙钟时间，结果写入本文件附录。这是后续一切 agent 架构决策的基线数据。

## 5. 验收标准

- [ ] `dispatch_subagent` 派发一个文件探索任务后，lead 历史只新增最终报告（可用 trace/日志验证中间轮次不进 lead 上下文）。
- [ ] `max_turns`/`token_budget` 超限能终止并返回部分结论；工具注册表确实不含 team/memory/web 工具（注册表快照单测）。
- [ ] teammate 线程被 kill -9 模拟崩溃后，`list_teammates` 显示 crashed 且任务可被 reclaim；取消协议与结束总结有集成测试。
- [ ] `LLM_FAST_MODEL_ID` 未配置时行为与现在完全一致（回归保障）。
- [ ] 全量 pytest 通过；`TeammateManger → TeammateManager` 改名后全局无残留引用。

## 6. 与其他分支的冲突面协调

| 冲突点 | 对方分支 | 协调建议 |
|---|---|---|
| `lead_agent.py`（注册表注入 vs 尾部注入改造） | context-optimize | 注入逻辑对方会重构成 `_inject_synthetic()`；本分支只动注册表构建与 run() 的工具执行段，合并时以对方注入框架为基 |
| `llm_adapters.py`（工厂化 vs usage 字段扩展） | context-optimize | 对方改 `_build_response`/Usage，本分支改 client 创建与工厂；同一文件不同区域，rebase 冲突小 |
| `tools_setup.py` 注册表 | tool-call-visualize（不动）/ context-optimize（排序导出） | 对方排序导出与本分支新增工具不冲突，后合方 rebase |
| `message_bus.py` 新 msg 类型 `progress` | tool-call-visualize 埋点不改总线 | 无竞争 |

**建议合并顺序**：context-optimize → subagent-optimize → tool-call-visualize（数据字段先定、行为改动次之、前端消费最后）。

## 7. 参考资料

- [Claude Code SDK Subagents 官方文档](https://code.claude.com/docs/en/agent-sdk/subagents)
- [Claude Code Subagents: How the Task Tool Actually Distributes Work](https://medium.com/@neonmaxima/claude-code-subagents-how-the-task-tool-actually-distributes-work-e5fe19f48584)
- [Context Management with Subagents in Claude Code](https://www.richsnapp.com/article/2025/10-05-context-management-with-subagents-in-claude-code)
- [Context Engineering 101: How Agents Manage Context](https://newsletter.victordibia.com/p/context-engineering-101-how-agents)
- [Claude Code Sub-Agents: Split Tasks Across Experts](https://claudefa.st/blog/guide/agents/sub-agent-design)
- [HN：subagent 与 context wrangling 的批评视角](https://news.ycombinator.com/item?id=45181577)

## 8. 附录：P4 效果评测（harness 就绪，结果待真实跑批）

评测 harness：`src/rove/benchmarks/subagent_eval.py` + 任务集 `tasks_subagent_eval.jsonl`（5 探索 + 5 修改，
setup/verify 自包含）。三种跑法（纯 lead / lead+teammate / lead+dispatch_subagent）各跑同一任务集，
记录成功率、lead 上下文峰值 token、总成本（按模型计价）、墙钟时间。

执行（需 `.env` 配置 `LLM_API_KEY`，全程无人值守，建议预留 30–60 分钟与相应 API 预算）：

```bash
python -m rove.benchmarks.subagent_eval run \
    --tasks src/rove/benchmarks/tasks_subagent_eval.jsonl \
    --out .rove/eval/results.json \
    --append-plan plans/subagent-optimize.md
```

跑批结束后对比表会自动追加到本附录下方。可选：配置 `LLM_FAST_MODEL_ID` 后重跑一轮，
验证探索类子代理路由廉价模型的成本/质量差异。

### 待回填结果

| 跑法 | 任务数 | 成功率 | lead 上下文峰值 (avg) | 总成本 USD | 墙钟 avg (s) |
|---|---|---|---|---|---|
| 纯 lead | — | 待跑批 | — | — | — |
| lead+teammate | — | 待跑批 | — | — | — |
| lead+dispatch_subagent | — | 待跑批 | — | — | — |
