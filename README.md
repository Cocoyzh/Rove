# Rove

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white) ![LLM: Anthropic](https://img.shields.io/badge/LLM-Anthropic-000000)

> 多智能体编码框架（Multi-Agent Coding Harness）· 跑在终端里的自治编码系统

Rove 在终端里运行一个主智能体（Lead），通过工具编排规划、执行并验证编码任务；需要并行时，派生自治的 Teammate 到后台线程协作。工具调用经过权限门控，长对话由四层上下文压缩兜底。Python + Anthropic API 实现，数据落在本地文件，不依赖外部服务。

---

## 核心特性

### 🤝 多智能体协作

- Lead 通过 `spawn_teammate` 派发自治队友，每个队友在独立后台线程中跑自己的 LLM 循环
- **任务看板**（`.tasks/`）：Lead 用 `task_create` 发布任务，队友 `scan_tasks` / `claim_task` **原子认领**（加锁防重复），完成后由 Lead `task_update` 标记完成
- **消息总线**（`.team/inbox/`）：`send_message` / `read_inbox` 全异步通信，天然落盘、可排查
- **协议系统**：`shutdown`、`plan_review` 等结构化请求需对方显式 approve / reject
- 队友只持有受限工具集：能认领任务、读写文件、收发消息，但不能创建 / 完成任务

### 🛡️ 权限门控与安全

- 每次工具调用先过 `PermissionPolicy`，三级决策：**ALLOW**（只读类直接放行）/ **ASK**（需用户确认）/ **DENY**（直接拦截）
- **破坏性命令硬拒**：`rm -rf /`、`sudo`、`shutdown`、`mkfs`、`dd` 等直接拦截
- **路径逃逸防护**：文件读写被锁定在 workspace 内，`../` 越界即拒绝
- 交互式审批：`y` 放行一次 / `s` 本次会话内同类操作免审批 / `N` 拒绝

### 📦 四层上下文压缩

对话上下文会随运行时间不断累积，最终触发 API 的 `prompt_too_long`。每次 LLM 调用前都会先跑四层压缩流水线（前 3 层零 API 调用）：

- **L3** 超大 tool 结果落盘到 `.rove/tool-results/`，仅保留预览
- **L1** 中段消息裁剪（保持 tool_use / tool_result 配对完整）
- **L2** 旧工具结果折叠为占位符
- **L4** 全量对话存档后由 LLM 生成摘要
- 压缩前完整对话存档到 `.rove/transcripts/`，不丢信息
- 触发 `prompt_too_long` 时启用应急压缩并自动重试

---

## 技术栈

Python ≥ 3.10 · Anthropic Messages API（Tool Use）· rich · 线程级并行（daemon thread）· 纯文件持久化

---

## 快速开始

```bash
# 1. 安装（可编辑模式）
pip install -e .

# 2. 配置环境变量（仓库根目录新建 .env）
MODEL_ID=你的模型名
ANTHROPIC_BASE_URL=兼容 Anthropic 接口的端点

# 3. 启动 REPL
rove

# 4. 在 REPL 里
#    > 帮我用二分查找修这个 bug，写完跑一下测试
#    /team   查看当前队友
#    q       退出
```

---

## 目录结构

```
src/rove/
├── main.py                    # REPL 入口：装配工具集、循环读输入
├── agent_loop.py              # Lead 的 LLM 循环：注入收件箱/后台通知、执行工具调用
├── tool_registry.py           # 工具注册中心 + 统一执行（先过权限判定）
├── permissions.py             # ALLOW / ASK / DENY 三级权限门控
├── llm_client.py              # Anthropic 客户端 + 系统提示词
├── task_manager.py            # 任务看板：JSON 持久化，原子认领
├── paths.py                   # 所有项目/运行时目录的唯一事实来源
├── compaction/
│   └── compaction_layers.py   # 四层上下文压缩流水线 + 应急压缩
└── tools/
    ├── agent_teams.py         # Teammate 派生、受限工具集、生命周期管理
    ├── message_bus.py         # 文件消息总线（异步、落盘）
    ├── protocol.py            # 结构化协议：shutdown / plan_review
    ├── file_tools.py          # 文件读写工具
    ├── background.py          # 后台 shell 任务
    ├── task_tool.py           # 任务看板工具（task_create/update/scan/claim）
    └── execute_python.py      # 执行 Python 代码
```

