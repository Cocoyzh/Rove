import os

from rove.tool_registry import ToolRegistry
from rove.skill_loader import SkillLoader
from rove.tools.todo import build_todo_tool, todo_tool
from rove.tools.execute_python import execute_python_tool
from rove.tools.background import run_bg_tool, check_bg_tool
from rove.tools.skill_tool import build_skill_tool
from rove.tools.memory_tools import build_memory_tools
from rove.tools.web_search import web_search_tool
from rove.task_manager import TaskManager
from rove.tools.team_tool import build_team_tools
from rove.tools.agent_teams import TeammateManager
from rove.tools.subagent import SubagentManager, build_subagent_tool
from rove.tools.file_tools import FILE_TOOLS
from rove.permissions import APPROVAL_MANAGER, PermissionPolicy
from rove.paths import WORKSPACE_ROOT

def build_default_registry(
    skill_loader: SkillLoader,
    task_manager: TaskManager,
    team_manager: TeammateManager
) -> ToolRegistry:
    registry = ToolRegistry(
        PermissionPolicy(WORKSPACE_ROOT),
        APPROVAL_MANAGER.request,
    )

    # teammate 是实验功能，ROVE_TEAMMATES=1 时才开放；默认并行方式是 dispatch_subagent
    team_tools = build_team_tools(team_manager, task_manager) if os.getenv("ROVE_TEAMMATES") == "1" else []

    registry.register_many([
        build_todo_tool(task_manager),
        execute_python_tool,
        *FILE_TOOLS,
        *team_tools,
        build_skill_tool(skill_loader),
        *build_memory_tools(),
        web_search_tool,
        run_bg_tool,
        check_bg_tool,
        # 一次性只读子代理，默认走 ASK 审批；配置了 LLM_FAST_MODEL_ID 时默认用廉价模型
        build_subagent_tool(SubagentManager(
            team_manager.llm,
            fast_model_id=os.getenv("LLM_FAST_MODEL_ID"),
        )),
    ])

    return registry
