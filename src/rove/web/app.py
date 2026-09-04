import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from rove.paths import SKILL_DIR, TASK_DIR, TEAM_DIR, SESSIONS_DIR, PROJECT_ROOT
from rove.task_manager import TaskManager
from rove.skill_loader import SkillLoader
from rove.tools.agent_teams import TeammateManger
from rove.llm_adapters import AnthropicLLMAdapter
from rove.web.session_manager import SessionManager
from rove.web.websocket_handler import websocket_endpoint
from fastapi import WebSocket
from fastapi.staticfiles import StaticFiles

load_dotenv(PROJECT_ROOT / ".env")


class CreateSessionRequest(BaseModel):
    title: Optional[str] = "新会话"


class UpdateSessionRequest(BaseModel):
    title: str


def create_app() -> FastAPI:
    app = FastAPI(title="Rove Web API", version="0.1.0")

    # 允许本地跨域，以便 Vite 开发服务器无缝交互
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 初始化全局基础设施
    task_manager = TaskManager(TASK_DIR)
    skill_loader = SkillLoader(SKILL_DIR)
    session_manager = SessionManager(SESSIONS_DIR)

    ctx_override = os.getenv("LLM_CONTEXT_WINDOW")
    llm = AnthropicLLMAdapter(
        model=os.getenv("LLM_MODEL_ID", "claude-sonnet-5"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL"),
        timeout=int(os.getenv("LLM_TIMEOUT", "60")),
        context_window=int(ctx_override) if ctx_override else None,
    )
    team_manager = TeammateManger(TEAM_DIR, task_manager, llm)

    # 附加到 app.state 便于后续 WebSocket 和服务共享
    app.state.task_manager = task_manager
    app.state.skill_loader = skill_loader
    app.state.session_manager = session_manager
    app.state.llm = llm
    app.state.team_manager = team_manager

    # ==================== 会话管理接口 ====================

    @app.get("/api/sessions")
    def list_sessions():
        return session_manager.list_sessions()

    @app.post("/api/sessions")
    def create_session(req: CreateSessionRequest):
        return session_manager.create_session(title=req.title or "新会话")

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.patch("/api/sessions/{session_id}")
    def update_session(session_id: str, req: UpdateSessionRequest):
        session = session_manager.update_session_title(session_id, req.title)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str):
        ok = session_manager.delete_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        # 同步清理该会话绑定的 Task 看板目录和 Team 协同目录
        import shutil
        s_task_dir = TASK_DIR / session_id
        if s_task_dir.exists():
            shutil.rmtree(s_task_dir, ignore_errors=True)
        s_team_dir = TEAM_DIR / session_id
        if s_team_dir.exists():
            shutil.rmtree(s_team_dir, ignore_errors=True)
        return {"status": "ok"}

    # ==================== 协同与任务看板接口 ====================

    @app.get("/api/tasks")
    def get_tasks(session_id: Optional[str] = None):
        if session_id:
            return TaskManager(TASK_DIR / session_id).get_all_tasks()
        return task_manager.get_all_tasks()

    @app.get("/api/team")
    def get_team(session_id: Optional[str] = None):
        if session_id:
            s_team_dir = TEAM_DIR / session_id
            if (s_team_dir / "config.json").exists():
                tm = TeammateManger(s_team_dir, TaskManager(TASK_DIR / session_id), llm)
                config = tm.config
                return {
                    "team_name": config.get("team_name", "default"),
                    "members": config.get("members", []),
                }
            return {"team_name": "default", "members": []}
        config = team_manager.config
        return {
            "team_name": config.get("team_name", "default"),
            "members": config.get("members", []),
        }

    # ==================== 系统状态与指标 ====================

    @app.get("/api/system/status")
    def get_system_status(session_id: Optional[str] = None):
        session_ctx = 0
        session_tokens = 0
        if session_id:
            s = session_manager.get_session(session_id)
            if s:
                session_ctx = s.get("last_context_tokens", 0)
                session_tokens = s.get("total_input_tokens", 0) + s.get("total_output_tokens", 0)

        pct = round(session_ctx / llm.context_window * 100, 2) if (llm.context_window and session_ctx) else 0.0
        
        # 项目总计开销持久化统计：汇总所有持久化会话消耗，并结合内存实时指标
        disk_total = session_manager.get_project_total_tokens()
        in_memory_total = llm.total_input_tokens + llm.total_output_tokens
        project_total = max(disk_total, in_memory_total, session_tokens)

        return {
            "model": llm.model,
            "context_window": llm.context_window,
            "session_id": session_id,
            "session_context_tokens": session_ctx,
            "context_used_pct": pct,
            "session_tokens": session_tokens,
            "project_total_tokens": project_total,
            "total_input_tokens": llm.total_input_tokens,
            "total_output_tokens": llm.total_output_tokens,
            "call_count": llm.call_count,
        }

    # ==================== WebSocket 双向实时流 ====================

    @app.websocket("/ws/{session_id}")
    async def ws_route(websocket: WebSocket, session_id: str):
        await websocket_endpoint(websocket, session_id, app.state)

    # ==================== 前端静态页面挂载 ====================

    dist_dir = PROJECT_ROOT / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")

    return app


app = create_app()
