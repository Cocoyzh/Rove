from dataclasses import dataclass
from typing import List, Optional
from rove.messages import Message, ToolCall


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