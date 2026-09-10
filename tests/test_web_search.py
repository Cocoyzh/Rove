"""web_search 工具测试：全部 mock HTTP，不发真实网络请求"""
import json
import unittest
from unittest.mock import MagicMock, patch

from rove.tools.web_search import (
    DuckDuckGoBackend,
    TavilyBackend,
    _run_web_search,
    get_search_backend,
    web_search_tool,
    MAX_SNIPPET_CHARS,
)


def _mock_urlopen(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class TestBackendSelection(unittest.TestCase):
    def test_auto_uses_tavily_when_key_present(self):
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "auto", "TAVILY_API_KEY": "k"}):
            self.assertIsInstance(get_search_backend(), TavilyBackend)

    def test_auto_falls_back_to_duckduckgo(self):
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "auto"}, clear=False):
            import os
            os.environ.pop("TAVILY_API_KEY", None)
            self.assertIsInstance(get_search_backend(), DuckDuckGoBackend)

    def test_explicit_none_disables(self):
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "none"}):
            self.assertIsNone(get_search_backend())

    def test_tavily_without_key_raises(self):
        import os
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "tavily"}, clear=False):
            os.environ.pop("TAVILY_API_KEY", None)
            with self.assertRaises(ValueError):
                get_search_backend()


class TestTavilyBackend(unittest.TestCase):
    def test_search_parses_and_caps(self):
        payload = json.dumps({"results": [
            {"title": "Result A", "url": "https://a.example", "content": "c" * (MAX_SNIPPET_CHARS + 100)},
            {"title": "Result B", "url": "https://b.example", "content": "short"},
            {"title": "Result C", "url": "https://c.example", "content": "x"},
        ]}).encode("utf-8")

        backend = TavilyBackend("test-key")
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            results = backend.search("rove harness", max_results=2)

        self.assertEqual(len(results), 2)  # max_results 生效
        self.assertEqual(results[0]["url"], "https://a.example")
        self.assertEqual(len(results[0]["snippet"]), MAX_SNIPPET_CHARS)  # 摘要截断
        self.assertEqual(results[1]["snippet"], "short")

    def test_search_error_propagates(self):
        backend = TavilyBackend("test-key")
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            with self.assertRaises(OSError):
                backend.search("q", 5)


class TestDuckDuckGoBackend(unittest.TestCase):
    HTML = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example%2Frove&amp;rut=xyz">
        Rove <b>Docs</b>
      </a>
      <a class="result__snippet" href="#">The <em>multi-agent</em> harness</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://direct.example/page">Direct Link</a>
      <a class="result__snippet" href="#">Second snippet</a>
    </div>
    """

    def test_parse_unwrap_and_strip_tags(self):
        backend = DuckDuckGoBackend()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(self.HTML.encode("utf-8"))):
            results = backend.search("rove harness", max_results=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://docs.example/rove")  # uddg 已解包
        self.assertEqual(results[0]["title"], "Rove Docs")  # HTML标签已剥离
        self.assertEqual(results[0]["snippet"], "The multi-agent harness")
        self.assertEqual(results[1]["url"], "https://direct.example/page")  # 非跳转链接原样保留


class TestWebSearchTool(unittest.TestCase):
    def test_tool_registration(self):
        self.assertEqual(web_search_tool.name, "web_search")
        self.assertIsNotNone(web_search_tool.handler)
        self.assertEqual(web_search_tool.input_schema["required"], ["query"])

    def test_empty_query_rejected(self):
        self.assertTrue(_run_web_search("   ").startswith("Error:"))

    def test_disabled_provider_message(self):
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "none"}):
            out = _run_web_search("python 3.13 release notes")
        self.assertTrue(out.startswith("Info:"), out)

    def test_backend_error_degrades_gracefully(self):
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "duckduckgo"}):
            with patch("urllib.request.urlopen", side_effect=OSError("blocked by anti-bot")):
                out = _run_web_search("some query")
        self.assertTrue(out.startswith("Error: web search failed (duckduckgo)"), out)

    def test_success_output_format(self):
        payload = json.dumps({"results": [
            {"title": "Doc", "url": "https://d.example", "content": "content here"},
        ]}).encode("utf-8")
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "k"}):
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
                out = _run_web_search("rove docs", max_results=1)
        self.assertIn("Web search: rove docs (via tavily)", out)
        self.assertIn("[1] Doc", out)
        self.assertIn("https://d.example", out)

    def test_invalid_max_results_falls_back_to_default(self):
        payload = json.dumps({"results": [
            {"title": f"T{i}", "url": f"https://{i}.example", "content": "c"} for i in range(8)
        ]}).encode("utf-8")
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "k"}):
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
                out = _run_web_search("q", max_results="not-a-number")
        self.assertIn("[5]", out)  # 默认 5 条
        self.assertNotIn("[6]", out)

    def test_results_capped_at_ten(self):
        payload = json.dumps({"results": [
            {"title": f"T{i}", "url": f"https://{i}.example", "content": "c"} for i in range(15)
        ]}).encode("utf-8")
        with patch.dict("os.environ", {"ROVE_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "k"}):
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
                out = _run_web_search("q", max_results=50)
        self.assertIn("[10]", out)
        self.assertNotIn("[11]", out)


if __name__ == "__main__":
    unittest.main()
