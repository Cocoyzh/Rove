from pathlib import Path
import json
import os
import re
import subprocess
from ..tool_registry import Tool
from rove.paths import WORKSPACE_ROOT, AGENT_TMP_DIR
from rove.security.command_classifier import CommandClassifier

# 分级超时（Issue #9）：只读探索命令硬上限，防止 find / 之类全盘扫描
# 卡死 120s；其余命令默认 120s，可由 agent 显式指定（上限 600s）
SAFE_READ_TIMEOUT = 30
DEFAULT_BASH_TIMEOUT = 120
MAX_BASH_TIMEOUT = 600

_bash_classifier = CommandClassifier(WORKSPACE_ROOT)

def _safe_path(p: str) -> Path:
    path = (WORKSPACE_ROOT / p).resolve()
    if not path.is_relative_to(WORKSPACE_ROOT):
        raise ValueError(f"Path: {p} escapes workspace")
    return path

def _run_bash(command: str, timeout: int = None) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked."

    is_safe, _ = _bash_classifier.classify(command)
    if is_safe:
        effective = min(timeout or DEFAULT_BASH_TIMEOUT, SAFE_READ_TIMEOUT)
    else:
        effective = min(max(int(timeout), 1), MAX_BASH_TIMEOUT) if timeout else DEFAULT_BASH_TIMEOUT

    # 受控临时空间：子进程的 TMPDIR/TMP/TEMP 全部指向 .rove/tmp，
    # 避免 agent 脚本向宿主机 /tmp 写入脱离监管的文件
    AGENT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TMPDIR": str(AGENT_TMP_DIR),
        "TMP": str(AGENT_TMP_DIR),
        "TEMP": str(AGENT_TMP_DIR),
    }

    try:
        s = subprocess.run(
            command, shell=True,
            text=True, cwd=WORKSPACE_ROOT,
            capture_output=True, timeout=effective,
            env=env,
        )
        output = (s.stdout + s.stderr).strip()
        return output[:50000] if output else f"(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout expired({effective}s)."
    except Exception as e:
        return f"Error: {e}"

def _run_read(path: str, limit: int = None) -> str:
    try:
        fp = _safe_path(path)
        ftype = sniff_file_type(fp)

        if ftype == "pdf":
            return _extract_pdf_text(fp)
        if ftype == "rtf":
            # RTF 富文本（如 TextEdit 保存的 .txt 实为 RTF）：剥离控制字取正文
            text = _extract_rtf_text(fp.read_bytes())
        elif ftype == "utf16-text":
            text = fp.read_text(encoding="utf-16")
        elif ftype == "utf8-sig-text":
            text = fp.read_text(encoding="utf-8-sig")
        elif ftype in ("binary", "zip", "gzip", "png", "jpeg", "gif"):
            return f"Error: {path} appears to be binary content (sniffed as {ftype}); read_file only handles text. Use bash with a dedicated tool if you really need it."
        else:
            text = fp.read_text(encoding="utf-8", errors="replace")

        lines = text.splitlines()
        if limit and len(lines) > limit:
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def sniff_file_type(path: Path) -> str:
    """轻量文件类型嗅探：基于魔数字节而非扩展名判断（Issue #8），
    避免 TextEdit 保存的 RTF 伪装成 .txt 被当纯文本误读。"""
    try:
        with path.open("rb") as f:
            head = f.read(8192)
    except Exception:
        return "unknown"

    if head.startswith(b"{\\rtf1"):
        return "rtf"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"GIF8"):
        return "gif"
    if head.startswith((b"\xfe\xff", b"\xff\xfe")):
        return "utf16-text"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf8-sig-text"
    if b"\x00" in head:
        return "binary"
    return "text"


def _extract_rtf_text(data: bytes) -> str:
    """最小化 RTF 转纯文本：处理 \\uN 与 \\'hh 转义、\\par 段落，
    剥离其余控制字与花括号分组，保留文本内容。"""
    text = data.decode("ascii", errors="ignore")
    text = re.sub(r"\\u(-?\d+)\s?\??", lambda m: chr(int(m.group(1)) & 0xFFFF), text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", text)
    text = text.replace("{", "").replace("}", "")
    return text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")


def _extract_pdf_text(fp: Path) -> str:
    """PDF 轻量文本提取：检测到 pypdf/PyPDF2 时使用，未安装则返回
    明确指引，不引入硬依赖（Issue #8）。"""
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
    except ImportError:
        return (
            f"Info: {fp.name} is a PDF file, but no PDF text extraction library is installed.\n"
            "Options:\n"
            "1. pip install pypdf, then retry read_file;\n"
            "2. run `pdftotext` via bash if the binary is available."
        )

    try:
        reader = PdfReader(str(fp))
        pages = []
        for i, page in enumerate(reader.pages[:50]):
            pages.append(f"--- Page {i + 1} ---\n{(page.extract_text() or '').strip()}")
        return "\n".join(pages)[:50000]
    except Exception as e:
        return f"Error: PDF extraction failed: {e}"


def _run_read_notebook(path: str, max_output_chars: int = 2000) -> str:
    """结构化读取 Jupyter Notebook（Issue #8）：按 cell 提取代码与
    Markdown 源码，输出做摘要（stream/result 截断、富媒体报类型、
    error 带异常名），无需 agent 手写解析脚本。"""
    try:
        fp = _safe_path(path)
        nb = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as e:
        return f"Error: {e}"

    cells = nb.get("cells", [])
    kernel = nb.get("metadata", {}).get("kernelspec", {}).get("name", "unknown")
    lines = [f"Notebook: {path} ({len(cells)} cells, kernel={kernel})"]

    for i, cell in enumerate(cells):
        ctype = cell.get("cell_type", "unknown")
        lines.append(f"\n=== Cell {i + 1} [{ctype}] ===")
        lines.append("".join(cell.get("source", [])))

        if ctype != "code":
            continue

        outputs = cell.get("outputs", [])
        lines.append(f"--- outputs (execution_count={cell.get('execution_count')}, {len(outputs)} items) ---")
        for out in outputs:
            otype = out.get("output_type")
            if otype == "stream":
                text = "".join(out.get("text", []))
                suffix = "..." if len(text) > max_output_chars else ""
                lines.append(f"[stream:{out.get('name', '')}] {text[:max_output_chars]}{suffix}")
            elif otype in ("execute_result", "display_data"):
                data = out.get("data", {})
                if "text/plain" in data:
                    text = "".join(data["text/plain"])
                    suffix = "..." if len(text) > max_output_chars else ""
                    lines.append(f"[{otype}] {text[:max_output_chars]}{suffix}")
                else:
                    lines.append(f"[{otype}] rich media: {', '.join(sorted(data.keys()))}")
            elif otype == "error":
                lines.append(f"[error] {out.get('ename')}: {out.get('evalue')}")

    return "\n".join(lines)[:50000]


def _run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = _safe_path(path)
        c = fp.read_text()
        cnt = c.count(old_text)
        if cnt == 0:
            return f"Error: Text not found in file: {path}"
        if cnt > 1:
            return f"Error: Text appears {cnt} times in file: {path}, must be unique."
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def _run_write(path: str, content: str) -> str:
    try:
        fp = _safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

FILE_TOOLS = [
    Tool(
        name="bash",
        description="run a shell command in the workspace root. Read-only commands are capped at a 30s timeout; pass timeout (up to 600) for builds and long tasks.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Optional timeout in seconds (default 120, max 600)"},
            },
            "required": ["command"],
        },
        handler= lambda **kw: _run_bash(kw["command"], kw.get("timeout"))
    ),
    Tool(
        name="read_file",
        description="read file contents. Sniffs the real file type by magic bytes: handles RTF rich text, PDF (needs pypdf), UTF-16 text; refuses plain-binary dumps.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
        handler= lambda **kw: _run_read(kw["path"], kw.get("limit"))
    ),
    Tool(
        name="read_notebook",
        description="read a Jupyter Notebook (.ipynb) structurally: cell code/markdown sources plus summarized outputs (stream text, results, errors), no parsing scripts needed.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_output_chars": {"type": "integer", "description": "Per-output truncation limit (default 2000)"},
            },
            "required": ["path"],
        },
        handler= lambda **kw: _run_read_notebook(kw["path"], kw.get("max_output_chars", 2000))
    ),
    Tool(
        name="write_file",
        description="write contents to file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler= lambda **kw: _run_write(kw["path"], kw["content"])
    ),
    Tool(
        name="edit_file",
        description="Replace exact text in file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        handler= lambda **kw: _run_edit(kw["path"], kw["old_text"], kw["new_text"])
    )
]
