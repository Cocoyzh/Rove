import json
import time
from pathlib import Path
import threading
from ..tool_registry import ToolRegistry, Tool
from ..tools.file_tools import FILE_TOOLS
from ..tools.protocol import build_protocol_tools
from ..tools.message_bus import BUS, VALID_MSG_TYPES
from rove.permissions import APPROVAL_MANAGER, PermissionPolicy
from rove.paths import WORKSPACE_ROOT
from rove.llm_adapters import BaseLLMAdapter
from rove.llm import LLMResponse, LLMRequest
from rove.messages import Message

class TeammateManger:
    def __init__(self, team_dir: Path, task_manager=None, llm: BaseLLMAdapter = None):
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads = {}
        self._task_manager = task_manager
        self._tool_registries: dict[str, ToolRegistry] = {}
        self._llm = llm

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
            if member.get("status") not in ("idle", "shutdown"):
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
        team_name = self.config["team_name"]
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

        POLL_INTERVAL = 5
        IDLE_TIMEOUT = 60

        while True:
            # ========== WORK ==========
            self._set_status(name, "working")
            idle_requested = False
            tools = self._teammate_tools(name)

            for _ in range(50):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    messages.append(Message(role="user", content=json.dumps(msg)))

                try:
                    request = LLMRequest(messages=messages, tools=tools,
                                         max_tokens=8000, system_prompt=sys_prompt)
                    response: LLMResponse = self._llm.complete(request)
                except Exception:
                    self._set_status(name, "idle")
                    return

                messages.append(Message(role="assistant",
                                        content=response.content,
                                        tool_calls=response.tool_calls))

                if response.stop_reason != "tool_use":
                    break

                for tc in response.tool_calls:
                    try:
                        output = self._exec(name, tc.tool_name, tc.tool_args)
                        if tc.tool_name == "idle":
                            idle_requested = True
                    except Exception as e:
                        output = f"Tool error: {e}"

                    messages.append(Message(role="tool",
                                            tool_call_id=tc.tool_id,
                                            content=str(output)))

                if idle_requested:
                    break

            # ========== IDLE ==========
            self._set_status(name, "idle")
            resume = False

            for _ in range(IDLE_TIMEOUT // max(POLL_INTERVAL, 1)):
                time.sleep(POLL_INTERVAL)

                inbox = BUS.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        messages.append(Message(role="user", content=json.dumps(msg)))
                    resume = True
                    break

            if not resume:
                self._set_status(name, "shutdown")
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
        registry.register_many([*FILE_TOOLS, *teammate_tools])
        self._tool_registries[name] = registry
        return registry

    def _exec(self, sender: str, tool_name: str, args):
        return self._teammate_registry(sender).execute(tool_name, args)

    def list_all(self):
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"{m['name']}, {m['role']}, {m['status']}")
        return "\n".join(lines)
