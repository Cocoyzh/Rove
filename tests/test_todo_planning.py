import json
import pytest
from pathlib import Path

from rove.task_manager import TaskManager
from rove.tools.todo import TodoManager, build_todo_tool
from rove.messages import Message, ToolCall
from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import BaseLLMAdapter
from rove.tool_registry import ToolRegistry
from rove.permissions import PermissionPolicy, PermissionDecision
from rove.lead_agent import LeadAgent


def test_todo_auto_healing(tmp_path):
    tm = TaskManager(task_dir=tmp_path)
    todo = TodoManager(task_manager=tm)

    # 传入 3 个任务，其中 2 个都是 in_progress
    items = [
        {"id": "1", "text": "步骤 1", "status": "in_progress"},
        {"id": "2", "text": "步骤 2", "status": "in_progress"},
        {"id": "3", "text": "步骤 3", "status": "pending"},
    ]

    rendered = todo.update(items)

    # 验证自愈：步骤 1 被自动降级为 pending，步骤 2 保持 in_progress
    assert todo.items[0].status == "pending"
    assert todo.items[1].status == "in_progress"
    assert todo.items[2].status == "pending"

    # 验证返回文本中包含温和的提示
    assert "Notice: Only one todo can be in_progress simultaneously" in rendered
    assert "[>] #2: 步骤 2" in rendered
    assert "[ ] #1: 步骤 1" in rendered


def test_todo_sync_to_task_manager(tmp_path):
    tm = TaskManager(task_dir=tmp_path)
    todo = TodoManager(task_manager=tm)

    items = [
        {"id": "1", "text": "初始化项目", "status": "completed"},
        {"id": "2", "text": "编写功能代码", "status": "in_progress"},
    ]

    todo.update(items)

    # 验证文件看板 .tasks/ 下对应文件已生成
    task1_path = tmp_path / "task_1.json"
    task2_path = tmp_path / "task_2.json"

    assert task1_path.exists()
    assert task2_path.exists()

    task1 = json.loads(task1_path.read_text())
    task2 = json.loads(task2_path.read_text())

    assert task1["subject"] == "初始化项目"
    assert task1["status"] == "completed"
    assert task2["subject"] == "编写功能代码"
    assert task2["status"] == "in_progress"


def test_todo_preserves_owner_and_blocked_by(tmp_path):
    tm = TaskManager(task_dir=tmp_path)
    # 先通过任务系统创建一个由队友认领的任务
    created = json.loads(tm.create(subject="协同任务", description="后台计算"))
    task_id = created["id"]
    tm.claim_task(task_id, owner="teammate-worker")

    # 验证初始认领状态
    task = json.loads(tm.get(task_id))
    assert task["owner"] == "teammate-worker"
    assert task["status"] == "in_progress"

    # Lead Agent 通过 todo 工具全量更新列表，并未显式传递 owner
    todo = TodoManager(task_manager=tm)
    todo.update([
        {"id": str(task_id), "text": "协同任务 - 更新标题", "status": "in_progress"}
    ])

    # 校验 owner 没有被抹除丢失
    updated_task = json.loads(tm.get(task_id))
    assert updated_task["owner"] == "teammate-worker"
    assert updated_task["subject"] == "协同任务 - 更新标题"


class MockLLM(BaseLLMAdapter):
    def __init__(self, response_generator):
        super().__init__(model="mock", api_key="", base_url=None, timeout=None, context_window=100000)
        self.generator = response_generator

    def _create_client(self):
        return None

    def complete(self, request: LLMRequest) -> LLMResponse:
        return next(self.generator)

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        return self.complete(request)


def test_lead_agent_reminder_backoff_end_to_end(tmp_path):
    from rove.tool_registry import Tool
    policy = PermissionPolicy(tmp_path)
    registry = ToolRegistry(policy, lambda *a, **k: True)
    tm = TaskManager(task_dir=tmp_path / ".tasks")
    registry.register(build_todo_tool(tm))

    # 注册一个普通非 todo 工具
    registry.register(Tool(
        name="do_work",
        description="do something",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kw: "work done",
    ))

    # 构造响应序列：前 5 轮调用 do_work，第 6 轮结束
    responses = [
        LLMResponse(
            content=f"Round {i}",
            tool_calls=[ToolCall(tool_id=f"c_{i}", tool_name="do_work", tool_args={})],
            model="mock",
            stop_reason="tool_use",
        )
        for i in range(5)
    ]
    responses.append(LLMResponse(content="All done", tool_calls=[], model="mock", stop_reason="end_turn"))

    llm = MockLLM(iter(responses))
    lead = LeadAgent(llm, registry, max_steps=10)

    result = lead.run("Start working")
    assert result == "All done"

    # 检查所有注入的消息中的 reminder
    reminders = [m.content for m in lead.messages if m.role == "user" and "<reminder>" in (m.content or "")]

    # 第 1,2,3 轮未触发，第 4 轮触发一次并进入 6 轮冷却；第 5 轮在冷却期内未触发
    assert len(reminders) == 1
    assert "If your plan has evolved" in reminders[0]


def test_web_session_todo_syncs_to_api_tasks():
    from starlette.testclient import TestClient
    from rove.web.app import app
    from rove.paths import TASK_DIR

    client = TestClient(app)
    res = client.post("/api/sessions", json={"title": "测试看板同步"})
    assert res.status_code == 200
    sid = res.json()["id"]

    try:
        session_tm = TaskManager(TASK_DIR / sid)
        todo = TodoManager(task_manager=session_tm)
        todo.update([
            {"id": "1", "text": "解析数据", "status": "completed"},
            {"id": "2", "text": "训练模型", "status": "in_progress"},
        ])

        tasks_res = client.get(f"/api/tasks?session_id={sid}")
        assert tasks_res.status_code == 200
        tasks = tasks_res.json()
        assert len(tasks) == 2
        assert tasks[0]["subject"] == "解析数据"
        assert tasks[0]["status"] == "completed"
        assert tasks[1]["subject"] == "训练模型"
        assert tasks[1]["status"] == "in_progress"
    finally:
        client.delete(f"/api/sessions/{sid}")


