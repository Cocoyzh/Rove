"""联网搜索工具：可插拔后端（Tavily / DuckDuckGo）。query 会发往外部服务，所以默认走 ASK 审批。"""

import json
import os
import re
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import List, Optional

from ..tool_registry import Tool

USER_AGENT = "Mozilla/5.0 (compatible; RoveAgent/0.1; +https://github.com/Cocoyzh/Rove)"
SEARCH_TIMEOUT = 15
MAX_SNIPPET_CHARS = 500
MAX_RESULTS = 10
MAX_OUTPUT_CHARS = 8000


class SearchBackend(ABC):
    """搜索后端接口：返回 [{title, url, snippet}, ...]"""

    name = "base"

    @abstractmethod
    def search(self, query: str, max_results: int) -> List[dict]:
        """执行搜索；失败时抛异常由工具层统一降级。"""


class TavilyBackend(SearchBackend):
    """Tavily（为 LLM 调优的搜索 API，免费额度 1000 次/月）"""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("TAVILY_API_KEY is required for the tavily backend")
        self.api_key = api_key

    def search(self, query: str, max_results: int) -> List[dict]:
        payload = json.dumps({
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("content") or "")[:MAX_SNIPPET_CHARS],
            }
            for r in data.get("results", [])
        ][:max_results]


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo HTML 端点（免 key 兜底；HTML 解析脆弱，被反爬时按失败降级）"""

    name = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"

    _RESULT_RE = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    _SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    _TAG_RE = re.compile(r"<[^>]+>")

    def search(self, query: str, max_results: int) -> List[dict]:
        form = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=form, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        links = self._RESULT_RE.findall(html)
        snippets = [self._TAG_RE.sub("", s).strip() for s in self._SNIPPET_RE.findall(html)]

        results = []
        for i, (href, title) in enumerate(links[:max_results]):
            results.append({
                "title": self._TAG_RE.sub("", title).strip(),
                "url": self._unwrap(href),
                "snippet": snippets[i][:MAX_SNIPPET_CHARS] if i < len(snippets) else "",
            })
        return results

    @staticmethod
    def _unwrap(href: str) -> str:
        """DDG 结果链接是 /l/?uddg=<urlencoded> 的跳转包装，取真实 URL"""
        if "uddg=" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" in parsed:
                return parsed["uddg"][0]
        return href


def get_search_backend() -> Optional[SearchBackend]:
    """按 ROVE_SEARCH_PROVIDER 选后端（auto|tavily|duckduckgo|none）；auto 有 TAVILY_API_KEY 用 Tavily，否则 DuckDuckGo。"""
    provider = os.getenv("ROVE_SEARCH_PROVIDER", "auto").strip().lower()
    if provider == "none":
        return None

    tavily_key = os.getenv("TAVILY_API_KEY", "")
    if provider == "tavily":
        if not tavily_key:
            raise ValueError("ROVE_SEARCH_PROVIDER=tavily 但未设置 TAVILY_API_KEY")
        return TavilyBackend(tavily_key)
    if provider == "duckduckgo":
        return DuckDuckGoBackend()
    if provider == "auto" and tavily_key:
        return TavilyBackend(tavily_key)
    return DuckDuckGoBackend()


def _run_web_search(query: str, max_results: int = 5) -> str:
    query = str(query).strip()
    if not query:
        return "Error: query is required"

    try:
        max_results = max(1, min(int(max_results or 5), MAX_RESULTS))
    except (TypeError, ValueError):
        max_results = 5

    try:
        backend = get_search_backend()
    except Exception as e:
        return f"Error: {e}"
    if backend is None:
        return "Info: web search is disabled (ROVE_SEARCH_PROVIDER=none)."

    try:
        results = backend.search(query, max_results)
    except Exception as e:
        return f"Error: web search failed ({backend.name}): {e}"

    if not results:
        return f"No results for: {query}"

    lines = [f"Web search: {query} (via {backend.name})"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r.get('title', '')}\n    {r.get('url', '')}\n    {r.get('snippet', '')}")
    return "\n".join(lines)[:MAX_OUTPUT_CHARS]


web_search_tool = Tool(
    name="web_search",
    description=(
        "Search the public web for current information: error messages, library "
        "docs/versions, release notes. Returns title + URL + snippet per result. "
        "Requires a search backend (ROVE_SEARCH_PROVIDER / TAVILY_API_KEY); "
        "gracefully degrades when unavailable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"},
            "max_results": {"type": "integer", "description": "1-10, default 5"},
        },
        "required": ["query"],
    },
    handler=lambda **kw: _run_web_search(kw["query"], kw.get("max_results", 5)),
)
