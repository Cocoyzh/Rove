from abc import ABC, abstractmethod
import os
from typing import List, Optional, Callable
from rich.console import Console
from rove.llm import LLMResponse, LLMRequest, Usage
from rove.messages import Message, ToolCall
from rove.usage_ledger import LEDGER
import time

console = Console()

CONTEXT_WINDOWS = {
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 1_000_000

# ---------------- 显式缓存断点（context-optimize P2） ----------------
# LLM_CACHE_MODE:
#   auto     —— 不传 cache_control，依赖端点隐式缓存（GLM/DeepSeek 自动缓存），仅做前缀稳定化
#   explicit —— 传入显式 ephemeral 断点（仅限 Anthropic 官方或已验证支持的端点）
#   off      —— 显式关闭，请求行为与 auto 一致（服务端隐式缓存无法从客户端关闭）
CACHE_MODES = ("auto", "explicit", "off")
DEFAULT_CACHE_MODE = "auto"

# 默认放行显式断点的端点特征；其他端点需实测容忍 cache_control 后显式开启
EXPLICIT_CACHE_HOST_MARKERS = ("anthropic.com",)

# 断点 2 打在保留窗口边缘（与 L1 KEEP_TAIL 对齐），使 L1 剪除区不落在断点之前的稳定前缀里
CACHE_BREAKPOINT_TAIL_MESSAGES = 35

_CACHE_CONTROL = {"type": "ephemeral"}


def resolve_cache_mode(value: Optional[str]) -> str:
    """解析 LLM_CACHE_MODE 配置；非法值回退 auto 并告警。"""
    mode = (value or DEFAULT_CACHE_MODE).strip().lower()
    if mode not in CACHE_MODES:
        console.print(f"[yellow]⚠ 未知 LLM_CACHE_MODE={value!r}，回退为 {DEFAULT_CACHE_MODE}[/yellow]")
        return DEFAULT_CACHE_MODE
    return mode


# 思考模型耗尽输出 Token 时的兜底文本前缀（_build_response），
# 供下游（如 L4 摘要）识别"拿到的是思维链碎片而非正式产出"
THINKING_FALLBACK_MARKER = "*(思考中已达Token上限)*"


def _usage_get(raw: object, name: str):
    """兼容对象与 dict 两种 usage 载体取字段，缺失一律回 0。"""
    if raw is None:
        return 0
    if isinstance(raw, dict):
        return raw.get(name) or 0
    return getattr(raw, name, 0) or 0


def _normalize_usage(raw) -> "Usage":
    """把 Anthropic / GLM 兼容层两种 usage 形态归一化为 Usage。

    - Anthropic 口径：input_tokens 已剔除缓存部分，缓存读/写单列字段；
    - GLM(OpenAI 风格) 口径：prompt 总量含命中部分，仅有
      prompt_tokens_details.cached_tokens 时未命中输入 = 总量 - 命中。
    """
    input_tokens = _usage_get(raw, "input_tokens") or _usage_get(raw, "prompt_tokens")
    output_tokens = _usage_get(raw, "output_tokens") or _usage_get(raw, "completion_tokens")
    cache_read = _usage_get(raw, "cache_read_input_tokens")
    cache_creation = _usage_get(raw, "cache_creation_input_tokens")
    if not cache_read:
        cached = _usage_get(_usage_get(raw, "prompt_tokens_details"), "cached_tokens")
        if cached:
            cache_read = cached
            input_tokens = max(0, input_tokens - cached)
    return Usage(input_tokens=int(input_tokens),
                 output_tokens=int(output_tokens),
                 cache_read_tokens=int(cache_read),
                 cache_creation_tokens=int(cache_creation))

class BaseLLMAdapter(ABC):
    def __init__(self, model: str, api_key: str, base_url: Optional[str], timeout: Optional[int],
                 context_window: Optional[int] = None, cache_mode: Optional[str] = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self.call_count = 0
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.cjk_token_ratio = 1.3
        self.context_window = context_window or CONTEXT_WINDOWS.get(self.model, DEFAULT_CONTEXT_WINDOW)
        # P2：缓存断点模式（auto/explicit/off），显式传参优先于环境变量
        self.cache_mode = resolve_cache_mode(cache_mode if cache_mode is not None
                                             else os.getenv("LLM_CACHE_MODE"))
        self._explicit_cache_allowed = self._endpoint_supports_explicit_cache()
        self._warned_explicit_fallback = False
        # P4：水位估算误差校准（详见 set_pending_estimate / estimate_messages_tokens）
        self.estimate_scale = 1.0
        self._pending_estimate: Optional[int] = None
        self._calibration_armed = False

    def _endpoint_supports_explicit_cache(self) -> bool:
        """仅 Anthropic 官方端点默认放行显式断点；其他端点需实测容忍后
        用 LLM_CACHE_EXPLICIT_ALLOWED=1 显式 attest 开启。"""
        if os.getenv("LLM_CACHE_EXPLICIT_ALLOWED", "").strip().lower() in ("1", "true", "yes"):
            return True
        if not self.base_url:
            return True
        host = str(self.base_url).lower()
        return any(marker in host for marker in EXPLICIT_CACHE_HOST_MARKERS)

    @staticmethod
    def _apply_explicit_cache_control(system, converted_messages: List[dict], tools: list):
        """在已转换的请求载荷上放置显式 ephemeral 断点（最多 3 个，≤ Anthropic 上限 4）。

        断点 1：system 块 + 最后一个工具定义之后（tools + system 是 agent 前缀最稳定的两层）；
        断点 2：历史倒数第 K 条消息（保留窗口边缘）。
        返回新对象，不改动入参（converted_messages 与 self.messages 共享元素）。
        """
        if tools:
            tools = [dict(t) for t in tools]
            tools[-1] = {**tools[-1], "cache_control": _CACHE_CONTROL}
        if system:
            system = [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL}]

        messages = [dict(m) for m in converted_messages]
        if messages:
            idx = max(0, len(messages) - CACHE_BREAKPOINT_TAIL_MESSAGES)
            target = messages[idx]
            content = target.get("content")
            if isinstance(content, str):
                target["content"] = [{"type": "text", "text": content,
                                      "cache_control": _CACHE_CONTROL}]
            elif isinstance(content, list) and content:
                marked = dict(content[-1])
                marked["cache_control"] = _CACHE_CONTROL
                target["content"] = content[:-1] + [marked]
        return system, tools, messages

    def _prepare_payload(self, request: LLMRequest):
        """按 cache_mode 装配 system / tools / messages；explicit 仅在已验证端点生效。"""
        messages = self._convert_messages(request.messages)
        system, tools = request.system_prompt, request.tools
        if self.cache_mode == "explicit":
            if self._explicit_cache_allowed:
                system, tools, messages = self._apply_explicit_cache_control(system, messages, tools)
            elif not self._warned_explicit_fallback:
                console.print("[yellow]⚠ 当前端点未验证支持 cache_control，explicit 回退为 auto"
                              "（仅做前缀稳定化）[/yellow]")
                self._warned_explicit_fallback = True
        return system, tools, messages

    def set_pending_estimate(self, tokens: Optional[int]) -> None:
        """记录本轮调用前压缩管线对全部输入的估算值，供 _record_usage 校准 estimate_scale。

        None 表示本轮估算不可信（如刚刚发生过压缩改写），跳过校准。
        """
        self._pending_estimate = tokens
        self._calibration_armed = False

    def _record_usage(self, response: LLMResponse) -> LLMResponse:
        if response.usage:
            usage = response.usage
            # total_input_tokens 维持"全部输入规模"口径（未命中 + 缓存读 + 缓存写），
            # 与上下文占用、成本汇总的历史语义一致；缓存明细单独累计
            self.total_input_tokens += usage.context_input_tokens
            self.total_output_tokens += usage.output_tokens
            self.total_cache_read_tokens += usage.cache_read_tokens
            self.total_cache_creation_tokens += usage.cache_creation_tokens
            self.call_count += 1
            self.last_input_tokens = usage.context_input_tokens
            self.last_output_tokens = usage.output_tokens

            # P4：水位触发误差校准——用 API 真实回报的输入规模修正估算比例（EMA），
            # 只在 stream 主调用且存在本轮估算时进行（L4 摘要等 complete 路径不参与）
            pending = self._pending_estimate
            if self._calibration_armed and pending and usage.context_input_tokens > 0:
                observed_ratio = usage.context_input_tokens / pending
                self.estimate_scale = round(
                    min(2.0, max(0.5, self.estimate_scale * 0.7 + observed_ratio * 0.3)), 3)
            self._calibration_armed = False
            self._pending_estimate = None

            LEDGER.record(model=response.model or self.model,
                          usage=usage, latency_ms=response.latency_ms)

            content = response.content or ""
            c_len = len(content)
            # 校准仅取纯文本响应：tool_use 的 JSON 结构开销与思考 token
            # 同样计入 output_tokens，混入会把 observed 比例系统性抬高
            if c_len > 0 and not response.tool_calls and response.usage.output_tokens > 0:
                b_len = len(content.encode("utf-8", errors="ignore"))
                non_ascii = (b_len - c_len) // 2
                if non_ascii >= 20:
                    ascii_chars = c_len - non_ascii
                    remaining = response.usage.output_tokens - (ascii_chars / 3.8)
                    if remaining > 0:
                        observed = max(0.5, min(2.5, remaining / non_ascii))
                        self.cjk_token_ratio = round(self.cjk_token_ratio * 0.5 + observed * 0.5, 3)
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
                last = result[-1] if result else None
                if last is not None and last["role"] == "user":
                    if isinstance(last["content"], str):
                        last["content"] = f"{last['content']}\n\n{msg.content or ''}"
                    elif isinstance(last["content"], list):
                        last["content"].append({"type": "text", "text": msg.content or ""})
                else:
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
                if not blocks:
                    blocks.append({"type": "text", "text": "(empty)"})
                result.append({"role": "assistant", "content": blocks})
            else:  # role == "tool"
                block = {"type": "tool_result",
                         "tool_use_id": msg.tool_call_id,
                         "content": msg.content}
                last = result[-1] if result else None
                if last is not None and last["role"] == "user":
                    if isinstance(last["content"], list):
                        last["content"].append(block)
                    else:
                        last["content"] = [{"type": "text", "text": last["content"]}, block]
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
        if not text and not tool_calls:
            # 兼容仅有思考块或耗尽输出 Token 的场景
            for block in message.content:
                if getattr(block, "type", "") == "thinking" and getattr(block, "thinking", ""):
                    text = f"{THINKING_FALLBACK_MARKER}: {block.thinking[:200]}..."
                    break
        usage = None
        if hasattr(message, "usage") and message.usage is not None:
            # 保留缓存读/写原始值，不再合并进 input_tokens 丢弃
            usage = _normalize_usage(message.usage)
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
        system, tools, messages = self._prepare_payload(request)

        response = self._client.messages.create(
            messages=messages,
            model=self.model,
            system=system,
            tools=tools,
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
        system, tools, messages = self._prepare_payload(request)
        # 仅 stream 主调用参与水位校准（complete 可能是 L4 摘要等旁路调用）
        self._calibration_armed = self._pending_estimate is not None

        with self._client.messages.stream(max_tokens=request.max_tokens,
                                          messages=messages,
                                          system=system,
                                          tools=tools,
                                          model=self.model) as stream:
            if on_text:
                for text in stream.text_stream:
                    on_text(text)
            final = stream.get_final_message()
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)

        return self._record_usage(self._build_response(message=final, latency_ms=latency_ms))