import json
import time
from pathlib import Path
import threading
from typing import Optional
from ..tool_registry import ToolRegistry, Tool
from ..tools.file_tools import FILE_TOOLS
from ..tools.protocol import build_protocol_tools, respond_and_send
from ..tools.message_bus import BUS, MessageBus, VALID_MSG_TYPES
from rove.permissions import APPROVAL_MANAGER, PermissionPolicy
from rove.paths import WORKSPACE_ROOT
from rove.llm_adapters import BaseLLMAdapter
from rove.llm import LLMResponse, LLMRequest
from rove.messages import Message
from rove.call_trace import CallTracer, new_session_id

# Teammate 循环节奏与预算（plans/subagent-optimize.md P2）
POLL_INTERVAL = 5        # 收件箱轮询周期（秒）
IDLE_TIMEOUT = 60        # 空闲自弃等待时长（秒）
DEFAULT_MAX_TOTAL_TURNS = 40   # 全生命周期 LLM 轮数预算，超限走优雅结束
HEARTBEAT_STALE_TURNS = 2      # 心跳过期阈值 = 2 × 轮询周期


class TeammateManager:
    def __init__(self, team_dir: Path, task_manager=None, llm: BaseLLMAdapter = None,
                 poll_interval: float = POLL_INTERVAL,
                 idle_timeout: float = IDLE_TIMEOUT,
                 max_total_turns: int = DEFAULT_MAX_TOTAL_TURNS,
                 bus: MessageBus = BUS,
                 tracer: Optional[CallTracer] = None):
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads = {}
        self._task_manager = task_manager
        self._tool_registries: dict[str, ToolRegistry] = {}
        self._llm = llm
        self._bus = bus
        self.poll_interval = poll_interval
        self.idle_timeout = idle_timeout
        self.max_total_turns = max_total_turns
        # 调用级追踪：Web 会话注入与 lead 共享的 tracer；REPL 自建独立会话文件
        self._tracer = tracer if tracer is not None else CallTracer(new_session_id("repl"))

    @property
    def llm(self) -> BaseLLMAdapter:
        """本团队绑定的主模型适配器（dispatch_subagent 派发时复用）。"""
        return self._llm

    @property
    def tracer(self) -> CallTracer:
        """会话级 CallTracer，供运行时把 lead 视图挂到同一 trace 文件。"""
        return self._tracer

    def _load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def _find_member(self, name: str):
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def _set_status(self, name: str, status: str):
        member = self._find_member(name)
        if member:
            member["status"] = status
            self._save_config()

    # ---------- 心跳与死亡感知（P2.1） ----------

    def _heartbeat_path(self, name: str) -> Path:
        return self.dir / "heartbeat" / name

    def _touch_heartbeat(self, name: str) -> None:
        """mtime 即心跳：每轮工作与每次空闲轮询都刷新。"""
        path = self._heartbeat_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(time.time())))

    def _is_heartbeat_stale(self, name: str) -> bool:
        path = self._heartbeat_path(name)
        if not path.exists():
            return True
        return (time.time() - path.stat().st_mtime) > HEARTBEAT_STALE_TURNS * self.poll_interval

    def _check_health(self) -> None:
        """死亡感知：线程已退出（或本进程从未启动）且心跳过期 → 标记 crashed。

        覆盖两类崩溃：线程体内未捕获异常（由 _teammate_loop 兜底先行标记，
        此处兜底复核）与 kill -9 / 进程重启后遗留的 working/idle 成员。
        已优雅退出的 shutdown 成员不算崩溃。
        """
        changed = False
        for m in self.config["members"]:
            name = m["name"]
            if m.get("status") in ("shutdown", "crashed"):
                continue
            thread = self.threads.get(name)
            if thread is not None and thread.is_alive():
                continue
            if self._is_heartbeat_stale(name):
                m["status"] = "crashed"
                changed = True
        if changed:
            self._save_config()

    # ---------- 优雅结束与取消（P2.2 / P2.3） ----------

    @staticmethod
    def _is_cancel(msg: dict) -> bool:
        return (msg.get("type") == "protocol_request"
                and msg.get("protocol_type") == "cancel")

    def _handle_cancel(self, name: str, msg: dict) -> None:
        """应答取消协议并通知发起方；实际收尾由调用方的优雅结束路径完成。"""
        try:
            respond_and_send(
                name, msg["request_id"], True,
                {"reason": f"{name} acknowledged cancel, wrapping up."})
        except Exception:
            pass

    def _generate_summary(self, name: str, messages: list, reason: str) -> str:
        """结束前向 lead 汇报：做了什么 / 卡在哪 / 交接建议。"""
        tail_lines = []
        for m in messages[-6:]:
            if m.content:
                tail_lines.append(f"[{m.role}] {m.content[:300]}")
        tail = "\n".join(tail_lines)
        try:
            resp: LLMResponse = self._llm.complete(LLMRequest(
                messages=[Message(role="user", content=(
                    "你即将退出团队。请用不超过 150 字向 lead 发一条工作总结，"
                    "格式：做了什么 / 卡在哪 / 交接建议。"
                    f"退出原因：{reason}\n\n近期对话片段：\n{tail}"))],
                tools=[], max_tokens=600, system_prompt=None))
            if resp.content and resp.content.strip():
                return resp.content.strip()
        except Exception:
            pass
        last_text = next((m.content for m in reversed(messages)
                          if m.role == "assistant" and m.content), "")
        return (f"[{name}] 工作总结（{reason}）：最后输出片段：{last_text[:500] or '（无）'}")

    def _graceful_exit(self, name: str, messages: list, reason: str) -> None:
        """空闲自弃 / 被取消 / 轮数超限的共同出口：先发总结，再置 shutdown。"""
        summary = self._generate_summary(name, messages, reason)
        try:
            self._bus.send(name, "lead", summary, msg_type="message")
        except Exception:
            pass
        self._set_status(name, "shutdown")

    # ---------- 派生与主循环 ----------

    @staticmethod
    def _make_identity_block(name, role, team_name) -> Message:
        return Message(
            role="user",
            content=f"<identity> You are {name}, role: {role}, team:{team_name}."
                    f"Continue your work.</identity>"
        )

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find_member(name)
        if member:
            if member.get("status") not in ("idle", "shutdown", "crashed"):
                return f"Error: '{name}' is currently {member['status']}"
            member['status'] = 'working'
            member['role'] = role
        else:
            member = {
                "name": name,
                "role": role,
                "status": 'working',
            }
            self.config["members"].append(member)

        self._save_config()

        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt),
            daemon=True,
        )

        self.threads[name] = thread
        thread.start()

        return f"Spawned '{name}' (role: {role})"

    def _teammate_loop(self, name: str, role: str, prompt: str):
        """线程体：异常统一捕获，标记 crashed 并写 lead 收件箱，不允许静默死亡。"""
        try:
            self._loop_inner(name, role, prompt)
        except Exception as e:
            self._set_status(name, "crashed")
            try:
                self._bus.send(name, "lead",
                         f"Teammate {name} crashed: {type(e).__name__}: {e}",
                         msg_type="message")
            except Exception:
                pass

    def _loop_inner(self, name: str, role: str, prompt: str):
        team_name = self.config["team_name"]
        agent_tracer = self._tracer.for_agent(name)
        sys_prompt = (
            f"You are '{name}', role: {role}, team: {team_name}. "
            f"You are an autonomous teammate. The lead creates tasks on a shared board and you find and complete them yourself.\n\n"
            f"Workflow:\n"
            f"1. Use scan_tasks to see the task board, claim_task to lock an unclaimed task.\n"
            f"2. Work on your task — read, edit, and run code as needed.\n"
            f"3. When finished, report to lead via send_message. The lead will mark the task as completed.\n"
            f"4. Use scan_tasks again to find the next unclaimed task. Repeat.\n"
            f"5. When the board is empty, use idle to enter polling. You will be woken when new inbox messages arrive.\n\n"
            f"Protocols:\n"
            f"- If you receive a protocol_request in your inbox, respond with protocol_response.\n"
            f"- shutdown: Approve if you have no pending work, reject with a reason otherwise.\n"
            f"- plan_review: Review the plan and respond with feedback in response_payload.\n\n"
            f"Communication:\n"
            f"- Use send_message(to=\"lead\", ...) to report progress, ask questions, or signal completion.\n"
            f"- Read your inbox regularly — the lead may send you messages or protocol requests."
        )
        messages: list[Message] = [Message(role="user", content=prompt)]
        first_llm_call = True
        total_turns = 0

        while True:
            # ========== WORK ==========
            self._set_status(name, "working")
            idle_requested = False
            tools = self._teammate_tools(name)

            for _ in range(50):
                if total_turns >= self.max_total_turns:
                    self._graceful_exit(name, messages, reason=f"已达最大轮数预算（{self.max_total_turns}）")
                    return

                inbox = self._bus.read_inbox(name)
                for msg in inbox:
                    if self._is_cancel(msg):
                        self._handle_cancel(name, msg)
                        self._graceful_exit(name, messages, reason="被 lead 取消")
                        return
                    messages.append(Message(role="user", content=json.dumps(msg)))

                self._touch_heartbeat(name)

                seq = None
                try:
                    request = LLMRequest(messages=messages, tools=tools,
                                         max_tokens=8000, system_prompt=sys_prompt)
                    seq = agent_tracer.llm_start(
                        query=prompt if first_llm_call else None,
                        composition={
                            "system_segments": ["teammate_role"],
                            "memory_block": False,
                            "message_count": len(messages),
                            "tool_count": len(tools),
                        },
                    )
                    first_llm_call = False
                    response: LLMResponse = self._llm.complete(request)
                    agent_tracer.llm_end(seq, response)
                except Exception as e:
                    agent_tracer.llm_error(seq, str(e))
                    # 交给 _teammate_loop 的崩溃兜底（原先静默置 idle 返回）
                    raise
                total_turns += 1
                self._touch_heartbeat(name)

                messages.append(Message(role="assistant",
                                        content=response.content,
                                        tool_calls=response.tool_calls))

                if response.stop_reason != "tool_use":
                    break

                for tc in response.tool_calls:
                    tseq = agent_tracer.tool_start(tc.tool_id, tc.tool_name, tc.tool_args)
                    start_t = time.time()
                    try:
                        output = self._exec(name, tc.tool_name, tc.tool_args)
                        if tc.tool_name == "idle":
                            idle_requested = True
                        is_error = False
                    except Exception as e:
                        output = f"Tool error: {e}"
                        is_error = True
                    cost_ms = int((time.time() - start_t) * 1000)
                    agent_tracer.tool_end(tseq, tc.tool_id, tc.tool_name,
                                          str(output), is_error, cost_ms)

                    messages.append(Message(role="tool",
                                            tool_call_id=tc.tool_id,
                                            content=str(output)))

                if idle_requested:
                    break

            # ========== IDLE ==========
            self._set_status(name, "idle")
            resume = False

            ticks = max(int(self.idle_timeout // max(self.poll_interval, 0.001)), 1)
            for _ in range(ticks):
                time.sleep(self.poll_interval)
                self._touch_heartbeat(name)

                inbox = self._bus.read_inbox(name)
                for msg in inbox:
                    if self._is_cancel(msg):
                        self._handle_cancel(name, msg)
                        self._graceful_exit(name, messages, reason="被 lead 取消")
                        return
                    messages.append(Message(role="user", content=json.dumps(msg)))
                    resume = True

                if resume:
                    break

            if not resume:
                self._graceful_exit(name, messages, reason="空闲超时自弃")
                return

            # 压缩后重新注入身份
            if len(messages) <= 3:
                messages.insert(0, self._make_identity_block(name, role, team_name))
                messages.insert(1, Message(role="assistant", content=f"I am {name}. Continuing."))

    def _teammate_tools(self, name):
        return self._teammate_registry(name).get_schemas()

    def _teammate_registry(self, name: str) -> ToolRegistry:
        existing = self._tool_registries.get(name)
        if existing is not None:
            return existing

        registry = ToolRegistry(
            PermissionPolicy(WORKSPACE_ROOT),
            APPROVAL_MANAGER.request,
        )
        teammate_tools = [
            Tool(
                name="send_message",
                description="Send message to a teammate.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "content": {"type": "string"},
                        "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}
                    },
                    "required": ["to", "content"]
                },
                handler= lambda **kw: BUS.send(
                    name,
                    kw["to"],
                    kw["content"],
                    kw.get("msg_type", "message"),
                )
            ),
            Tool(
                name="read_inbox",
                description="Read and drain your inbox.",
                input_schema={
                    "type": "object",
                    "properties": {}
                },
                handler=lambda **kw: json.dumps(BUS.read_inbox(name), indent=2),
            ),
            Tool(
              name="idle",
              description="Signal that you have no more work. Enters idle polling phase",
              input_schema={"type": "object", "properties": {}},
              handler=lambda **kw: "Entering idle phase. Will poll inbox for new messages."
            ),
            *build_protocol_tools(name)
        ]
        if self._task_manager is not None:
            from ..tools.task_tool import build_scan_tasks_tool, build_claim_task_tool
            teammate_tools.extend([
                build_scan_tasks_tool(self._task_manager),
                build_claim_task_tool(self._task_manager, name),
            ])
        # 团队公用知识库：Teammates 只拥有长期记忆的只读检索（Issue #4 Phase 4）
        from ..tools.memory_tools import build_memory_recall_tool
        registry.register_many([*FILE_TOOLS, build_memory_recall_tool(), *teammate_tools])
        self._tool_registries[name] = registry
        return registry

    def _exec(self, sender: str, tool_name: str, args):
        return self._teammate_registry(sender).execute(tool_name, args)

    def list_all(self):
        self._check_health()
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            line = f"{m['name']}, {m['role']}, {m['status']}"
            if m["status"] == "crashed":
                line += " (no heartbeat; reclaim its tasks via reclaim_teammate_tasks)"
            lines.append(line)
        return "\n".join(lines)
