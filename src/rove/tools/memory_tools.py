"""记忆工具 (Issue #4)：memory_store / memory_recall / memory_update；teammate 只有只读的 memory_recall。"""

import time
from typing import List

from ..tool_registry import Tool
from rove.memory import MEMORY, MEMORY_CATEGORIES


def build_memory_tools() -> List[Tool]:
    """面向 Lead Agent 的完整记忆工具集。"""

    def _store(category: str, key: str, content: str) -> str:
        return MEMORY.long_term.store(category, key, content)

    def _recall(query: str, category: str = None, limit: int = 5) -> str:
        results = MEMORY.long_term.recall(query, category=category, limit=limit)
        if not results:
            return "No matching long-term memories."
        lines = []
        for e in results:
            date = time.strftime("%Y-%m-%d", time.localtime(e.get("ts", 0)))
            lines.append(f"[{e.get('category')}] {e.get('key')} ({date}): {e.get('content')}")
        return "\n".join(lines)

    def _update(goal: str = None, finding: str = None, file: str = None,
                note: str = None, clear_scratchpad: bool = False) -> str:
        rendered = MEMORY.update_working(
            goal=goal, finding=finding, file=file, note=note,
            clear_scratchpad=clear_scratchpad,
        )
        return rendered

    return [
        Tool(
            name="memory_store",
            description="Persist durable knowledge to long-term memory (survives across sessions). category: semantic (project facts/conventions), episodic (task lessons/root causes), preference (user habits/constraints).",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": sorted(MEMORY_CATEGORIES)},
                    "key": {"type": "string", "description": "Short unique key, e.g. 'test-command'"},
                    "content": {"type": "string"},
                },
                "required": ["category", "key", "content"],
            },
            handler=lambda **kw: _store(kw["category"], kw["key"], kw["content"]),
        ),
        Tool(
            name="memory_recall",
            description="Search long-term memory by keywords (returns top matches).",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "category": {"type": "string", "enum": sorted(MEMORY_CATEGORIES)},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda **kw: _recall(kw["query"], kw.get("category"), kw.get("limit", 5)),
        ),
        Tool(
            name="memory_update",
            description="Update the working-memory blackboard injected into every LLM call: current goal, verified findings, focused files, scratchpad notes. Survives context compaction.",
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Set/replace the current goal"},
                    "finding": {"type": "string", "description": "Append one verified finding"},
                    "file": {"type": "string", "description": "Mark one file as focused"},
                    "note": {"type": "string", "description": "Append one scratchpad note"},
                    "clear_scratchpad": {"type": "boolean"},
                },
            },
            handler=lambda **kw: _update(
                goal=kw.get("goal"), finding=kw.get("finding"), file=kw.get("file"),
                note=kw.get("note"), clear_scratchpad=kw.get("clear_scratchpad", False),
            ),
        ),
    ]


def build_memory_recall_tool() -> Tool:
    """Teammates 只读检索工具（团队公用知识库）。"""

    def _recall(query: str, category: str = None, limit: int = 5) -> str:
        results = MEMORY.long_term.recall(query, category=category, limit=limit)
        if not results:
            return "No matching long-term memories."
        return "\n".join(
            f"[{e.get('category')}] {e.get('key')}: {e.get('content')}" for e in results
        )

    return Tool(
        name="memory_recall",
        description="Search the team's shared long-term memory by keywords (read-only).",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string", "enum": sorted(MEMORY_CATEGORIES)},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
        handler=lambda **kw: _recall(kw["query"], kw.get("category"), kw.get("limit", 5)),
    )
