from pathlib import Path
import subprocess
from ..tool_registry import Tool
from rove.paths import WORKSPACE_ROOT

def _safe_path(p: str) -> Path:
    path = (WORKSPACE_ROOT / p).resolve()
    if not path.is_relative_to(WORKSPACE_ROOT):
        raise ValueError(f"Path: {p} escapes workspace")
    return path

def _run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked."
    try:
        s = subprocess.run(
            command, shell=True,
            text=True, cwd=WORKSPACE_ROOT,
            capture_output=True, timeout=120
        )
        output = (s.stdout + s.stderr).strip()
        return output[:50000] if output else f"(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout expired(120s)."
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
        description="run a shell command",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        handler= lambda **kw: _run_bash(kw["command"])
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
