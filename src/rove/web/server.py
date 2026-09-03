import sys
import argparse
import webbrowser
import threading
import time
import uvicorn
from rich.console import Console

from rove.paths import PROJECT_ROOT

console = Console()


def open_browser(url: str, delay: float = 1.0) -> None:
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 Rove Web 交互控制台")
    parser.add_argument("--host", default="127.0.0.1", help="服务监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="服务监听端口 (默认 8000)")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="启用热重载 (开发模式)")

    args = parser.parse_args()

    dist_dir = PROJECT_ROOT / "frontend" / "dist"
    if not dist_dir.exists():
        console.print("[yellow]⚠ 警告: frontend/dist 未构建，前端页面可能无法直接托管。[/yellow]")
        console.print("[dim]提示: 可在 frontend 目录下运行 `npm run build` 生成构建产物。[/dim]")

    url = f"http://{args.host}:{args.port}"
    console.rule("[bold cyan]Rove Web Console[/bold cyan]", style="cyan")
    console.print(f"[cyan]🚀 服务地址:[/cyan] [bold underline]{url}[/bold underline]")
    console.print(f"[dim]按 Ctrl+C 退出服务[/dim]\n")

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    uvicorn.run(
        "rove.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
