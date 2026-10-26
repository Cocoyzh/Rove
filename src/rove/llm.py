from dataclasses import dataclass
from typing import List, Optional
from rove.messages import Message, ToolCall

@dataclass(frozen=True)
class Usage:
    """单次调用的 token 用量。

    input_tokens 为 API 原始口径的未命中缓存输入；缓存读/写单列，
    三者之和才是本次请求的全部输入规模（context_input_tokens）。
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def context_input_tokens(self) -> int:
        """请求的全部输入规模，用于上下文占用统计。"""
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    @property
    def cache_hit_rate(self) -> float:
        """前缀缓存命中率 = 缓存读 / 全部输入。"""
        denom = self.context_input_tokens
        return self.cache_read_tokens / denom if denom else 0.0

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