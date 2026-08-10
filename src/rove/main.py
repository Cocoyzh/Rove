from rove.lead_agent import LeadAgent
from rove.tools.tools_setup import build_default_registry
from rove.skill_loader import SkillLoader
from rove.task_manager import TaskManager
from rove.paths import SKILL_DIR, TASK_DIR, TEAM_DIR
from rove.permissions import APPROVAL_MANAGER
from rove.tools.agent_teams import TeammateManger
from rich.console import Console
from rich.markdown import Markdown
from rove.llm_adapters import AnthropicLLMAdapter
import readline
import os
from dotenv import load_dotenv

load_dotenv()

readline.parse_and_bind("set bind-tty-special-chars off")
readline.parse_and_bind("set input-meta on")
readline.parse_and_bind("set output-meta on")
readline.parse_and_bind("set convert-meta off")

console = Console()

PROMPT = "\001\033[1;36m\002Rove >>\001\033[0m\002 "

def main() -> None:
    skill_loader = SkillLoader(SKILL_DIR)
    task_manager = TaskManager(TASK_DIR)

    llm = AnthropicLLMAdapter(model=os.getenv("LLM_MODEL_ID"),
                              api_key=os.getenv("LLM_API_KEY"),
                              base_url=os.getenv("LLM_BASE_URL"),
                              timeout=int(os.getenv("LLM_TIMEOUT", "60")))

    team_manager = TeammateManger(TEAM_DIR, task_manager, llm)

    registry = build_default_registry(
        skill_loader=skill_loader,
        task_manager=task_manager,
        team_manager=team_manager,
    )

    lead = LeadAgent(llm, registry, 50)
    while True:
        query = APPROVAL_MANAGER.read_input(PROMPT)
        if query.strip().lower() in ("q", "exit", ""):
            break
        if query.strip() == "/team":
            print(team_manager.list_all())
            continue
        if query.strip() == "/new":
            lead.reset()
            console.print("[dim]✦ 新会话已开始，历史已清空[/dim]")
            continue
        answer = lead.run(query)

        console.rule("[bold cyan]Answer[/bold cyan]", style="cyan")
        if answer and answer.strip():
            console.print(Markdown(answer))
        else:
            console.print("[dim](empty response)[/dim]")

if __name__ == "__main__":
    main()
