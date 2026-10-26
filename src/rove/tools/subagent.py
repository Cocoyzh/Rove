"""dispatch_subagent：一次性子代理。

在独立上下文里跑有边界的子任务，只把浓缩报告回传 lead；
子代理只有文件工具和 execute_python（不能派发、写记忆或联网）。
"""

import time
import threading
import uuid
from pathlib import Path

from rove.paths import TOOL_RESULTS_DIR, WORKSPACE_ROOT
from rove.permissions import APPROVAL_MANAGER, PermissionPolicy
from rove.llm import LLMRequest, LLMResponse
from rove.llm_adapters import ADAPTER_FACTORY, BaseLLMAdapter, estimate_cost_usd
from rove.messages import Message
from rove.tools.background import BackGroundManager, bg_manager
from rove.tools.execute_python import execute_python_tool
from rove.tools.file_tools import FILE_TOOLS
from rove.tools.message_bus import BUS, MessageBus
from rove.tool_registry import Tool, ToolRegistry

# 同时运行的子代理数上限
MAX_CONCURRENT_SUBAGENTS = 1
DEFAULT_MAX_TURNS = 12
DEFAULT_TOKEN_BUDGET = 100_000
# 报告内联进 lead 历史的字符上限；超长部分落盘 tool-results/ 并附路径指针
MAX_INLINE_CHARS = 16_000

SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused subagent dispatched by a lead agent to complete one bounded task.\n"
    "You run in an isolated context: you cannot see the lead's conversation history. "
    "Work only from the briefing you receive.\n\n"
    "Rules:\n"
    "- Complete the objective efficiently; you have hard limits on turns and tokens.\n"
    "- Prefer read-only exploration (bash/read_file) before modifying anything.\n"
    "- You have no messaging, memory, web or agent-spawning tools; do not ask for them.\n"
    "- When the objective is achieved, or you have gathered everything obtainable, "
    "stop calling tools and reply with your final report.\n\n"
    "Your final reply must be exactly this report (plain text, no tool calls, no extra prose):\n\n"
    "## 结论摘要\n"
    "<condensed conclusion, <=2000 chars, directly answering the objective>\n"
    "## 关键文件与证据\n"
    "<file paths / commands / data that back the conclusion, one per line>\n"
    "## 遇到的障碍\n"
    "<blockers encountered, or 无>"
)


def build_restricted_registry(
    permission_policy: PermissionPolicy = None,
    approval_handler=None,
) -> ToolRegistry:
    """subagent 专用受限注册表：文件工具 + execute_python，别无其他。"""
    registry = ToolRegistry(
        permission_policy or PermissionPolicy(WORKSPACE_ROOT),
        approval_handler or APPROVAL_MANAGER.request,
    )
    registry.register_many([*FILE_TOOLS, execute_python_tool])
    return registry


class SubagentManager:
    """dispatch_subagent 的执行体：独立线程跑受限 LLM 循环，产出结构化报告。"""

    def __init__(
        self,
        llm: BaseLLMAdapter,
        permission_policy: PermissionPolicy = None,
        approval_handler=None,
        results_dir: Path = TOOL_RESULTS_DIR,
        bus: MessageBus = BUS,
        bg: BackGroundManager = bg_manager,
        on_event=None,
        fast_model_id: str | None = None,
        adapter_factory=None,
    ):
        self._llm = llm
        self._permission_policy = permission_policy
        self._approval_handler = approval_handler
        self._results_dir = results_dir
        self._bus = bus
        self._bg = bg
        self._on_event = on_event
        self._fast_model_id = fast_model_id
        self._factory = adapter_factory if adapter_factory is not None else ADAPTER_FACTORY
        self._lock = threading.Lock()
        self._active = 0
        self._current_id: str | None = None
        self._last_total_tokens = 0

    # ---------- 派发入口 ----------

    def dispatch(
        self,
        objective: str,
        context: str,
        max_turns: int = DEFAULT_MAX_TURNS,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        model: str | None = None,
        run_in_background: bool = False,
    ) -> str:
        objective = (objective or "").strip()
        context = (context or "").strip()
        if not objective:
            return "Error: objective is required"
        if not context:
            return "Error: context is required (the subagent sees nothing but your briefing)"
        if self._llm is None:
            return "Error: no LLM adapter configured for subagent"

        with self._lock:
            if self._active >= MAX_CONCURRENT_SUBAGENTS:
                return (f"Error: concurrent subagent limit reached ({MAX_CONCURRENT_SUBAGENTS}); "
                        f"current: {self._current_id}. Use check_background to poll it.")
            task_id = f"sub-{uuid.uuid4().hex[:8]}"
            self._active += 1
            self._current_id = task_id

        worker_args = (task_id, objective, context, int(max_turns), int(token_budget), model)

        if run_in_background:
            self._bg.register(task_id, f"dispatch_subagent: {objective[:70]}")
            thread = threading.Thread(
                target=self._run_task,
                args=worker_args + (True, None, None),
                daemon=True,
                name=f"subagent-{task_id}",
            )
            thread.start()
            return (f"Background subagent {task_id} started: {objective[:70]}. "
                    f"Its report will arrive via background notification; use check_background to poll.")

        done = threading.Event()
        holder: dict[str, str] = {}
        thread = threading.Thread(
            target=self._run_task,
            args=worker_args + (False, done, holder),
            daemon=True,
            name=f"subagent-{task_id}",
        )
        thread.start()
        done.wait()
        return holder.get("report", f"Error: subagent {task_id} terminated without a report")

    # ---------- 执行体 ----------

    def _run_task(self, task_id, objective, context, max_turns, token_budget, model,
                  notify_bg, done_event, holder):
        try:
            self._emit({
                "type": "subagent_status", "task_id": task_id, "status": "running",
                "objective": objective[:200], "max_turns": max_turns,
            })
            report = self._execute(task_id, objective, context, max_turns, token_budget, model)
            status = "completed"
        except Exception as e:  # 不允许静默死亡：异常也要变成报告回到 lead
            report = f"## 结论摘要\n（子代理异常终止）\n\n## 遇到的障碍\nError: {e}"
            status = "error"
        finally:
            with self._lock:
                self._active -= 1
                if self._current_id == task_id:
                    self._current_id = None

        if notify_bg:
            self._bg.notify(task_id, status, report)

        self._emit({
            "type": "subagent_status", "task_id": task_id, "status": status,
            "total_tokens": self._last_total_tokens,
        })
        if done_event is not None:
            holder["report"] = report
            done_event.set()
    def _execute(self, task_id, objective, context, max_turns, token_budget, model) -> str:
        llm = self._resolve_adapter(model)
        registry = build_restricted_registry(self._permission_policy, self._approval_handler)
        tools = registry.get_schemas()
        started = time.time()

        messages: list[Message] = [
            Message(role="user", content=self._briefing(objective, context, max_turns, token_budget)),
        ]

        total_in = total_out = 0
        turns_used = 0
        final_text = ""
        last_text = ""
        stop_note = ""

        for turn in range(1, max_turns + 1):
            turns_used = turn
            try:
                response: LLMResponse = llm.complete(LLMRequest(
                    messages=list(messages), tools=tools,
                    max_tokens=8000, system_prompt=SUBAGENT_SYSTEM_PROMPT,
                ))
            except Exception as e:
                stop_note = f"LLM call failed at turn {turn}: {e}"
                break

            if response.usage:
                total_in += response.usage.input_tokens
                total_out += response.usage.output_tokens
            self._last_total_tokens = total_in + total_out
            messages.append(Message(role="assistant",
                                    content=response.content,
                                    tool_calls=response.tool_calls))
            if response.content:
                last_text = response.content

            if response.stop_reason != "tool_use":
                final_text = response.content or ""
                break

            if total_in + total_out >= token_budget:
                stop_note = (f"token_budget exhausted "
                             f"({total_in + total_out}/{token_budget} tokens after turn {turn})")
                break

            activity = []
            for tc in response.tool_calls:
                output = registry.execute(tc.tool_name, tc.tool_args or {})
                messages.append(Message(role="tool", tool_call_id=tc.tool_id, content=str(output)))
                preview = ", ".join(str(v) for v in (tc.tool_args or {}).values())[:80]
                activity.append(f"{tc.tool_name}({preview})")
            self._emit_progress(task_id, turn, max_turns, "; ".join(activity)[:120])

        if not final_text and not stop_note:
            stop_note = f"max_turns reached ({max_turns})"

        return self._assemble_report(task_id, final_text, last_text, stop_note,
                                     turns_used, total_in, total_out, started, llm.model)

    # ---------- 报告组装 ----------

    def _assemble_report(self, task_id, final_text, last_text, stop_note,
                         turns_used, total_in, total_out, started, model_id) -> str:
        duration = max(int(time.time() - started), 0)
        if final_text:
            body = final_text.strip()
        else:
            body = (
                "## 结论摘要\n"
                "（子代理未能在限制内产出最终报告，以下为其最后一次文本输出）\n"
                f"{last_text.strip() or '（无文本输出）'}\n\n"
                "## 遇到的障碍\n"
                f"{stop_note}"
            )

        cost_usd = estimate_cost_usd(model_id, total_in, total_out)
        report = (
            f"{body}\n\n---\n"
            f"usage: turns={turns_used}, input_tokens={total_in}, "
            f"output_tokens={total_out}, total_tokens={total_in + total_out}, "
            f"duration={duration}s, cost_usd={cost_usd:.6f}"
        )
        if stop_note:
            report += f"\nstopped: {stop_note}"

        if len(report) > MAX_INLINE_CHARS:
            full_path = self._persist(task_id, report)
            head = report[: MAX_INLINE_CHARS // 2]
            tail = report[-MAX_INLINE_CHARS // 4:]
            report = (
                f"{head}\n\n... [report truncated, full version persisted at {full_path}] ...\n\n{tail}"
            )
        return report

    def _persist(self, task_id: str, report: str) -> Path:
        self._results_dir.mkdir(parents=True, exist_ok=True)
        path = self._results_dir / f"{task_id}.md"
        path.write_text(report, encoding="utf-8")
        return path

    # ---------- 进度与辅助 ----------

    def _emit_progress(self, task_id: str, turn: int, max_turns: int, activity: str) -> None:
        self._emit({
            "type": "subagent_status", "task_id": task_id, "status": "running",
            "turn": turn, "max_turns": max_turns, "activity": activity,
        })
        # 轻量进度心跳写 lead 收件箱，lead 下一步轮询时以 <inbox> 读到；
        # check_background 亦可经 bg 记录读到（同步派发无 bg 记录，自动跳过）
        try:
            self._bus.send(task_id, "lead",
                           f"[{task_id}] turn {turn}/{max_turns}: {activity}",
                           msg_type="progress")
        except Exception:
            pass
        self._bg.set_progress(task_id, f"turn {turn}/{max_turns}: {activity}")

    def _emit(self, event: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:
            pass

    @staticmethod
    def _briefing(objective: str, context: str, max_turns: int, token_budget: int) -> str:
        return (
            f"<objective>\n{objective}\n</objective>\n\n"
            f"<context>\n{context}\n</context>\n\n"
            f"Operating limits for this run: at most {max_turns} LLM turns and "
            f"{token_budget} tokens. Finish within them; when done, reply with the "
            f"final report in the required format."
        )

    def _resolve_adapter(self, model: str | None) -> BaseLLMAdapter:
        """选模型："fast" 或未指定时用 LLM_FAST_MODEL_ID（未配置则用主模型），具体 id 走适配器工厂。"""
        if model == "fast":
            return self._factory.get(self._fast_model_id) if self._fast_model_id else self._llm
        if model:
            return self._factory.get(model)
        if self._fast_model_id:
            return self._factory.get(self._fast_model_id)
        return self._llm


def build_subagent_tool(manager: SubagentManager) -> Tool:
    return Tool(
        name="dispatch_subagent",
        description=(
            "Dispatch a one-shot subagent that works in its own isolated context window and "
            "returns only a condensed report; intermediate exploration never enters this conversation. "
            "The subagent sees NOTHING from this session — only what you pass in. Briefing template for "
            "`context`: (1) relevant file paths (with hints where to look); (2) established facts and "
            "constraints; (3) exact acceptance criteria; (4) what NOT to touch. "
            "Its tools: bash, read_file, read_notebook, write_file, edit_file, execute_python "
            "(no team/memory/web tools). "
            "Report sections: 结论摘要 / 关键文件与证据 / 遇到的障碍 / usage statistics."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "What the subagent must accomplish, and how the result will be judged.",
                },
                "context": {
                    "type": "string",
                    "description": "Everything the subagent needs: paths, facts, constraints, "
                                   "acceptance criteria. It receives no other context.",
                },
                "max_turns": {
                    "type": "integer",
                    "description": f"Hard cap on LLM turns (default {DEFAULT_MAX_TURNS}).",
                },
                "token_budget": {
                    "type": "integer",
                    "description": f"Hard cap on total tokens (default {DEFAULT_TOKEN_BUDGET}); "
                                   "the run terminates early and returns partial conclusions when exceeded.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model id override, or \"fast\" for the cheap model "
                                   "configured via LLM_FAST_MODEL_ID (exploration tasks default to it).",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run asynchronously and return immediately; the report arrives as a "
                                   "background notification (default false: block until the report is ready).",
                },
            },
            "required": ["objective", "context"],
        },
        handler=lambda **kw: manager.dispatch(
            objective=kw["objective"],
            context=kw["context"],
            max_turns=kw.get("max_turns", DEFAULT_MAX_TURNS),
            token_budget=kw.get("token_budget", DEFAULT_TOKEN_BUDGET),
            model=kw.get("model"),
            run_in_background=kw.get("run_in_background", False),
        ),
    )
