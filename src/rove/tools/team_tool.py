"""
Lead Agent
"""
from ..tools.agent_teams import TeammateManger
from ..tool_registry import Tool
import json
from ..tools.protocol import build_protocol_tools
from ..tools.message_bus import BUS

VALID_MSG_TYPES = {
    "message",
    "broadcast",
}

def build_team_tools(manager: TeammateManger):
    return [
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
            description="List all teammates with name, role, status.",
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
