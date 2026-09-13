# Plan：工具调用过程可视化与 Prompt 拆分优化（tool-call-visualize）

> 分支：`tool-call-visualize`（基于 `Optimize` @ c6e62b1 切出）
> 状态：方案文档，待实现会话认领执行
> 关联：《git-workflow.md》——实现开工时建议先提 issue 并将分支重命名为 `feat/<n>-tool-call-visualize`

## 1. 背景与目标

参考 GLM/ZCode 客户端的"调用检查器"交互（见需求截图）：每次 LLM 调用一个条目，展示序号、会话类型徽章（主会话）、状态徽章（正常结束/工具调用）、IN/OUT token 数、耗时、墙钟时间；可展开查看输入（用户消息 / 工具结果 + 工具名 chip + 调用 ID）与输出（思考过程 / 工具调用参数 / 助手消息）。

本分支两个目标：

1. **可视化**：在 Rove 的 Web UI 实现对齐该体验的调用检查器，但不局限于截图形态——利用 Rove 自有的多 Agent 结构，做出超越参考的观测体验。
2. **Prompt 拆分调研与落地**：观察参考产品把大量职责拆成独立的小 prompt（如初始化对话时用拼接 prompt 为会话命名），而 Rove 的 system prompt 是一整块。调研业界拆分方式，落地轻量旁路 prompt 与可组合的 system prompt 分段。

**非目标**：不做成本/命中率的业务逻辑（context-optimize 分支负责），本分支只消费其产出的数据字段；不改动 teammate 协作协议（subagent-optimize 分支负责）。

## 2. 现状分析（代码级）

### 2.1 后端有数据、没有出口

- `llm.py`：`LLMResponse` 已携带 `model / stop_reason / latency_ms / usage(input_tokens, output_tokens) / tool_calls`——单次调用的核心数据齐全，缺思考块、缓存 token（后者由 context-optimize 分支补字段）与"这次调用属于哪个 agent"的归属信息。
- `lead_agent.py::LeadAgent.run` 的每一步有清晰的阶段结构（drain inbox → 压缩 → LLM 调用 → 逐个执行 tool_use），适合在阶段边界埋点。
- `web/websocket_handler.py` / `web/agent_runner.py`：WebSocket 推流通道已存在，目前推的是消息/审批类事件，没有"调用级"事件。
- **无持久化 trace**：历史调用关掉页面即消失，无从回看、回放。

### 2.2 前端是"消息粒度"不是"调用粒度"

`frontend/src/components/`：`ChatArea / MessageItem / ToolCard / CollabPanel / TeamMonitor / ApprovalCard / Sidebar`。现有 `ToolCard` 大概是聊天流里的工具卡片，缺少：调用列表、IN/OUT token、耗时、状态徽章、思考过程展示、跨调用的筛选与回放。`types/rove.ts` 需要新增调用事件类型。

### 2.3 Prompt 是一整块

`SYSTEM_PROMPT` 单体拼装（含身份、环境声明、工具策略），另有 todo reminder / inbox / 黑板等动态注入。没有旁路小任务（会话命名、摘要），没有结构化的"请求由哪些段构成"的记录。

## 3. 调研结论

### 3.1 可视化参考产品的通用模式

来源：[Langfuse Agent Observability](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)、[Langfuse Agent Graphs](https://langfuse.com/docs/observability/features/agent-graphs)、[LangSmith trace 执行树](https://www.analyticsvidhya.com/blog/2026/06/agent-observability-with-langsmith-langfuse-arize/)、[Langfuse 开源仓库](https://github.com/langfuse/langfuse)

- **层级 span 树**：agent → LLM 调用 → 工具调用 → 子 agent，节点可展开检查输入/输出/token/成本/延迟（Langfuse 与 LangSmith 共同的核心数据模型）。
- **瀑布/时间线**：跨 agent 的并行执行一目了然，适合多 teammate 场景。
- **Agent graph**：多步工作流的图状视图。
- **性能要点**：长嵌套 trace 的树渲染需要虚拟滚动与懒加载（LangChain 博客称其为此做了 12–15x 的加载优化）——Rove 的长会话同样适用。

### 3.2 会话命名等旁路小 prompt 的业界实践

来源：[Claude Code 会话命名 feature/issue 讨论](https://github.com/anthropics/claude-code/issues/47176)、[#42726 自动命名](https://github.com/anthropics/claude-code/issues/42726)、[#67724 标题语言匹配](https://github.com/anthropics/claude-code/issues/67724)、[#35810 中途改名困扰](https://github.com/anthropics/claude-code/issues/35810)

- ChatGPT/Claude.ai/Claude Code 均在首轮之后用**独立的轻量 prompt（小模型、不占主会话上下文）**生成会话标题。
- 社区关注点值得抄：标题使用**用户对话语言**；**中途不改名**（避免破坏用户预期）；支持手动覆盖。
- "system prompt 拆非常多"的本质：把身份/环境/工具策略/动态上下文分成独立段，各段有独立的生命周期——静态段可长期缓存（与 context-optimize 的前缀稳定化天然协同），动态段走尾部注入。

## 4. 方案设计（分阶段）

### P0 数据层：CallTrace 事件模型与埋点

1. 新增 `web/call_trace.py`（或 `trace.py`）：定义调用事件结构——
   `seq, session_id, agent_name(lead/<teammate>), kind(llm_call|tool_exec), status(running|ok|error|tool_use), model, input_tokens, output_tokens, cache_read_tokens(可选), latency_ms, started_at, blocks[{type: thinking|text|tool_use|tool_result, tool_name, tool_id, preview, full_ref}]`。
2. 埋点：`lead_agent.py` 在 LLM 调用前后与每个工具执行前后发射；teammate 循环同样埋点（带 agent_name）。
3. 通道：WebSocket 新消息类型 `call_event`；大内容只传 preview（如 200 字符）+ `full_ref`，前端按需拉取全量（新增 HTTP 端点读 trace 文件），避免长输出打爆 WS。
4. 持久化：`.rove/traces/<session_id>.jsonl` 追加写（目录遵循 `paths.py` 新增 `TRACE_DIR`，不硬编码路径），支持历史会话回放。
5. 兼容 REPL：REPL 暂不展示调用列表，但 trace 落盘对两种运行时一致生效。

### P1 前端：调用检查器（对齐截图）

1. 新组件 `CallInspector.tsx`：右侧抽屉或独立 Tab；调用列表虚拟滚动 + 筛选（按工具名/状态/agent）。
2. 条目样式对齐截图：`序号 + 主会话徽章 + 状态徽章 + IN x,xxx · OUT x,xxx · 12.3s · 21:33:09`。
3. 展开块：输入（用户消息 / 工具结果 + 工具 chip + 调用 ID）/ 输出（思考过程折叠态 + 展开态 / 工具调用参数 JSON + 工具 chip + ID / 助手消息）。
4. `types/rove.ts` + `useRoveWebSocket.ts` 消费 `call_event`。

### P2 超越截图的体验（自由发挥区，按性价比排序）

1. **Token 燃烧仪表**：顶栏实时显示当前会话累计 token 与剩余上下文百分比（数据源 `estimate_messages_tokens` 与 adapter 的 `context_window`），逼近压缩水位时变色预警——把"即将被压缩"这个不可见事件可视化。
2. **运行瀑布图**：lead + teammates 的调用时间线（甘特式条），并行 spawn 一目了然；点击条目跳转对应调用详情。
3. **工具卡增强**：`edit_file`/`write_file` 渲染 diff 视图；`execute_python`/bash 长输出默认折叠；错误结果红色高亮 + 重试标记。
4. **回放模式**：从 `.rove/traces/` 加载历史会话，带 scrubber 逐步回放——复盘失败 run 的利器。
5. **"这次调用为什么发生"**：展开某次调用时显示该请求的构成（system 各段、注入的 reminder/inbox/黑板消息、工具数量），让 prompt 结构透明。依赖 P3 的分段记录。
6. **导出**：单次调用复制 JSON；整个会话导出 Markdown 复盘。
7. 成本/命中率徽章：消费 context-optimize 分支的 Usage 扩展字段（字段未合入前留占位）。

### P3 Prompt 拆分落地

1. **TitleService（会话命名旁路）**：首轮回复结束后，用独立小 prompt（"根据以下用户请求生成 ≤12 字的中文标题，直接输出标题"）+ 可配的廉价模型调用一次；结果写会话元数据，Sidebar 显示；支持 `/title` 手动改；**生成后不再自动改名**。REPL 与 Web 共用。
2. **SYSTEM_PROMPT 分段化**：拆为 `IDENTITY / ENVIRONMENT / TOOL_POLICY / MEMORY_POLICY` 等常量段 + 明确的拼装函数；拼装结果与顺序保持逐字节稳定（协同 context-optimize 的缓存前提），并在 CallTrace 的 `request_composition` 里记录本次用了哪些段。
3. **预留旁路任务框架**：`SidePromptService`（title 是第一个实例），后续可挂"会话结束摘要""错误归因"等小任务，统一走廉价模型 + 独立短 prompt，不污染主历史。

## 5. 验收标准

- [ ] 截图中的核心信息全部可实现：序号、双徽章、IN/OUT、耗时、墙钟时间、四类内容块、工具 chip + 调用 ID。
- [ ] trace 文件按会话落盘，重开页面可回放历史调用；WS 只传 preview 不被长输出卡死。
- [ ] TitleService 首轮后产出中文标题且只生成一次；tsc --noEmit 通过。
- [ ] 新增埋点与 trace 模块有单测；全量 pytest 通过。

## 6. 与其他分支的冲突面协调

| 冲突点 | 对方分支 | 协调建议 |
|---|---|---|
| `llm.py` Usage 字段（缓存三元组） | context-optimize | 对方先合，本分支消费其字段；未合入前本分支用占位字段 |
| `lead_agent.py` 埋点 vs 注入改造 | context-optimize / subagent-optimize | 埋点集中在 run() 的调用边界处，改动面收窄到一个函数；建议最后合并本分支并处理三向冲突 |
| `types/rove.ts` / `App.tsx` | 无直接竞争（其他两分支不动前端） | 本分支独占前端 |
| `paths.py` 新增 TRACE_DIR | 无直接竞争 | — |

## 7. 参考资料

- [Langfuse：AI Agent Observability](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse) / [Agent Graphs](https://langfuse.com/docs/observability/features/agent-graphs) / [GitHub](https://github.com/langfuse/langfuse)
- [Agent Observability: LangSmith vs Langfuse vs Arize](https://www.analyticsvidhya.com/blog/2026/06/agent-observability-with-langsmith-langfuse-arize/)
- [Claude Code 会话命名讨论](https://github.com/anthropics/claude-code/issues/47176) / [自动命名提案](https://github.com/anthropics/claude-code/issues/42726) / [标题语言匹配](https://github.com/anthropics/claude-code/issues/67724) / [中途改名困扰](https://github.com/anthropics/claude-code/issues/35810)
