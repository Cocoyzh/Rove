from pathlib import Path
import os
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
        lines = _safe_path(path).read_text().splitlines()
        if limit and len(lines) > limit:
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

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
        description="read file contents",
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
