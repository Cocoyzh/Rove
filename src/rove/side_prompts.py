"""旁路小 prompt 服务（SidePromptService）。

参考业界实践（ChatGPT / Claude.ai / Claude Code 会话命名）：把"会话命名"这类轻量任务
拆成独立短 prompt + 可配廉价模型执行，不进入主会话历史、不污染上下文。
TitleService（generate_title）是第一个实例；后续可挂"会话结束摘要""错误归因"等同框架任务。

社区经验对齐：
- 标题使用中文（验收标准），≤12 字；
- 同一会话只自动命名一次，生成后不再改名（避免中途改名破坏用户预期）；
- 用户手动改名的标题（title_source=manual）永远不被覆盖。
"""

import os
import threading
from typing import Optional, Set

from rove.llm import LLMRequest, LLMResponse
from rove.llm_adapters import BaseLLMAdapter
from rove.messages import Message

TITLE_MAX_CHARS = 12


class SidePromptService:
    """统一执行旁路小任务：独立短 prompt，走 complete()（非流式），失败静默。"""

    def __init__(self, llm: BaseLLMAdapter, title_model_id: Optional[str] = None) -> None:
        self._llm = llm
        self._title_model_id = title_model_id or os.getenv("LLM_TITLE_MODEL_ID") or None
        self._title_adapter: Optional[BaseLLMAdapter] = None
        self._lock = threading.Lock()
        self._titled: Set[str] = set()  # 已自动命名的会话，保证"只生成一次"

    def _adapter(self) -> BaseLLMAdapter:
        """配置了 LLM_TITLE_MODEL_ID 时惰性构建廉价模型适配器，否则复用主模型。"""
        if self._title_model_id and self._title_adapter is None:
            from rove.llm_adapters import AnthropicLLMAdapter

            self._title_adapter = AnthropicLLMAdapter(
                model=self._title_model_id,
                api_key=os.getenv("LLM_API_KEY", ""),
                base_url=os.getenv("LLM_BASE_URL"),
                timeout=int(os.getenv("LLM_TIMEOUT", "30")),
            )
        return self._title_adapter or self._llm

    def generate_title(self, query: str, session_id: Optional[str] = None) -> Optional[str]:
        """根据首轮用户请求生成 ≤12 字的中文标题；同一会话只生成一次，失败返回 None。"""
        if not self._llm.api_key:
            return None  # 无 API Key（如纯单测环境）直接跳过，绝不发起网络请求
        if session_id is not None:
            with self._lock:
                if session_id in self._titled:
                    return None
                self._titled.add(session_id)

        prompt = (
            f"根据以下用户请求生成一个不超过{TITLE_MAX_CHARS}个字的中文标题，概括任务目标。"
            f"直接输出标题本身，不要引号、句号或任何解释。\n\n用户请求：{query[:2000]}"
        )
        request = LLMRequest(
            messages=[Message(role="user", content=prompt)],
            tools=[],
            max_tokens=64,
        )
        try:
            response: LLMResponse = self._adapter().complete(request)
            title = (response.content or "").strip()
            # 容错：取第一行、去包裹引号、截断到上限（中英文混合场景按字符截断）
            title = title.splitlines()[0].strip().strip("\"'“”「」《》。")[:TITLE_MAX_CHARS * 2]
            return title or None
        except Exception:
            return None
