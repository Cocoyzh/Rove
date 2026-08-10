from abc import ABC, abstractmethod
from typing import List, Optional
from rove.llm import LLMResponse, LLMRequest
from rove.messages import Message, ToolCall
import time

class BaseLLMAdapter(ABC):
    def __init__(self, model: str, api_key: str, base_url: Optional[str], timeout: Optional[int]):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client = None

    @abstractmethod
    def _create_client(self):
        """每个provider一个Client"""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """"""

class AnthropicLLMAdapter(BaseLLMAdapter):
    def _create_client(self):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("请先安装Anthropic: pip install anthropic")

        return Anthropic(base_url=self.base_url,
                         api_key=self.api_key,
                         timeout=self.timeout)

    @staticmethod
    def _convert_messages(messages: List[Message]) -> List[dict]:
        result: List[dict] = []
        for msg in messages:
            if msg.role == "user":
                result.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                blocks: list = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls or []:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.tool_id,
                        "name": tc.tool_name,
                        "input": tc.tool_args,
                    })
                result.append({"role": "assistant", "content": blocks})
            else:  # role == "tool"
                block = {"type": "tool_result",
                         "tool_use_id": msg.tool_call_id,
                         "content": msg.content}
                last = result[-1] if result else None
                if (last is not None and last["role"] == "user"
                        and isinstance(last["content"], list)):
                    last["content"].append(block)
                else:
                    result.append({"role": "user", "content": [block]})
        return result

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self._client:
            self._client = self._create_client()

        start_time = time.time()

        response = self._client.messages.create(
            messages=self._convert_messages(request.messages),  # type: ignore[arg-type]
            model=self.model,
            system=request.system_prompt,
            tools=request.tools,
            max_tokens=request.max_tokens
        )

        tool_calls: list[ToolCall] = []
        contents = []
        for block in response.content:
            if block.type == "text":
                contents.append(block.text)

            if block.type == "tool_use":
                tool_id = block.id
                tool_name = block.name
                tool_args = block.input
                tool_calls.append(ToolCall(tool_id=tool_id,
                                           tool_name=tool_name,
                                           tool_args=tool_args))
        text = "\n".join(contents)
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)

        return LLMResponse(
            content=text,
            tool_calls=tool_calls,
            model=response.model,
            stop_reason=str(response.stop_reason),
            latency_ms=latency_ms
        )

