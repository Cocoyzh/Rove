import subprocess
import tempfile
from pathlib import Path
from ..tool_registry import Tool
from rove.paths import WORKSPACE_ROOT


def execute_python(code: str, timeout: int = 30, max_output_chars: int = 8000) -> str:
    """Execute Python code in a subprocess and return stdout/stderr."""
    if not code.strip():
        raise ValueError("code is required")

    with tempfile.TemporaryDirectory(prefix="rove_exec_") as temp_dir:
        script_path = Path(temp_dir) / "agent_code.py"
        script_path.write_text(code, encoding="utf-8")

        try:
            result = subprocess.run(
                ["python", str(script_path)],
                cwd=WORKSPACE_ROOT,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: Python execution timeout after {timeout} seconds"

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        output = (
            f"Return code: {result.returncode}\n\n"
            f"STDOUT:\n{stdout or '(empty)'}\n\n"
            f"STDERR:\n{stderr or '(empty)'}"
        )

        if len(output) > max_output_chars:
            output = output[:max_output_chars] + "\n\n... [output truncated]"

        return output


execute_python_tool = Tool(
    name="execute_python",
    description="Execute Python code for data analysis. Use pandas, numpy, or sklearn to inspect data and compute statistics.",
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds.",
            },
        },
        "required": ["code"],
    },
    handler=lambda **kw: execute_python(
        code=kw["code"],
        timeout=kw.get("timeout", 30),
    ),
)
