from dataclasses import dataclass
from typing import List, Optional
from rove.messages import Message, ToolCall

@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    # 占位字段：context-optimize 分支将补齐缓存三元组（cache_read/creation/ttl），
    # 本分支（tool-call-visualize）先以 cache_read 消费，便于调用检查器展示命中
    cache_read_tokens: int = 0

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
    # 思考块拼接文本（适配器从响应的 thinking blocks 收集），供调用检查器展示思考过程
    thinking: str = ""