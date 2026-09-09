"""长短期记忆系统测试 (Issue #4)。

全部使用 tmp_path 隔离的 MemoryManager 实例，不触碰真实 .rove/memory/。
"""
import json

from rove.memory import (
    MemoryManager,
    WorkingMemory,
    LongTermMemory,
    MEMORY_CATEGORIES,
    MEMORY_CONTEXT_BUDGET,
    MAX_WORKING_FINDINGS,
    MAX_WORKING_FILES,
    MAX_WORKING_SCRATCHPAD,
)
from rove.tools.memory_tools import build_memory_tools, build_memory_recall_tool
from rove.messages import Message, ToolCall
from rove.llm import LLMResponse, LLMRequest
from rove.compaction.compaction_layers import compact_history
from rove.prompt.system_prompt import SYSTEM_PROMPT


class DummyLLM:
    """最小可用的摘要 LLM 替身（与 test_compaction 中行为一致）"""

    context_window = 1_000_000

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content="1. 目标：排查内存泄漏",
            tool_calls=[],
            model="test-model",
            stop_reason="end_turn",
        )


def test_working_memory_update_and_caps():
    wm = WorkingMemory()
    wm.update(goal="排查内存泄漏")
    wm.update(goal="")  # 空串不覆盖
    assert wm.current_goal == "排查内存泄漏"

    for i in range(MAX_WORKING_FINDINGS + 3):
        wm.update(finding=f"finding {i}")
    assert len(wm.findings) == MAX_WORKING_FINDINGS
    assert wm.findings[-1] == f"finding {MAX_WORKING_FINDINGS + 2}"

    for i in range(MAX_WORKING_FILES + 2):
        wm.update(file=f"src/f{i}.py")
    assert len(wm.focused_files) == MAX_WORKING_FILES

    # 重复聚焦同一文件应移到末尾（最近优先），不产生重复项
    wm.update(file="src/f0.py")
    assert wm.focused_files.count("src/f0.py") == 1
    assert wm.focused_files[-1] == "src/f0.py"

    for i in range(MAX_WORKING_SCRATCHPAD + 1):
        wm.update(note=f"note {i}")
    assert len(wm.scratchpad) == MAX_WORKING_SCRATCHPAD


def test_working_memory_render_and_persistence(tmp_path):
    wm = WorkingMemory()
    assert wm.render() == ""  # 空记忆不产出注入块

    wm.update(goal="重构缓存层", finding="fast_cache 是热点路径", file="src/fast_cache.py", note="待确认 LRU 容量")
    block = wm.render()
    assert block.startswith("<working-memory>")
    assert "Goal: 重构缓存层" in block
    assert "- fast_cache 是热点路径" in block
    assert block.endswith("</working-memory>")

    path = tmp_path / "working_memory.json"
    wm.save(path)
    loaded = WorkingMemory.load(path)
    assert loaded.current_goal == "重构缓存层"
    assert loaded.findings == ["fast_cache 是热点路径"]

    # 损坏文件回退为空实例，不抛异常
    path.write_text("{broken json", encoding="utf-8")
    assert WorkingMemory.load(path).current_goal == ""


def test_long_term_store_overwrites_same_key(tmp_path):
    lt = LongTermMemory(tmp_path)
    assert "Stored" in lt.store("semantic", "test-command", "pytest -q")
    assert "Stored" in lt.store("semantic", "test-command", "python -m pytest tests/ -q")

    entries = lt._load()
    assert len(entries) == 1
    assert entries[0]["content"] == "python -m pytest tests/ -q"

    # 非法类别被拒绝且不落盘
    assert lt.store("hacker", "k", "v").startswith("Error")
    assert len(lt._load()) == 1


def test_long_term_recall_scoring_and_filter(tmp_path):
    lt = LongTermMemory(tmp_path)
    lt.store("semantic", "db", "项目使用 PostgreSQL 16，连接池 pgbouncer")
    lt.store("episodic", "lesson-1", "PostgreSQL 测试失败根因是时区配置")
    lt.store("preference", "style", "用户偏好 SQLite 做本地缓存")
    lt.store("semantic", "other", "前端用 React + Vite 构建")

    hits = lt.recall("PostgreSQL 时区")
    assert len(hits) >= 1
    # 两条 PostgreSQL 相关命中，重叠度高者在前
    assert "PostgreSQL" in hits[0]["content"]
    assert all("React" not in e["content"] for e in hits)

    only_episodic = lt.recall("PostgreSQL 时区", category="episodic")
    assert len(only_episodic) == 1
    assert only_episodic[0]["key"] == "lesson-1"

    assert lt.recall("完全不相关的查询词表") == []


def test_long_term_context_budget_and_fallback(tmp_path):
    lt = LongTermMemory(tmp_path)
    # 只有 episodic 且无查询命中时：render_context 返回空（不注入噪音）
    lt.store("episodic", "old", "x" * 100)
    assert lt.render_context("") == ""

    # semantic/preference 即使无查询也必须兜底可见（跨会话基础知识继承）
    lt.store("semantic", "convention", "提交信息遵循 Conventional Commits")
    lt.store("preference", "lang", "回复使用中文")
    block = lt.render_context("")
    assert block.startswith("<long-term-memory>")
    assert "Conventional Commits" in block
    assert "回复使用中文" in block

    # 预算约束：超长条目被截断在 budget 内
    lt.store("semantic", "huge", "y" * (MEMORY_CONTEXT_BUDGET + 1000))
    rendered = lt.render_context("convention", budget=MEMORY_CONTEXT_BUDGET)
    assert len(rendered) <= MEMORY_CONTEXT_BUDGET + len("</long-term-memory>\n")


def test_memory_manager_archive_working_memory(tmp_path):
    mm = MemoryManager(tmp_path)
    mm.update_working(goal="修复压缩死循环", finding="L2 阈值过小", file="compaction_layers.py")

    assert mm.archive_working_memory() is True
    # 归档后：长期记忆多一条 episodic，工作记忆黑板清空
    archived = mm.long_term.recall("压缩死循环", category="episodic")
    assert len(archived) == 1
    assert "L2 阈值过小" in archived[0]["content"]
    assert mm.working.current_goal == ""

    # 空黑板再归档应为 False，不产生垃圾条目
    assert mm.archive_working_memory() is False

    # handoff 信息一并沉淀
    mm.update_working(goal="第二个任务")
    mm.archive_working_memory(handoff="下一步跑全量回归")
    assert any("handoff" in e["content"] for e in mm.long_term._load())


def test_memory_manager_system_context_blocks(tmp_path):
    mm = MemoryManager(tmp_path)
    assert mm.build_system_context("") == ""

    mm.update_working(goal="目标 A")
    mm.long_term.store("semantic", "fact", "项目使用 uv 管理依赖")
    ctx = mm.build_system_context("uv 依赖")
    assert "<working-memory>" in ctx
    assert "<long-term-memory>" in ctx
    assert ctx.index("<working-memory>") < ctx.index("<long-term-memory>")


def test_memory_tools_registration_and_handlers(tmp_path, monkeypatch):
    tools = build_memory_tools()
    assert [t.name for t in tools] == ["memory_store", "memory_recall", "memory_update"]
    for t in tools:
        assert t.handler is not None

    # Teammates 只拿到只读检索工具
    teammate_tool = build_memory_recall_tool()
    assert teammate_tool.name == "memory_recall"

    # 用隔离实例替换全局单例，验证 handler 行为而不污染真实记忆
    from rove.tools import memory_tools as mt
    isolated = MemoryManager(tmp_path)
    monkeypatch.setattr(mt, "MEMORY", isolated)

    store_tool = next(t for t in tools if t.name == "memory_store")
    recall_tool = next(t for t in tools if t.name == "memory_recall")
    update_tool = next(t for t in tools if t.name == "memory_update")

    out = store_tool.handler(category="semantic", key="ci", content="CI 跑 python -m pytest")
    assert "Stored" in out
    assert "CI 跑" in recall_tool.handler(query="CI pytest")
    assert "<working-memory>" in update_tool.handler(goal="写文档")

    # 团队只读工具同样可检索
    assert "CI 跑" in teammate_tool.handler(query="CI pytest")


def test_l4_summary_persists_into_long_term_memory(tmp_path):
    """压缩与记忆联动：L4 摘要经 on_summary 沉淀为 episodic 条目"""
    mm = MemoryManager(tmp_path)
    messages = [Message(role="user", content="排查内存泄漏")]
    for i in range(10):
        messages.append(Message(
            role="assistant", content=f"step {i}",
            tool_calls=[ToolCall(tool_id=f"c_{i}", tool_name="t", tool_args={})],
        ))
        messages.append(Message(role="tool", tool_call_id=f"c_{i}", content=f"out {i}"))

    compact_history(DummyLLM(), messages, on_summary=lambda s: mm.long_term.store("episodic", "summary", s))

    hits = mm.long_term.recall("内存泄漏", category="episodic")
    assert len(hits) == 1
    assert "内存泄漏" in hits[0]["content"]


def test_system_prompt_guides_memory_usage():
    """System Prompt 必须告知 agent 记忆块的存在与工具用法，否则工具形同虚设"""
    assert "memory_update" in SYSTEM_PROMPT
    assert "memory_store" in SYSTEM_PROMPT
    assert "memory_recall" in SYSTEM_PROMPT
    assert "<working-memory>" in SYSTEM_PROMPT
