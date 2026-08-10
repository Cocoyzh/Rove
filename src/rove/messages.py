from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

MessageRole = Literal["user", "assistant", "tool"]


@dataclass
class ToolCall:
    tool_id: str
    tool_name: str
    tool_args: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
        }


@dataclass
class Message:
    role: MessageRole
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.role == "assistant":
            if self.tool_call_id is not None:
                raise ValueError("assistant 消息不能带 tool_call_id")
        elif self.role == "tool":
            if self.tool_call_id is None:
                raise ValueError("tool 消息必须带 tool_call_id")
            if self.tool_calls is not None:
                raise ValueError("tool 消息不能带 tool_calls")
        else:  # user
            if self.tool_calls is not None or self.tool_call_id is not None:
                raise ValueError("user 消息不能带 tool_calls / tool_call_id")

    def to_dict(self) -> dict:
        d: dict = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls is not None:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        return d
