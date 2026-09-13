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

    registry.register_many([
        build_todo_tool(task_manager),
        execute_python_tool,
        *FILE_TOOLS,
        *build_team_tools(team_manager, task_manager),
        build_skill_tool(skill_loader),
        *build_memory_tools(),
        web_search_tool,
        run_bg_tool,
        check_bg_tool,
        # 一次性子代理：权限策略复用 lead 实例（模式切换同步生效），
        # 不入 _ALLOWED_TOOLS / _ASK_TOOLS，默认走 ASK 兜底审批；
        # 配置 LLM_FAST_MODEL_ID 时探索类子代理默认路由到廉价模型（P3）
        build_subagent_tool(SubagentManager(
            team_manager.llm,
            permission_policy=registry.permission_policy,
            fast_model_id=os.getenv("LLM_FAST_MODEL_ID"),
        )),
    ])

    return registry
