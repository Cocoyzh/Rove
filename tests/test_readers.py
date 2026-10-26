import json
import time
import unittest
from pathlib import Path

from rove.paths import AGENT_TMP_DIR
from rove.tools.file_tools import (
    _extract_rtf_text,
    _run_read,
    _run_read_notebook,
    sniff_file_type,
)

_WORKSPACE = Path(__file__).parent.parent.resolve()


def _scratch_dir() -> Path:
    """在 gitignored 的 .rove/tmp 下为本轮测试开独立草稿目录（不删除）"""
    AGENT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    scratch = AGENT_TMP_DIR / f"test_readers_{int(time.time() * 1000)}_{id(object())}"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _write_scratch(scratch: Path, name: str, data: bytes = None) -> str:
    p = scratch / name
    if data is not None:
        p.write_bytes(data)
    return str(p.relative_to(_WORKSPACE))


class TestFileSniffing(unittest.TestCase):
    """Issue #8：基于魔数而非扩展名的文件类型嗅探

    测试产物统一写入 gitignored 的 .rove/tmp，不做任何删除操作。
    """

    def setUp(self):
        self.workspace = _WORKSPACE
        self.scratch = _scratch_dir()

    def _rel(self, name: str, data: bytes = None) -> str:
        return _write_scratch(self.scratch, name, data)

    def test_sniff_common_types(self):
        self.assertEqual(sniff_file_type(self.workspace / "pyproject.toml"), "text")
        self.assertEqual(sniff_file_type(self.workspace / "tests" / "test_sandbox.py"), "text")

    def test_rtf_disguised_as_txt_is_detected(self):
        name = self._rel("disguise.rtf.txt", b"{\\rtf1\\ansi\\deff0 Hello}")
        self.assertEqual(sniff_file_type(self.workspace / name), "rtf")

    def test_pdf_magic_detected(self):
        name = self._rel("fake.pdf", b"%PDF-1.4\n...")
        self.assertEqual(sniff_file_type(self.workspace / name), "pdf")

    def test_binary_rejected(self):
        name = self._rel("blob.bin", b"abc\x00\x00def")
        self.assertEqual(sniff_file_type(self.workspace / name), "binary")

    def test_rtf_text_extraction(self):
        rtf = rb"{\rtf1\ansi\deff0 {\fonttbl{\f0 Courier;}}\f0\fs18 def hello():\par    print('hi')\par}"
        text = _extract_rtf_text(rtf)
        self.assertIn("def hello():", text)
        self.assertIn("print('hi')", text)
        self.assertNotIn("\\rtf1", text)
        self.assertNotIn("{", text)

    def test_read_file_extracts_rtf_content(self):
        name = self._rel("rtf_content.txt", b"{\\rtf1\\ansi print('algorithm inside')\\par}")
        out = _run_read(name)
        self.assertIn("algorithm inside", out)
        self.assertNotIn("\\rtf1", out)


class TestReadNotebook(unittest.TestCase):
    """Issue #8：read_notebook 结构化读取 .ipynb"""

    def setUp(self):
        self.workspace = _WORKSPACE
        self.scratch = _scratch_dir()

        self.nb_name = _write_scratch(self.scratch, "sample.ipynb")
        nb = {
            "metadata": {"kernelspec": {"name": "python3"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Title\n", "Some docs"]},
                {
                    "cell_type": "code",
                    "execution_count": 3,
                    "source": ["import pandas as pd\n", "df.head()"],
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": ["line1\n", "line2\n"]},
                        {"output_type": "error", "ename": "ValueError", "evalue": "bad column"},
                        {"output_type": "display_data", "data": {"image/png": "<binary>"}},
                    ],
                },
            ],
        }
        (self.workspace / self.nb_name).write_text(json.dumps(nb), encoding="utf-8")

    def test_notebook_structure_extracted(self):
        out = _run_read_notebook(self.nb_name)
        self.assertIn("2 cells", out)
        self.assertIn("kernel=python3", out)
        self.assertIn("# Title", out)
        self.assertIn("df.head()", out)
        self.assertIn("line1", out)
        self.assertIn("[error] ValueError: bad column", out)
        self.assertIn("[display_data] rich media: image/png", out)

    def test_output_truncation(self):
        out = _run_read_notebook(self.nb_name, max_output_chars=5)
        self.assertIn("line1...", out)

    def test_invalid_notebook_returns_error(self):
        name = _write_scratch(self.scratch, "bad.ipynb", b"not json")
        out = _run_read_notebook(name)
        self.assertTrue(out.startswith("Error:"))


if __name__ == "__main__":
    unittest.main()


class TestRtfEscapeEdgeCases(unittest.TestCase):
    """RTF 转义字符边界：\\{ \\} \\\\ 必须先还原再剥组括号"""

    def test_escaped_braces_preserved(self):
        rtf = rb"{\rtf1\ansi code: a\{b\}c\par}"
        text = _extract_rtf_text(rtf)
        self.assertIn("a{b}c", text)

    def test_escaped_backslash_preserved(self):
        rtf = rb"{\rtf1 path C:\\temp\par}"
        text = _extract_rtf_text(rtf)
        self.assertIn("C:\\temp", text)

    def test_group_braces_still_stripped(self):
        rtf = rb"{\rtf1{\fonttbl{\f0 Courier;}}plain text\par}"
        text = _extract_rtf_text(rtf)
        self.assertIn("plain text", text)
        self.assertNotIn("fonttbl", text)


class TestEncodingEdgeCases(unittest.TestCase):
    """编码与二进制嗅探边界"""

    def setUp(self):
        self.workspace = _WORKSPACE
        self.scratch = _scratch_dir()

    def _rel(self, name, data):
        return _write_scratch(self.scratch, name, data)

    def test_utf8_sig_detected_and_read(self):
        name = self._rel("bom.txt", "带 BOM 的中文内容".encode("utf-8-sig"))
        self.assertEqual(sniff_file_type(self.workspace / name), "utf8-sig-text")
        out = _run_read(name)
        self.assertIn("带 BOM 的中文内容", out)

    def test_utf16_detected_and_read(self):
        name = self._rel("u16.txt", "UTF-16 中文".encode("utf-16"))
        self.assertEqual(sniff_file_type(self.workspace / name), "utf16-text")
        out = _run_read(name)
        self.assertIn("UTF-16 中文", out)

    def test_png_magic_refused(self):
        name = self._rel("img.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        out = _run_read(name)
        self.assertTrue(out.startswith("Error:"), out)
        self.assertIn("binary", out)

    def test_missing_file_returns_error(self):
        out = _run_read("no/such/file_xyz.txt")
        self.assertTrue(out.startswith("Error:"), out)


class TestNotebookEdgeCases(unittest.TestCase):
    """read_notebook 边界：纯 Markdown、空 notebook、无输出 cell"""

    def setUp(self):
        self.workspace = _WORKSPACE
        self.scratch = _scratch_dir()

    def _write_nb(self, name, nb):
        rel = _write_scratch(self.scratch, name)
        (self.workspace / rel).write_text(json.dumps(nb), encoding="utf-8")
        return rel

    def test_markdown_only_notebook(self):
        rel = self._write_nb("md_only.ipynb", {
            "cells": [{"cell_type": "markdown", "source": ["# 只有文档"]}],
        })
        out = _run_read_notebook(rel)
        self.assertIn("[markdown]", out)
        self.assertIn("# 只有文档", out)
        self.assertNotIn("--- outputs", out)

    def test_empty_notebook(self):
        rel = self._write_nb("empty.ipynb", {"cells": []})
        out = _run_read_notebook(rel)
        self.assertIn("0 cells", out)

    def test_code_cell_without_outputs(self):
        rel = self._write_nb("no_out.ipynb", {
            "cells": [{"cell_type": "code", "source": ["x = 1"]}],
        })
        out = _run_read_notebook(rel)
        self.assertIn("x = 1", out)
        self.assertIn("0 items", out)

    def test_execute_result_text_plain(self):
        rel = self._write_nb("result.ipynb", {
            "cells": [{
                "cell_type": "code", "execution_count": 1, "source": ["1+1"],
                "outputs": [{"output_type": "execute_result",
                             "data": {"text/plain": ["42"]}}],
            }],
        })
        out = _run_read_notebook(rel)
        self.assertIn("[execute_result] 42", out)
