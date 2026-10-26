"""
Lead Agent
"""
from ..tools.agent_teams import TeammateManager
from ..tool_registry import Tool
import json
from ..tools.protocol import build_protocol_tools
from ..tools.message_bus import BUS

VALID_MSG_TYPES = {
    "message",
    "broadcast",
}

def build_team_tools(manager: TeammateManager, task_manager=None):
    tools = [
        Tool(
            name="spawn_teammate",
            description="Spawn a persistent teammate that runs in its own thread.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["name", "role", "prompt"],
            },
            handler= lambda **kw: manager.spawn(kw["name"], kw["role"], kw["prompt"])
        ),
        Tool(
            name="send_message",
            description="Send a message to a teammate's inbox.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"},
                    "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)},
                },
                "required": ["to", "content"],
            },
            handler= lambda **kw: BUS.send("lead", kw['to'], kw['content'], kw.get("msg_type", "message"))
        ),
        Tool(
            name="list_teammates",
            description="List all teammates with name, role, status "
                        "(working/idle/shutdown/crashed; crashed members have stale heartbeat and dead threads).",
            input_schema={
                "type": "object",
                "properties": {}
            },
            handler= lambda **kw: manager.list_all()
        ),
        Tool(
            name="read_inbox",
            description="Read and drain the lead's inbox.",
            input_schema={
                "type": "object",
                "properties": {}
            },
            handler= lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2)
        ),
        *build_protocol_tools("lead")
    ]
    if task_manager is not None:
        tools.append(Tool(
            name="reclaim_teammate_tasks",
            description="Reclaim all in-progress tasks owned by a teammate (typically a crashed one, "
                        "see list_teammates): reset them to pending so others can claim.",
            input_schema={
                "type": "object",
                "properties": {
                    "teammate": {"type": "string", "description": "The teammate whose tasks to reclaim."},
                },
                "required": ["teammate"],
            },
            handler=lambda **kw: task_manager.reclaim(kw["teammate"]),
        ))
    return tools
