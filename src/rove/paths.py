"""Rove 使用的项目与运行时目录。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# print(PROJECT_ROOT)
WORKSPACE_ROOT = PROJECT_ROOT

SKILL_DIR = PROJECT_ROOT / "skills"
TASK_DIR = PROJECT_ROOT / ".tasks"

TEAM_DIR = PROJECT_ROOT / ".team"
INBOX_DIR = TEAM_DIR / "inbox"

ROVE_DIR = PROJECT_ROOT / ".rove"
TOOL_RESULTS_DIR = ROVE_DIR / "tool-results"
TRANSCRIPT_DIR = ROVE_DIR / "transcripts"
