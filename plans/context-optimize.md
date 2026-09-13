# Plan：提高模型调用命中率，降低推理成本（context-optimize）

> 分支：`context-optimize`（基于 `Optimize` @ c6e62b1 切出）
> 状态：方案文档，待实现会话认领执行
> 关联：《git-workflow.md》——实现开工时建议先提 issue 并将分支重命名为 `feat/<n>-context-optimize`

## 1. 背景与目标

"命中率"在本方案中包含两层，二者都直接作用于成本：

1. **API 前缀缓存命中率**：同一会话的多轮调用之间，请求前缀（system prompt + 工具定义 + 历史消息）保持字节级一致的部分可以命中服务端缓存，命中部分按大幅折扣计价。这是 Agent 产品成本的大头——长会话中每轮都重复上传几乎全部历史。
2. **调用一次成功率**：模型发起的 tool_use 因 schema 错误、权限拒绝、参数不当而失败重试，每次失败都是一轮完整的 LLM 调用浪费。

**目标**：稳态多轮会话的前缀缓存命中率 ≥ 70%（以服务端返回的缓存命中 token 口径统计）；每次调用的缓存命中/未命中 token 可观测、可汇总、可换算成本；工具调用失败重试率显著下降且失败原因可归因。

**非目标**：不改动压缩管线的水位与分层策略本身（L1–L4 的触发逻辑维持现状，本方案只度量它们对缓存的破坏并做缓存友好的注入方式调整）。

## 2. 现状分析（代码级）

### 2.1 缓存指标"读了但扔了"

`src/rove/llm_adapters.py:146-151`（`_build_response`）：已经从 Anthropic 兼容响应中读取 `cache_read_input_tokens` 和 `cache_creation_input_tokens`，但随即合并进 `total_input`，原始字段丢弃。结果是：

- `Usage`（`src/rove/llm.py`）只有 `input_tokens/output_tokens`，命中率无处可算；
- 上层（lead、Web UI、REPL）完全看不到缓存是否生效。

### 2.2 从未主动请求缓存

`llm_adapters.py:162-199`（`complete`/`stream`）：调用 Anthropic 兼容端点时不传任何 `cache_control` 断点。对 Anthropic 官方端点而言，没有显式断点就没有缓存（除非启用自动缓存）；对智谱 GLM 端点则是隐式缓存自动生效（见 §3.2）。

### 2.3 前缀不稳定：system prompt 每回合变化

工作记忆黑板（`memory.py` 的 `current_goal/findings/focused_files/scratchpad`）**每回合渲染进 System Prompt**（AGENTS.md 已确认该设计）。黑板内容在活跃工作中频繁变化，一旦变化，整个请求前缀（system + tools + 全部历史）的缓存即刻全部失效。这是当前命中率的最大杀手。

### 2.4 历史的原地改写

压缩管线（`compaction/compaction_layers.py`）在水位之上对历史消息**原地**脱水（L2）、剪枝（L1）、重写（L4）。任何对已有消息的改写都会从改动点起作废缓存。缓解因素：这些操作只发生在水位（10%/20%/40%）之上，低频；L4 重写是结构性突变，本就不可避免。注意：Optimize 工作树上有一处**未提交**的 L4 调参（`SUMMARY_CHAR_BUDGET` 80k→300k、tail 6→10，`compaction_layers.py:41/384`），本分支实现前需先决定该改动的去留（提交或丢弃），避免混入。

### 2.5 失败调用的浪费源

- tool_use 无执行前 schema 校验，格式错误直接进 handler 异常路径；
- 已退役工具名（`task_create`/`task_update` → `todo`）无别名映射，模型沿用旧名即失败一轮；
- token 估算偏差触发 `reactive_compact` 重试（`MAX_REACTIVE_RETRIES`），每次重试是完整一轮调用；
- 必然被拒的调用（工作区边界外的写、硬 deny 子串）没有被前置告知模型，浪费轮次。

## 3. 调研结论

### 3.1 Anthropic 显式缓存机制

来源：[Prompt caching – Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)、[Claude Code prompt caching explainer](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code)、[5min vs 1h TTL 对比](https://www.padiso.co/blog/prompt-caching-system-prompts-5-minute-vs-1-hour-ttl/)

- 缓存按**请求前缀字节级一致**匹配；前缀中任何一处变化，其后所有内容全价重算。
- 显式 `cache_control: {"type": "ephemeral"}` 断点最多 **4 个**，可打在 system、tools、消息块上；也有自动模式（打在最后一个可缓存块）。
- TTL 默认 5 分钟，**命中免费续期**；可加价选 1 小时 TTL。写缓存 +25%，读缓存仅 10% 基价——长前缀 agent 负载理论节省可达 90%。
- 最佳实践：**稳定内容在前、易变内容在后**；tools + system prompt 是 agent 场景最敏感的两层。
- 学术佐证：长程 agent 工作负载中缓存可降 41–80% 成本、13–31% 延迟（[arXiv 2601.06007](https://arxiv.org/html/2601.06007v2)）。

### 3.2 智谱 GLM（当前 LLM 后端）的隐式缓存

来源：[智谱上下文缓存文档](https://docs.bigmodel.cn/cn/guide/capabilities/cache)、[智谱定价页](https://docs.bigmodel.cn/cn/guide/start/pricing)、[Z.AI 英文文档](https://docs.z.ai/guides/capabilities/cache)

- GLM 走**隐式（自动）缓存**：平台自动识别重复前缀，无需传 cache_control，命中部分按约半价计费；计费公式 = 未命中输入 + 缓存命中 + 输出 + 缓存存储。
- 前缀匹配规则与 Anthropic 一致：公共前缀完全相同命中率最高，**格式差异也会影响命中**；缓存有时效性，过期重算。
- 注意：缓存计费**不含 GLM Coding Plan 套餐**——若走套餐额度，降本收益体现为额度消耗变慢而非账单变少，度量口径要区分。
- 实现会话需先验证：Rove 使用的 Anthropic 兼容端点返回的 usage 中缓存字段的具体形态（`cache_read_input_tokens` 或 `prompt_tokens_details.cached_tokens`），以及传入 `cache_control` 是否报错（决定显式断点是否保留为可选项）。

### 3.3 可借鉴的工程实践

- LangChain 的 `anthropicPromptCachingMiddleware`：自动放置断点的中间件模式，可参考其断点策略。
- Claude Code 社区复盘（[TTL 过期导致全量重算](https://github.com/anthropics/claude-code/issues/46829)）：空闲超过 TTL 后的下一轮必然全价，设计上应把"必然失效点"集中到可控位置（如 L4 重写后），而不是散落在每轮。

## 4. 方案设计（分阶段）

### P0 可观测性（先能测量）

1. `Usage`（`llm.py`）增加 `cache_read_tokens`、`cache_creation_tokens` 字段；`_build_response` 保留原始值不再合并丢弃；同时兼容 GLM 的 `cached_tokens` 形态（在适配器内归一化）。
2. 每次调用后记录：`model, input, output, cache_read, cache_creation, latency, hit_rate = cache_read / (input + cache_read + cache_creation)`。
3. 落盘：`.rove/sessions/` 会话元数据追加逐调用 JSONL（复用 SESSIONS_DIR，不新造目录）。
4. REPL：`/stats` 命令 + 会话结束时的成本小结（总 token、命中率、按定价表估算成本）。定价表先做成常量字典（per-model 单价），后续可环境变量覆盖。
5. Web 端最小展示：状态栏显示命中率百分比（详细可视化归 tool-call-visualize 分支，本分支只保证数据可用）。

### P1 前缀稳定化（收益最大的一刀）

1. **黑板迁出 system prompt**：`memory.py` 的黑板渲染从系统提示拼装中移除，改为每回合作为**尾部合成 user 消息**注入（与现有 `<reminder>`/`<inbox>` 注入同一机制，见 `lead_agent.py` 的注入点）。注意与 L1 的协同：L1 的 `_is_user_anchor` 已把合成注入排除在锚点之外，黑板消息需打上同样的合成标记避免被当作真实用户输入锚定。
2. **工具定义顺序确定性**：`build_default_registry`（`tools/tools_setup.py`）导出工具 schema 时按名称排序，保证逐字节稳定。
3. **消息序列化的确定性**：检查 `_convert_messages` 与工具结果序列化无时间戳/随机 id 混入前缀。
4. 度量验证：同一任务脚本（如"读取文件并总结"固定脚本）跑 10 轮，改造前后命中率对比，作为 P1 的验收证据。

### P2 显式断点（provider 可配）

1. 新增配置 `LLM_CACHE_MODE = auto | explicit | off`（默认 auto）。
2. `explicit` 模式下（仅当 base_url 为 Anthropic 官方或已验证支持 cache_control 的端点）：断点 1 打在 system + tools 之后，断点 2 打在历史倒数第 K 条消息（K 取保留窗口边缘，使"被 L2 脱水区"落在断点之后）。
3. `auto` 模式（GLM 隐式缓存）：不传断点，只做 P1 的前缀稳定化。
4. 开发环境验证 GLM 端点对 `cache_control` 的容忍度，结论写入本文件附录。

### P3 调用一次成功率

1. `tool_registry.py::execute` 执行前按工具 schema 校验参数；校验失败返回**结构化错误**（哪个字段、期望什么、示例），作为 tool_result 回喂让模型自愈，不计入"失败轮"。
2. 工具名别名表：`task_create/task_update/task_list → todo` 等退役名映射，命中别名时执行改名后的工具并在结果中提示模型使用新名。
3. 失败经验入长期记忆：同类失败（工具名+失败原因类别）第二次出现时，在尾部注入一条"此前该工具因 X 失败，建议 Y"提示。
4. 前置规则声明（静态，进 system prompt 不破坏缓存）：工作区边界、硬 deny 子串清单的"不要尝试"表述，减少注定失败的调用。

### P4 重试浪费收敛

`reactive_compact` 触发后，先应用压缩再重试的逻辑已有；本阶段补充：用上一轮 API 真实回报的 token 数校正估算参数（`cjk_token_ratio` 校准已存在，扩展为也校准"水位触发误差"），目标把 reactive 重试次数降到每会话 ≤1 次。

## 5. 验收标准

- [ ] 稳态 10 轮以上会话命中率 ≥70%（GLM 端点实测，附对比数据）。
- [ ] `/stats` 与会话结束小结展示总 token / 命中率 / 估算成本；Web 状态栏显示命中率。
- [ ] 黑板已迁出 system prompt，L1 不再将其锚定；compaction 既有测试全绿并新增黑板注入的边界测试。
- [ ] 别名映射与 schema 预校验有单测；人为构造错误 tool_use 不再整轮报废。
- [ ] 全量 `pytest tests/ -q` 通过，前端 `tsc --noEmit` 通过（若触及状态栏）。

## 6. 与其他分支的冲突面协调

| 冲突点 | 对方分支 | 协调建议 |
|---|---|---|
| `llm.py` Usage 字段扩展 | tool-call-visualize | 本分支先行定字段（含缓存三元组），可视化分支消费；合并顺序：本分支先合 |
| `lead_agent.py` 注入点改造 | subagent-optimize | 双方都在动 lead 的尾部注入逻辑；约定注入统一走 `lead_agent.py` 内单一 `_inject_synthetic()` 帮助函数，各自只加自己的消息类型 |
| `memory.py` 黑板渲染 | 无直接竞争 | — |
| 模型路由（per-call model） | subagent-optimize | 路由机制归 subagent 分支，本分支只做成本核算按模型计价 |

## 7. 参考资料

- [Prompt caching – Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [智谱上下文缓存](https://docs.bigmodel.cn/cn/guide/capabilities/cache) / [智谱定价](https://docs.bigmodel.cn/cn/guide/start/pricing) / [Z.AI cache](https://docs.z.ai/guides/capabilities/cache)
- [An Evaluation of Prompt Caching for Long-Horizon Agentic Workloads (arXiv)](https://arxiv.org/html/2601.06007v2)
- [Claude Code prompt caching 实解](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code)
- [5min vs 1h TTL](https://www.padiso.co/blog/prompt-caching-system-prompts-5-minute-vs-1-hour-ttl/) / [TTL 过期重算 issue](https://github.com/anthropics/claude-code/issues/46829)
