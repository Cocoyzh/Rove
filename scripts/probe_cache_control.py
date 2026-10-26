"""P2-4 验证：当前端点对 cache_control 显式断点的容忍度探测（一次性工具）。

向配置端点发送带 ephemeral 断点的最小请求（断点 1：system 块；断点 2：末条
user 消息），共两次相同请求以观察缓存写/读字段。只输出模型名、判定结论与
usage 字段；错误信息经脱敏（剥离 URL 与密钥样式字符串），绝不打印密钥/端点。

    PYTHONPATH=src python scripts/probe_cache_control.py
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.getenv("ROVE_BENCH_DOTENV", ".env"))

from rove.llm_adapters import AnthropicLLMAdapter  # noqa: E402

CC = {"type": "ephemeral"}
SYSTEM = [{"type": "text", "text": "You are a cache tolerance probe. Reply OK.",
           "cache_control": CC}]
MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "Reply with exactly: OK", "cache_control": CC},
]}]


def sanitize(text: str) -> str:
    """错误信息脱敏：剥离 URL 与密钥样式字符串。"""
    text = re.sub(r"https?://\S+", "<url>", str(text))
    text = re.sub(r"\b(?:sk|key|token)-?[A-Za-z0-9_-]{8,}\b", "<redacted>", text,
                  flags=re.IGNORECASE)
    return text[:300]


def main() -> None:
    llm = AnthropicLLMAdapter(model=os.getenv("LLM_MODEL_ID"),
                              api_key=os.getenv("LLM_API_KEY"),
                              base_url=os.getenv("LLM_BASE_URL"),
                              timeout=int(os.getenv("LLM_TIMEOUT", "60")))
    client = llm._create_client()
    print(f"model={llm.model}")

    for attempt in (1, 2):
        try:
            resp = client.messages.create(
                model=llm.model, max_tokens=16,
                system=SYSTEM, messages=MESSAGES,
            )
            usage = resp.usage
            fields = {k: getattr(usage, k, None) for k in (
                "input_tokens", "output_tokens",
                "cache_read_input_tokens", "cache_creation_input_tokens")}
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                fields["prompt_tokens_details.cached_tokens"] = getattr(details, "cached_tokens", None)
            print(f"attempt {attempt}: ACCEPTED  usage={fields}")
        except Exception as e:
            print(f"attempt {attempt}: REJECTED  {type(e).__name__}: {sanitize(e)}")
            break

    print("\n判定：ACCEPTED=端点容忍显式断点（可用 LLM_CACHE_MODE=explicit + "
          "LLM_CACHE_EXPLICIT_ALLOWED=1 开启）；REJECTED=保持默认 auto。")


if __name__ == "__main__":
    main()
