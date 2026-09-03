import asyncio
import json
from typing import Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
from rich.console import Console

from rove.paths import WORKSPACE_ROOT
from rove.permissions import PermissionPolicy
from rove.tool_registry import ToolRegistry
from rove.tools.tools_setup import (
    todo_tool,
    execute_python_tool,
    run_bg_tool,
    check_bg_tool,
    build_skill_tool,
    build_create_task_tool,
    build_update_task_tool,
    build_get_task_tool,
    build_list_all_task_tool,
    build_team_tools,
    FILE_TOOLS,
)
from rove.web.web_approval import WebApprovalManager
from rove.web.agent_runner import WebLeadAgent
from rove.web.session_manager import message_to_dict

console = Console()


class SessionConnection:
    def __init__(self, websocket: WebSocket, session_id: str, app_state: Any) -> None:
        self.ws = websocket
        self.session_id = session_id
        self.app_state = app_state
        self.loop = asyncio.get_running_loop()

        # 为该会话构建专属的 WebApprovalManager
        self.approval_mgr = WebApprovalManager(on_approval_needed=self._on_approval_needed)

        # 构建专属于此 Web 会话的 ToolRegistry
        self.registry = ToolRegistry(
            PermissionPolicy(WORKSPACE_ROOT),
            self.approval_mgr.request,
        )
        self.registry.register_many([
            todo_tool,
            execute_python_tool,
            *FILE_TOOLS,
            *build_team_tools(self.app_state.team_manager),
            build_skill_tool(self.app_state.skill_loader),
            build_create_task_tool(self.app_state.task_manager),
            build_update_task_tool(self.app_state.task_manager),
            build_get_task_tool(self.app_state.task_manager),
            build_list_all_task_tool(self.app_state.task_manager),
            run_bg_tool,
            check_bg_tool,
        ])

        # 构建 WebLeadAgent 并挂载事件回调
        self.agent = WebLeadAgent(
            llm=self.app_state.llm,
            registry=self.registry,
            max_steps=50,
            on_text=self._on_token,
            on_tool_start=self._on_tool_start,
            on_tool_end=self._on_tool_end,
        )

        # 从 session_manager 恢复该会话的历史消息
        saved_messages = self.app_state.session_manager.load_session_messages(session_id)
        if saved_messages:
            self.agent.messages = saved_messages

    def _send_json_threadsafe(self, payload: Dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(self.ws.send_text(json.dumps(payload, ensure_ascii=False)), self.loop)

    def _on_token(self, token: str) -> None:
        self._send_json_threadsafe({
            "type": "token",
            "content": token,
        })

    def _on_tool_start(self, tool_id: str, tool_name: str, tool_args: Dict[str, Any]) -> None:
        self._send_json_threadsafe({
            "type": "tool_start",
            "tool_id": tool_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
        })

    def _on_tool_end(self, tool_id: str, tool_name: str, output: str, is_error: bool, cost_ms: int) -> None:
        self._send_json_threadsafe({
            "type": "tool_end",
            "tool_id": tool_id,
            "tool_name": tool_name,
            "output": output,
            "is_error": is_error,
            "cost_ms": cost_ms,
        })

    def _on_approval_needed(self, data: Dict[str, Any]) -> None:
        self._send_json_threadsafe({
            "type": "approval_required",
            "approval_id": data["approval_id"],
            "tool_name": data["tool_name"],
            "arguments": data["arguments"],
            "reason": data["reason"],
        })

    async def handle_chat(self, query: str) -> None:
        # 在独立线程中运行 agent.run，保证 WebSocket 异步接收事件不卡死
        def worker():
            try:
                self.agent.run(query)
            except Exception as e:
                console.print(f"[bold red]WebLeadAgent error: {e}[/bold red]")
                self._send_json_threadsafe({
                    "type": "error",
                    "error": str(e),
                })
            finally:
                # 保存会话消息
                self.app_state.session_manager.save_session_messages(
                    self.session_id, self.agent.messages
                )
                self._send_json_threadsafe({
                    "type": "done",
                    "session_id": self.session_id,
                })

        await asyncio.to_thread(worker)

    def handle_approval_response(self, approval_id: str, decision: str) -> None:
        self.approval_mgr.resolve(approval_id, decision)

    def handle_compact(self) -> None:
        def worker():
            self.agent.compact()
            self.app_state.session_manager.save_session_messages(
                self.session_id, self.agent.messages
            )
            self._send_json_threadsafe({
                "type": "compact_done",
                "session_id": self.session_id,
            })
        asyncio.create_task(asyncio.to_thread(worker))

    async def send_history(self) -> None:
        msgs = [message_to_dict(m) for m in self.agent.messages]
        await self.ws.send_text(json.dumps({
            "type": "history",
            "messages": msgs,
        }, ensure_ascii=False))


async def websocket_endpoint(websocket: WebSocket, session_id: str, app_state: Any) -> None:
    await websocket.accept()
    conn = SessionConnection(websocket, session_id, app_state)

    # 连接建立后立即下发历史消息
    await conn.send_history()

    try:
        while True:
            text = await websocket.receive_text()
            data = json.loads(text)
            msg_type = data.get("type")

            if msg_type == "chat":
                query = data.get("query", "")
                if query.strip():
                    await conn.handle_chat(query)
            elif msg_type == "approval_response":
                approval_id = data.get("approval_id")
                decision = data.get("decision", "N")
                if approval_id:
                    conn.handle_approval_response(approval_id, decision)
            elif msg_type == "compact":
                conn.handle_compact()
            elif msg_type == "get_history":
                await conn.send_history()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        console.print(f"[yellow]WebSocket disconnected: {e}[/yellow]")
