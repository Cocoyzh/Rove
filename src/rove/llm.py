from dataclasses import dataclass
from typing import List, Optional
from rove.messages import Message, ToolCall

@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

@dataclass
class LLMRequest:
    messages: List[Message]
    tools: list
    max_tokens: int
    system_prompt: Optional[str] = None

@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    stop_reason: str
    latency_ms: int = 0
    usage: Optional[Usage] = None