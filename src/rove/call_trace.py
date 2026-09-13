"""CallTrace：调用级事件模型、落盘与按需拉取。

每次 LLM 调用 / 工具执行产生两类出口：
- 完整事件（block 带 full 全文）追加写入 ``.rove/traces/<session_id>.jsonl``，供历史回放与按块拉取全量；
- 预览事件（block 只带 preview + full_ref，不含 full）通过 ``on_event`` 回调推给 WS 通道，避免长输出打爆前端。

同一会话内 lead 与多个 teammate 线程共享一个 ``CallTracer``（锁保护 seq 与落盘），
各自通过 ``for_agent(agent_name)`` 拿到绑定了归属名的视图。落盘失败一律吞掉，绝不阻断主流程。
"""

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rove.paths import TRACE_DIR

# WS 与回放列表中 block 携带的最大字符数，超出部分前端按 full_ref 拉全量
PREVIEW_LEN = 200

# 事件类型
KIND_LLM_CALL = "llm_call"
KIND_TOOL_EXEC = "tool_exec"

# 状态机：running（进行中，仅走 WS 不落盘）→ ok / error / tool_use（终态，落盘）
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TOOL_USE = "tool_use"


def new_session_id(prefix: str = "repl") -> str:
    """为无显式会话概念的运行时（REPL）生成 trace 会话标识。"""
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def _preview(text: Optional[str]) -> str:
    text = text or ""
    if len(text) <= PREVIEW_LEN:
        return text
    return text[:PREVIEW_LEN] + " …"


def strip_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """剥掉单个 block 的 full 全文，保留 preview 与 full_ref。"""
    return {k: v for k, v in block.items() if k != "full"}


def strip_full(event: Dict[str, Any]) -> Dict[str, Any]:
    """剥掉事件里所有 block 的 full 全文，得到 WS / 回放列表用的预览形态。"""
    slim = {k: v for k, v in event.items() if k != "blocks"}
    slim["blocks"] = [strip_block(b) for b in event.get("blocks", [])]
    return slim


class CallTracer:
    """会话级调用追踪器：分配全局 seq、聚合 tool_result 作为下一次 LLM 调用的输入块、落盘 JSONL。"""

    def __init__(
        self,
        session_id: str,
        trace_dir: Optional[Path] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.session_id = session_id
        self.trace_dir = Path(trace_dir) if trace_dir else TRACE_DIR
        self.on_event = on_event
        self._lock = threading.Lock()
        self._seq = 0
        # 尚未被下一次 llm_start 消费的 tool_result 输入块（跨 agent 共享同一会话序列无意义，
        # 但 lead/teammate 各自持有独立视图缓冲，见 AgentTracer）
        self._file_ready = False

    def _ensure_file(self) -> Optional[Path]:
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            return self.trace_dir / f"{self.session_id}.jsonl"
        except Exception:
            return None

    def _append(self, event: Dict[str, Any]) -> None:
        # 多 agent 线程并发 finalize，落盘与 seq 一样受锁保护
        with self._lock:
            path = self._ensure_file()
            if path is None:
                return
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def _emit(self, event: Dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(strip_full(event))
            except Exception:
                pass

    def for_agent(self, agent_name: str) -> "AgentTracer":
        return AgentTracer(self, agent_name)


class AgentTracer:
    """绑定单一 agent（lead / teammate 名）的追踪视图，缓存该 agent 待消费的输入块。"""

    def __init__(self, tracer: CallTracer, agent_name: str) -> None:
        self._tracer = tracer
        self.agent_name = agent_name
        self._pending_input: List[Dict[str, Any]] = []
        self._running: Dict[int, Dict[str, Any]] = {}

    # ---------- LLM 调用 ----------

    def llm_start(self, query: Optional[str] = None) -> Optional[int]:
        """LLM 调用开始。query 非空时作为 user_message 输入块；此前累积的 tool_result 一并作为输入。"""
        tracer = self._tracer
        blocks: List[Dict[str, Any]] = []
        if query is not None:
            blocks.append(self._make_block("user_message", full=query))
        with tracer._lock:
            blocks.extend(self._pending_input)
            self._pending_input = []
            tracer._seq += 1
            seq = tracer._seq
        event = self._base_event(seq, KIND_LLM_CALL, STATUS_RUNNING, blocks=blocks)
        with tracer._lock:
            self._running[seq] = event
        tracer._emit(event)
        return seq

    def llm_end(self, seq: Optional[int], response: Any) -> None:
        """LLM 调用结束：按响应内容产出 thinking / text / tool_use 输出块，终态为 ok 或 tool_use。"""
        if seq is None:
            return
        tracer = self._tracer
        with tracer._lock:
            event = self._running.pop(seq, None)
        if event is None:
            return

        blocks: List[Dict[str, Any]] = list(event.get("blocks", []))
        thinking = getattr(response, "thinking", "") or ""
        if thinking:
            blocks.append(self._make_block("thinking", full=thinking))
        content = getattr(response, "content", "") or ""
        if content:
            blocks.append(self._make_block("text", full=content))

        tool_calls = getattr(response, "tool_calls", None) or []
        for tc in tool_calls:
            args_text = json.dumps(getattr(tc, "tool_args", {}) or {}, ensure_ascii=False, indent=2)
            blocks.append(self._make_block(
                "tool_use",
                tool_name=getattr(tc, "tool_name", None),
                tool_id=getattr(tc, "tool_id", None),
                full=args_text,
            ))

        usage = getattr(response, "usage", None)
        event.update({
            "status": STATUS_TOOL_USE if tool_calls else STATUS_OK,
            "model": getattr(response, "model", None),
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
            "cache_read_tokens": getattr(usage, "cache_read_tokens", None) if usage else None,
            "latency_ms": getattr(response, "latency_ms", 0) or 0,
            "blocks": blocks,
        })
        self._finalize(event)

    def llm_error(self, seq: Optional[int], error: str) -> None:
        if seq is None:
            return
        tracer = self._tracer
        with tracer._lock:
            event = self._running.pop(seq, None)
        if event is None:
            return
        event.update({"status": STATUS_ERROR, "error": error})
        self._finalize(event)

    # ---------- 工具执行 ----------

    def tool_start(self, tool_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[int]:
        tracer = self._tracer
        args_text = json.dumps(tool_args or {}, ensure_ascii=False, indent=2)
        blocks = [self._make_block("tool_use", tool_name=tool_name, tool_id=tool_id, full=args_text)]
        with tracer._lock:
            tracer._seq += 1
            seq = tracer._seq
        event = self._base_event(seq, KIND_TOOL_EXEC, STATUS_RUNNING, blocks=blocks)
        with tracer._lock:
            self._running[seq] = event
        tracer._emit(event)
        return seq

    def tool_end(
        self,
        seq: Optional[int],
        tool_id: str,
        tool_name: str,
        output: str,
        is_error: bool,
        cost_ms: int = 0,
    ) -> None:
        if seq is None:
            return
        tracer = self._tracer
        with tracer._lock:
            event = self._running.pop(seq, None)
        if event is None:
            return

        result_block = self._make_block(
            "tool_result", tool_name=tool_name, tool_id=tool_id, full=str(output), is_error=is_error
        )
        blocks = list(event.get("blocks", [])) + [result_block]
        event.update({
            "status": STATUS_ERROR if is_error else STATUS_OK,
            "latency_ms": cost_ms,
            "blocks": blocks,
        })
        # 先 finalize 补齐 full_ref，再把预览块缓存为下一次 LLM 调用的输入
        self._finalize(event)
        self._pending_input.append(strip_block(result_block))

    # ---------- 内部 ----------

    def _base_event(self, seq: int, kind: str, status: str, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "seq": seq,
            "session_id": self._tracer.session_id,
            "agent_name": self.agent_name,
            "kind": kind,
            "status": status,
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": None,
            "latency_ms": 0,
            "started_at": time.time(),
            "error": None,
            "blocks": blocks,
        }

    def _make_block(
        self,
        type_: str,
        tool_name: Optional[str] = None,
        tool_id: Optional[str] = None,
        full: str = "",
        is_error: Optional[bool] = None,
    ) -> Dict[str, Any]:
        return {
            "type": type_,
            "tool_name": tool_name,
            "tool_id": tool_id,
            "preview": _preview(full),
            "full_len": len(full),
            "full_ref": None,  # 由 _finalize 按 block 序号补齐
            "is_error": is_error,
            "full": full,
        }

    def _finalize(self, event: Dict[str, Any]) -> None:
        """补齐 full_ref 后落盘并推送预览事件。running 事件永不落盘。"""
        tracer = self._tracer
        blocks = event.get("blocks", [])
        for i, b in enumerate(blocks):
            # 已带引用的输入块（来自 tool_result 缓存）保持指向原 tool_exec 事件
            if b.get("full_ref") is None:
                b["full_ref"] = f"{event['session_id']}:{event['seq']}:{i}" if b.get("full") else None
        tracer._append(event)
        tracer._emit(event)


# ---------- 回放读取（供 HTTP 端点使用） ----------


def load_trace_events(session_id: str, trace_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读取某会话全部终态事件（预览形态，不含 full 全文）。文件不存在返回空列表。"""
    directory = Path(trace_dir) if trace_dir else TRACE_DIR
    path = directory / f"{session_id}.jsonl"
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(strip_full(json.loads(line)))
                except Exception:
                    continue
    except Exception:
        return []
    return events


def load_block_full(session_id: str, seq: int, block_index: int, trace_dir: Optional[Path] = None) -> Optional[str]:
    """按 (seq, block 序号) 拉取单个 block 的全文；找不到返回 None。"""
    directory = Path(trace_dir) if trace_dir else TRACE_DIR
    path = directory / f"{session_id}.jsonl"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("seq") != seq:
                    continue
                blocks = event.get("blocks", [])
                if 0 <= block_index < len(blocks):
                    return blocks[block_index].get("full")
                return None
    except Exception:
        return None
    return None
