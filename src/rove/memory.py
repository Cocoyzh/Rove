"""长短期记忆 (Issue #4)。

WorkingMemory：当前目标 / 发现 / 聚焦文件 / 草稿的黑板，每轮注入 System Prompt；
LongTermMemory：semantic / episodic / preference 条目，JSONL 存在 .rove/memory/，关键词检索。
"""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from rove.paths import MEMORY_DIR

MEMORY_CATEGORIES = {"semantic", "episodic", "preference"}

# 注入上下文的字符预算：约 1500~2000 tokens，不挤占核心推理空间（验收标准 #4）
MEMORY_CONTEXT_BUDGET = 6_000
MAX_WORKING_FINDINGS = 12
MAX_WORKING_FILES = 15
MAX_WORKING_SCRATCHPAD = 8


def _tokenize(text: str) -> list[str]:
    """轻量混合分词：英文按单词小写，中文按双字 bigram，用于关键词重叠检索。"""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    bigrams: list[str] = []
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) == 1:
            bigrams.append(seg)
        else:
            bigrams.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return words + bigrams


@dataclass
class WorkingMemory:
    """短期工作记忆黑板：即使对话被 L1/L4 大幅剪切，这里依然完整。"""

    current_goal: str = ""
    findings: list[str] = field(default_factory=list)
    focused_files: list[str] = field(default_factory=list)
    scratchpad: list[str] = field(default_factory=list)

    def update(self, goal: str | None = None, finding: str | None = None,
               file: str | None = None, note: str | None = None,
               clear_scratchpad: bool = False) -> None:
        if goal is not None and str(goal).strip():
            self.current_goal = str(goal).strip()
        if finding and str(finding).strip():
            self.findings.append(str(finding).strip())
            del self.findings[:-MAX_WORKING_FINDINGS]
        if file and str(file).strip():
            f = str(file).strip()
            if f in self.focused_files:
                self.focused_files.remove(f)
            self.focused_files.append(f)
            del self.focused_files[:-MAX_WORKING_FILES]
        if note and str(note).strip():
            self.scratchpad.append(str(note).strip())
            del self.scratchpad[:-MAX_WORKING_SCRATCHPAD]
        if clear_scratchpad:
            self.scratchpad = []

    def render(self) -> str:
        """渲染为注入 System Prompt 的紧凑文本块；空记忆返回空串。"""
        if not (self.current_goal or self.findings or self.focused_files or self.scratchpad):
            return ""
        lines = ["<working-memory>"]
        if self.current_goal:
            lines.append(f"Goal: {self.current_goal}")
        if self.focused_files:
            lines.append("Focused files: " + ", ".join(self.focused_files[-MAX_WORKING_FILES:]))
        if self.findings:
            lines.append("Findings:")
            lines.extend(f"- {f}" for f in self.findings[-MAX_WORKING_FINDINGS:])
        if self.scratchpad:
            lines.append("Scratchpad:")
            lines.extend(f"- {s}" for s in self.scratchpad[-MAX_WORKING_SCRATCHPAD:])
        lines.append("</working-memory>")
        return "\n".join(lines)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "current_goal": self.current_goal,
            "findings": self.findings,
            "focused_files": self.focused_files,
            "scratchpad": self.scratchpad,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "WorkingMemory":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        return cls(
            current_goal=str(data.get("current_goal", "")),
            findings=[str(x) for x in data.get("findings", [])],
            focused_files=[str(x) for x in data.get("focused_files", [])],
            scratchpad=[str(x) for x in data.get("scratchpad", [])],
        )


class LongTermMemory:
    """长期记忆：JSONL 追加存储，同 (category, key) 只保留最新一条。"""

    def __init__(self, memory_dir: Path):
        self.dir = memory_dir
        self.path = memory_dir / "long_term.jsonl"
        self._lock = threading.Lock()
        self._cache: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._cache is None:
            entries: list[dict] = []
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            self._cache = entries
        return self._cache

    def store(self, category: str, key: str, content: str) -> str:
        category = str(category).strip().lower()
        key = str(key).strip()
        content = str(content).strip()
        if category not in MEMORY_CATEGORIES:
            return f"Error: category must be one of {sorted(MEMORY_CATEGORIES)}"
        if not key or not content:
            return "Error: key and content are required"

        entry = {"ts": time.time(), "category": category, "key": key, "content": content}
        with self._lock:
            entries = [e for e in self._load()
                       if not (e.get("category") == category and e.get("key") == key)]
            entries.append(entry)
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, default=str, ensure_ascii=False) + "\n")
            self._cache = entries
        return f"Stored {category}/{key} ({len(entries)} entries total)"

    def recall(self, query: str, category: str | None = None, limit: int = 5) -> list[dict]:
        """按关键词重叠度检索，同分时新条目优先。"""
        with self._lock:
            entries = list(self._load())

        if category:
            entries = [e for e in entries if e.get("category") == category]

        terms = set(_tokenize(query))
        scored: list[tuple[int, float, dict]] = []
        for e in entries:
            if not terms:
                break
            entry_terms = set(_tokenize(f"{e.get('key', '')} {e.get('content', '')}"))
            overlap = len(terms & entry_terms)
            if overlap > 0:
                scored.append((overlap, float(e.get("ts", 0)), e))

        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored[:max(1, limit)]]

    def render_context(self, query: str = "", budget: int = MEMORY_CONTEXT_BUDGET) -> str:
        """渲染注入 System Prompt 的长期记忆块：查询命中条目优先，
        近期 semantic/preference 条目兜底（保证跨会话基础知识总可见）。"""
        with self._lock:
            entries = list(self._load())
        if not entries:
            return ""

        picked: list[dict] = []
        seen: set[int] = set()
        for e in self.recall(query, limit=8):
            seen.add(id(e))
            picked.append(e)

        recent = [e for e in entries
                  if e.get("category") in ("semantic", "preference") and id(e) not in seen]
        recent.sort(key=lambda e: -float(e.get("ts", 0)))
        for e in recent:
            if len(picked) >= 10:
                break
            picked.append(e)

        lines = ["<long-term-memory>"]
        size = sum(len(l) for l in lines)
        for e in picked:
            line = f"[{e.get('category')}] {e.get('key')}: {e.get('content')}"
            if size + len(line) > budget:
                break
            lines.append(line)
            size += len(line) + 1
        if len(lines) == 1:
            return ""
        lines.append("</long-term-memory>")
        return "\n".join(lines)


class MemoryManager:
    """工作记忆 + 长期记忆的统一入口（进程级单例 MEMORY）。"""

    def __init__(self, memory_dir: Path):
        self.dir = memory_dir
        self.working = WorkingMemory.load(memory_dir / "working_memory.json")
        self.long_term = LongTermMemory(memory_dir)

    def build_system_context(self, query: str = "") -> str:
        """拼装每轮注入 System Prompt 的记忆块（工作记忆 + 长期记忆检索）。"""
        blocks = [b for b in (self.working.render(), self.long_term.render_context(query)) if b]
        return "\n".join(blocks)

    def update_working(self, **kwargs) -> str:
        self.working.update(**kwargs)
        self.working.save(self.dir / "working_memory.json")
        return self.working.render() or "Working memory is empty"

    def archive_working_memory(self, handoff: str = "") -> bool:
        """会话收尾的轻量反思：工作记忆快照沉淀为 episodic 条目后清空黑板，
        长期记忆跨会话保留。返回是否实际归档。"""
        wm = self.working
        parts = []
        if wm.current_goal:
            parts.append(f"goal: {wm.current_goal}")
        if wm.findings:
            parts.append("findings: " + "; ".join(wm.findings[-5:]))
        if not parts:
            return False

        content = " | ".join(parts)
        if handoff:
            content += f" | handoff: {handoff}"
        self.long_term.store("episodic", key=f"session_{int(time.time())}", content=content[:2000])
        self.working = WorkingMemory()
        self.working.save(self.dir / "working_memory.json")
        return True


MEMORY = MemoryManager(MEMORY_DIR)
