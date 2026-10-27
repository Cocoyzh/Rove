<div align="center">

<img src="assets/logo/logo.svg" width="150" alt="Rove logo" />

# Rove

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![LLM Anthropic](https://img.shields.io/badge/LLM-Anthropic-191919)
![Version 0.1.0](https://img.shields.io/badge/version-0.1.0-4C1)

</div>

Rove 是一个运行在终端中的多智能体编码框架。它由 Lead Agent 负责任务规划、工具调用和结果验证，并可派生只读子代理并行探索，或按需启用后台 Teammate。

项目内置权限门控、任务协作、异步消息和长上下文管理。任务状态与运行数据保存在本地文件系统中，无需额外部署数据库或消息队列。

## 核心特性

### Agent 协作

- Lead Agent 负责任务拆分、工具编排和最终响应。
- 一次性只读子代理（`dispatch_subagent`）在独立上下文里探索，只回传结论；写操作集中在 Lead。
- Teammate 在独立后台线程中运行，并使用受限工具集处理子任务（实验功能，设置 `ROVE_TEAMMATES=1` 开启）。
- 文件化任务看板支持任务创建、扫描、原子认领和状态更新。
- 异步消息总线用于 Lead 与 Teammate 之间的持久化通信。
- `shutdown`、`plan_review` 等结构化协议支持显式确认或拒绝。

### 工具与权限

- 所有工具通过统一的 `ToolRegistry` 注册和执行。
- `PermissionPolicy` 将操作划分为 `ALLOW`、`ASK` 和 `DENY`，支持 `strict`、`workspace`（默认）、`full_access` 三档审批模式。
- 高风险命令会被直接拒绝，只读命令经分类器识别后免审批，其余敏感操作需要交互确认。
- 文件读写限制在 workspace 范围内，防止路径逃逸；bash 按命令类型分级超时（只读 30s，默认 120s，最长 600s）。
- 支持文件操作、后台命令、Python 执行、联网搜索（`web_search`）、任务管理和 Skill 加载。
- `read_file` 按文件头识别类型，可读取 RTF、PDF（需另装 `pypdf`）；`read_notebook` 读取 Jupyter Notebook。

### 模型抽象

- 使用统一的 `Message` 和 `ToolCall` 表示对话与工具调用。
- `LLMRequest` / `LLMResponse` 隔离 Agent 运行时与供应商 SDK。
- `BaseLLMAdapter` 定义模型后端接口，当前提供 `AnthropicLLMAdapter`。
- Lead Agent、Teammate 和上下文管理器通过依赖注入共享模型后端。
- 可通过 `LLM_FAST_MODEL_ID` 为子代理指定更便宜的模型。

### 长会话与记忆

- REPL 中的连续请求共享同一个 Lead Agent 会话。
- 四层压缩流水线控制消息数量和工具输出体积。
- 压缩前自动保存完整对话，必要时可追溯原始内容。
- API 返回上下文溢出错误时，自动执行应急压缩并重试。
- 工作记忆黑板（目标、发现、关注文件）每轮注入系统提示，不受压缩影响；长期记忆按关键词检索，压缩摘要自动写入情景记忆。

## 快速开始

### 环境要求

- Python 3.10 或更高版本
- Anthropic API，或兼容 Anthropic Messages API 的服务

### 安装

```bash
git clone https://github.com/Cocoyzh/Rove.git
cd Rove

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

开发时安装测试依赖并运行测试（无需联网）：

```bash
pip install -e .[dev]
python -m pytest tests/ -q
```

### 配置

在仓库根目录创建 `.env` 文件：

```dotenv
LLM_MODEL_ID=your-model-id
LLM_API_KEY=your-api-key
LLM_TIMEOUT=60
```

可选配置：

| 变量 | 说明 |
| --- | --- |
| `LLM_BASE_URL` | 兼容 Anthropic Messages API 的服务地址 |
| `LLM_CONTEXT_WINDOW` | 覆盖模型的上下文窗口大小 |
| `LLM_FAST_MODEL_ID` | 子代理使用的模型 |
| `ROVE_WORKSPACE` | 目标 workspace，默认为启动时的当前目录 |
| `ROVE_TEAMMATES` | 设为 `1` 启用 Teammate |
| `ROVE_SEARCH_PROVIDER` | 联网搜索后端，默认 `auto` |
| `TAVILY_API_KEY` | Tavily 搜索密钥，未设置时使用 DuckDuckGo |

### 启动

```bash
rove
```

## 使用说明

启动后可直接输入编码任务：

```text
Rove >> 检查这个项目的配置加载逻辑，修复问题并验证结果
```

REPL 内置命令：

| 命令 | 说明 |
| --- | --- |
| `/team` | 查看当前 Teammate 状态 |
| `/new` | 清空历史并开始新会话 |
| `/compact` | 立即存档并摘要当前会话 |
| `/status` | 查看模型与本会话、全局的 token 消耗 |
| `/context` | 查看上下文窗口占用 |
| `/mode [strict\|workspace\|full_access]` | 查看或切换权限审批模式 |
| `q`、`exit` 或空输入 | 退出 Rove |

当工具调用需要审批时，可输入：

| 输入 | 说明 |
| --- | --- |
| `y` | 仅允许本次操作 |
| `s` | 当前会话内允许完全相同的操作 |
| `c` | 当前会话内允许同一命令前缀（bash、后台命令） |
| `p` | 当前会话内允许同一路径（写文件、编辑文件） |
| `t` | 当前会话内允许该工具的所有调用 |
| `N` | 拒绝操作 |

## 上下文管理

Rove 在每轮模型调用前按 L3 → L2 → L1 → L4 的顺序运行压缩流水线，L2、L1、L4 只在上下文占用超过对应水位后触发：

| 层级 | 策略 | 触发水位 | 行为 |
| --- | --- | --- | --- |
| L3 | Tool Result Budget | 每轮检查 | 将超大工具输出保存到磁盘，仅保留路径和预览 |
| L2 | Micro Compact | 10% | 将较旧的工具结果落盘，保留近期 10 条 |
| L1 | Snip Compact | 20% | 裁剪中段消息，保持工具调用与结果配对完整，固定保留用户消息 |
| L4 | Auto Compact | 40% | 存档完整对话，调用模型生成摘要，保留最近 10 条消息 |

如果接口返回 `prompt_too_long` 或 `too many tokens`，Rove 会执行一次应急压缩，然后重试当前请求。手动执行 `/compact` 可提前归档并压缩当前会话。

## 运行时数据

Rove 在 workspace 下使用以下目录保存运行状态：

| 路径 | 内容 |
| --- | --- |
| `.tasks/` | 任务看板与任务状态 |
| `.team/` | Teammate 状态与消息收件箱 |
| `.rove/tool-results/` | 从上下文中移出的超大工具输出 |
| `.rove/transcripts/` | 上下文摘要前的完整对话存档 |
| `.rove/memory/` | 长期记忆 |
| `.rove/tmp/` | bash 命令的临时目录 |

## 项目结构

```text
src/rove/
├── main.py                    # REPL 入口与依赖装配
├── lead_agent.py              # Lead Agent 会话与执行循环
├── messages.py                # 统一消息与工具调用模型
├── llm.py                     # 模型请求和响应结构
├── llm_adapters.py            # 模型后端适配器
├── prompt/                    # 系统提示词
├── compaction/                # 上下文压缩流水线
├── tools/                     # 内置工具与多智能体协作工具
├── tool_registry.py           # 工具注册和权限执行入口
├── permissions.py             # 权限策略与交互审批
├── security/                  # 只读命令分类器与会话授权规则
├── memory.py                  # 工作记忆与长期记忆
├── task_manager.py            # 文件化任务管理
├── skill_loader.py            # Skill 加载器
├── benchmarks/                # SWE-Bench 适配与子代理评测
└── paths.py                   # 项目及运行时路径定义
```