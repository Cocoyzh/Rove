import os
from pathlib import Path

# Rove 软件自身安装根目录（用于定位静态资源与全局公共技能库）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 动态目标工作区：用户在哪个终端目录启动，就管理哪个目录
_env_workspace = os.getenv("ROVE_WORKSPACE")
WORKSPACE_ROOT = Path(_env_workspace).resolve() if _env_workspace else Path.cwd().resolve()

# 公共技能库（复用全局）
SKILL_DIR = PROJECT_ROOT / "skills"

# 项目专属数据目录（跟随当前工作区，实现每个项目独立自包含）
TASK_DIR = WORKSPACE_ROOT / ".tasks"
TEAM_DIR = WORKSPACE_ROOT / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
ROVE_DIR = WORKSPACE_ROOT / ".rove"
TOOL_RESULTS_DIR = ROVE_DIR / "tool-results"
TRANSCRIPT_DIR = ROVE_DIR / "transcripts"
SESSIONS_DIR = ROVE_DIR / "sessions"
