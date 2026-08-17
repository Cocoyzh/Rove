from abc import ABC, abstractmethod
from typing import List, Optional, Callable
from rove.llm import LLMResponse, LLMRequest, Usage
from rove.messages import Message, ToolCall
import time

CONTEXT_WINDOWS = {
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 1_000_000

class BaseLLMAdapter(ABC):
    def __init__(self, model: str, api_key: str, base_url: Optional[str], timeout: Optional[int],
                 context_window: Optional[int] = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0
        self.last_input_tokens = 0
        self.context_window = context_window or CONTEXT_WINDOWS.get(self.model, DEFAULT_CONTEXT_WINDOW)

    def _record_usage(self, response: LLMResponse) -> LLMResponse:
        if response.usage:
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            self.call_count += 1
            self.last_input_tokens = response.usage.input_tokens
        return response

    @abstractmethod
    def _create_client(self):
        """每个provider一个Client"""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """"""

    @abstractmethod
    def stream(self, request: LLMRequest,
               on_text: Optional[Callable[[str], None]] = None) -> LLMResponse:
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

    @staticmethod
    def _build_response(message, latency_ms: int = 0) -> LLMResponse:
        contents = []
        tool_calls: list[ToolCall] = []

        for block in message.content:
            if block.type == "text":
                contents.append(block.text)

            elif block.type == "tool_use":
                tool_id = block.id
                tool_name = block.name
                tool_args = block.input
                tool_calls.append(ToolCall(tool_id=tool_id,
                                           tool_name=tool_name,
                                           tool_args=tool_args))
        text = "\n".join(contents)
        usage = None
        if hasattr(message, "usage"):
            usage = Usage(input_tokens=message.usage.input_tokens,
                          output_tokens=message.usage.output_tokens)
        return LLMResponse(
            content=text,
            tool_calls=tool_calls,
            model=message.model,
            stop_reason=str(message.stop_reason),
            latency_ms=latency_ms,
            usage=usage
        )


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

        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)

        return self._record_usage(self._build_response(message=response, latency_ms=latency_ms))

    def stream(self, request: LLMRequest,
               on_text: Optional[Callable[[str], None]] = None) -> LLMResponse:
        if not self._client:
            self._client = self._create_client()

        start_time = time.time()
        with self._client.messages.stream(max_tokens=request.max_tokens,
                                          messages=self._convert_messages(request.messages),  # type: ignore[arg-type]
                                          system=request.system_prompt,
                                          tools=request.tools,
                                          model=self.model) as stream:
            if on_text:
                for text in stream.text_stream:
                    on_text(text)
            final = stream.get_final_message()
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)

        return self._record_usage(self._build_response(message=final, latency_ms=latency_ms))