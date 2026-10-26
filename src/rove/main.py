from rove.lead_agent import LeadAgent
from rove.tools.tools_setup import build_default_registry
from rove.skill_loader import SkillLoader
from rove.task_manager import TaskManager
from rove.paths import SKILL_DIR, TASK_DIR, TEAM_DIR
from rove.permissions import APPROVAL_MANAGER, VALID_APPROVAL_MODES
from rove.tools.agent_teams import TeammateManager
from rove.call_trace import CallTracer, new_session_id
from rove.side_prompts import SidePromptService
from rich.console import Console
from rove.llm_adapters import AnthropicLLMAdapter, BaseLLMAdapter
from rove.usage_ledger import LEDGER
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

def show_context(lead: LeadAgent, llm: BaseLLMAdapter) -> None:
    console.rule("[bold cyan]Context[/bold cyan]", style="cyan")
    console.print(f"Context window  {llm.context_window:,} tokens")
    used = llm.last_input_tokens
    pct = used / llm.context_window * 100 if llm.context_window else 0
    console.print(f"上次调用上下文  {used:,} tokens ({pct:.1f}%)")
    console.print(f"消息数          {len(lead.messages)}")


def show_status(lead: LeadAgent, llm: BaseLLMAdapter) -> None:
    console.rule("[bold cyan]Status[/bold cyan]", style="cyan")
    console.print(f"[cyan]Model[/cyan]           {llm.model}")
    console.print(f"[cyan]Context window[/cyan]  {llm.context_window:,} tokens")
    console.print("[cyan]Session[/cyan]")
    console.print(f"  消息数          {len(lead.messages)}")
    console.print(f"  消耗 In/Out     {lead.conv_input_tokens:,} / {lead.conv_output_tokens:,}")
    console.print("[cyan]Global[/cyan]")
    console.print(f"  调用次数        {llm.call_count}")
    console.print(f"  消耗 In/Out     {llm.total_input_tokens:,} / {llm.total_output_tokens:,}")


def show_stats(lead: LeadAgent, title: str = "Stats") -> None:
    """缓存命中率与成本小结（context-optimize P0），/stats 与退出小结共用。"""
    s = LEDGER.summary()
    console.rule(f"[bold cyan]{title}[/bold cyan]", style="cyan")
    console.print(f"[cyan]调用次数[/cyan]          {s['calls']}")
    console.print(f"[cyan]输入 In[/cyan]            {s['context_input_tokens']:,}  "
                  f"[dim](未命中 {s['input_tokens']:,} / 缓存读 {s['cache_read_tokens']:,} / 缓存写 {s['cache_creation_tokens']:,})[/dim]")
    console.print(f"[cyan]输出 Out[/cyan]           {s['output_tokens']:,}")
    console.print(f"[cyan]前缀缓存命中率[/cyan]     {s['hit_rate'] * 100:.1f}%")
    console.print(f"[cyan]估算成本[/cyan]           ${s['estimated_cost_usd']:.4f} [dim](内置定价表估算)[/dim]")
    console.print(f"[cyan]本会话 In/Out[/cyan]      {lead.conv_input_tokens:,} / {lead.conv_output_tokens:,}")


import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        from rove.web.server import main as web_main
        sys.argv.pop(1)
        web_main()
        return

    skill_loader = SkillLoader(SKILL_DIR)
    task_manager = TaskManager(TASK_DIR)

    ctx_override = os.getenv("LLM_CONTEXT_WINDOW")
    llm = AnthropicLLMAdapter(
        model=os.getenv("LLM_MODEL_ID"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        timeout=int(os.getenv("LLM_TIMEOUT", "60")),
        context_window=int(ctx_override) if ctx_override else None,
    )

    team_manager = TeammateManager(TEAM_DIR, task_manager, llm,
                                   tracer=CallTracer(new_session_id()))

    registry = build_default_registry(
        skill_loader=skill_loader,
        task_manager=task_manager,
        team_manager=team_manager,
    )

    lead = LeadAgent(llm, registry, 50, tracer=team_manager.tracer.for_agent("lead"))
    side_prompts = SidePromptService(llm)
    titled = False
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
        if query.strip() == "/compact":
            lead.compact()
            continue
        if query.strip() == "/status":
            show_status(lead, llm)
            continue
        if query.strip() == "/stats":
            show_stats(lead)
            continue
        if query.strip() == "/context":
            show_context(lead, llm)
            continue
        if query.strip() == "/mode" or query.strip().startswith("/mode "):
            policy = registry.permission_policy
            parts = query.strip().split(maxsplit=1)
            if len(parts) == 1:
                console.print(f"[cyan]当前权限模式[/cyan] {policy.approval_mode.value} "
                              f"[dim](可选: strict / workspace / full_access)[/dim]")
                continue
            mode_value = parts[1].strip().lower()
            mode = VALID_APPROVAL_MODES.get(mode_value)
            if mode is None:
                console.print(f"[red]未知模式 {mode_value!r}[/red] [dim](可选: strict / workspace / full_access)[/dim]")
                continue
            policy.set_approval_mode(mode)
            console.print(f"[cyan]✦ 权限模式已切换[/cyan] {mode.value}")
            continue
        answer = lead.run(query)
        if not (answer and answer.strip()):
            console.print("(empty response)")
        # 旁路命名（REPL 与 Web 共用 TitleService）：首轮回复结束后生成一次会话标题
        if not titled:
            titled = True
            title = side_prompts.generate_title(query)
            if title:
                console.print(f"[dim]✦ 会话标题: {title}[/dim]")

    # 会话结束成本小结（context-optimize P0）
    show_stats(lead, title="会话结束小结")

if __name__ == "__main__":
    main()
