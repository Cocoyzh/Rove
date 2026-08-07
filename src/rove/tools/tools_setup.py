from rove.tool_registry import ToolRegistry
from rove.skill_loader import SkillLoader
from rove.tools.todo import todo_tool
from rove.tools.execute_python import execute_python_tool
from rove.tools.background import run_bg_tool, check_bg_tool
from rove.tools.skill_tool import build_skill_tool
from rove.tools.task_tool import (
    build_create_task_tool,
    build_update_task_tool,
    build_get_task_tool,
    build_list_all_task_tool,
    build_scan_tasks_tool,
    build_claim_task_tool,
)
from rove.task_manager import TaskManager
from rove.tools.team_tool import build_team_tools
from rove.tools.agent_teams import TeammateManger
from rove.tools.file_tools import FILE_TOOLS
from rove.permissions import APPROVAL_MANAGER, PermissionPolicy
from rove.paths import WORKSPACE_ROOT

def build_default_registry(
    skill_loader: SkillLoader,
    task_manager: TaskManager,
    team_manager: TeammateManger
) -> ToolRegistry:
    registry = ToolRegistry(
        PermissionPolicy(WORKSPACE_ROOT),
        APPROVAL_MANAGER.request,
    )

    registry.register_many([
        todo_tool,
        execute_python_tool,
        *FILE_TOOLS,
        *build_team_tools(team_manager),
        build_skill_tool(skill_loader),
        build_create_task_tool(task_manager),
        build_update_task_tool(task_manager),
        build_get_task_tool(task_manager),
        build_list_all_task_tool(task_manager),
        run_bg_tool,
        check_bg_tool,
    ])

    return registry
