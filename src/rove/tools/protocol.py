import time
from dataclasses import dataclass, field
import uuid
import threading
from typing import Any
from rove.tool_registry import Tool
from rove.tools.message_bus import BUS

VALID_PROTOCOL_TYPES = ["shutdown", "plan_review"]
PROTOCOL_RULES = {
    "shutdown": {"initiators": {"lead"}},
    "plan_review": {"initiators": set()},
}

@dataclass
class ProtocolState:
    request_id: str
    protocol_type: str       # shutdown, plan_review
    sender: str
    receiver: str
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None = None
    status: str = "pending"    # pending | approved | rejected
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

class ProtocolManager:
    def __init__(self):
        self.states: dict[str, ProtocolState] = {}
        self._lock = threading.Lock()

    def create(self, protocol_type: str, sender: str, receiver: str,
               request_payload: dict[str, Any] | None = None) -> ProtocolState:
        if protocol_type not in PROTOCOL_RULES:
            raise ValueError(f"Invalid protocol_type: {protocol_type}")

        allowed = PROTOCOL_RULES[protocol_type]['initiators']
        if allowed and sender not in allowed:
            raise PermissionError(f"{sender} cannot initiate {protocol_type}")

        request_id = str(uuid.uuid4())[:8]
        state = ProtocolState(
            request_id=request_id,
            protocol_type=protocol_type,
            sender=sender,
            receiver=receiver,
            request_payload=request_payload or {}
        )
        with self._lock:
            self.states[request_id] = state

        return state

    def respond(self, request_id: str, responder: str, approve: bool,
                 response_payload: dict[str, Any] | None = None) -> ProtocolState:
        with self._lock:
            state = self.states.get(request_id)
            if state is None:
                raise ValueError(f"Unknown request_id: {request_id}")

            if state.receiver != responder:
                raise PermissionError(
                    f"{responder} cannot respond to request {request_id}"
                    f"Expected receiver: {state.receiver}"
                )

            if state.status != "pending":
                raise ValueError(
                    f"Protocol {request_id} is already {state.status}"
                )

            state.status = "approved" if approve else "rejected"
            state.response_payload = response_payload or {}
            state.updated_at = time.time()

        return state

PROTOCOL = ProtocolManager()


def _handle_protocol_request(sender: str, **kw) -> str:
    """BUS.send 胶水 + PROTOCOL 纯状态调用"""
    state = PROTOCOL.create(kw["protocol_type"], sender, kw["receiver"], kw.get("request_payload"))
    request_content = (
        state.request_payload.get("content")
        or state.request_payload.get("plan")
        or state.request_payload.get("reason")
        or f"{state.protocol_type} request"
    )
    BUS.send(
        sender, kw["receiver"], str(request_content),
        extra={
            "type": "protocol_request",
            "request_id": state.request_id,
            "protocol_type": state.protocol_type,
            "request_payload": state.request_payload,
        },
    )
    return f"Protocol {state.request_id} sent to {kw['receiver']}"


def _handle_protocol_response(sender: str, **kw) -> str:
    """BUS.send 胶水 + PROTOCOL 纯状态调用"""
    state = PROTOCOL.respond(kw["request_id"], sender, kw["approve"], kw.get("response_payload"))
    response_content = (
        state.response_payload.get("content")
        or state.response_payload.get("feedback")
        or state.response_payload.get("reason")
        or state.status
    )
    BUS.send(
        sender, state.sender, str(response_content),
        extra={
            "type": "protocol_response",
            "request_id": kw["request_id"],
            "protocol_type": state.protocol_type,
            "status": state.status,
            "response_payload": state.response_payload,
        },
    )
    return f"Protocol {state.request_id} {state.status}"


def build_protocol_tools(sender: str) -> list[Tool]:
    return [
        Tool(
            name="protocol_request",
            description="Initiate a structured protocol that requires the receiver's explicit approval before any action. "
                        "--e.g., shutdown, plan-review",
            input_schema={
                "type": "object",
                "properties": {
                    "protocol_type": {"type": "string", "enum": VALID_PROTOCOL_TYPES},
                    "receiver": {"type": "string"},
                    "request_payload": {
                        "type": "object",
                        "description": (
                            "Business data for the protocol request. "
                            "For plan_review, include plan and optional criteria. "
                            "For shutdown, include reason."
                        ),
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Human-readable request content. If present, this is used as the inbox message content."
                            },
                            "plan": {
                                "type": "string",
                                "description": "For plan_review: the detailed plan to review."
                            },
                            "criteria": {
                                "type": "string",
                                "description": "For plan_review: what the reviewer should check."
                            },
                            "reason": {
                                "type": "string",
                                "description": "For shutdown: why shutdown is being requested."
                            }
                        }
                    }
                },
                "required": ["protocol_type", "receiver"],
            },
            handler=lambda **kw: _handle_protocol_request(sender, **kw),
        ),
        Tool(
            name="protocol_response",
            description="Respond to a pending protocol_request you received in your inbox."
                        "Approve if you agree, reject with a reason if you disagree or have incomplete work."
                        "The response is sent back to the protocol's initiator.",
            input_schema={
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "response_payload": {
                        "type": "object",
                        "description": "Include feedback or reason explaining the approval/rejection.",
                        "properties": {
                            "feedback": {
                                "type": "string",
                                "description": "Review feedback or explanation for approval/rejection."
                            },
                            "reason": {
                                "type": "string",
                                "description": "Reason for shutdown approval/rejection."
                            },
                            "approved_by": {
                                "type": "string",
                                "description": "Name of the responder approving the protocol."
                            }
                        }
                    }
                },
                "required": ["request_id", "approve"],
            },
            handler=lambda **kw: _handle_protocol_response(sender, **kw),
        ),
    ]
