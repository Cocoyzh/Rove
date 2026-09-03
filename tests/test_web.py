import json
import pytest
from starlette.testclient import TestClient

from rove.messages import Message, ToolCall
from rove.web.app import app
from rove.web.session_manager import SessionManager
from rove.web.web_approval import WebApprovalManager


def test_session_manager_crud(tmp_path):
    sm = SessionManager(sessions_dir=tmp_path)

    # 1. 列表初始为空
    assert sm.list_sessions() == []

    # 2. 创建会话
    s1 = sm.create_session(title="测试分析代码")
    sid = s1["id"]
    assert sid.startswith("s_")
    assert s1["title"] == "测试分析代码"

    # 3. 再次获取
    loaded = sm.get_session(sid)
    assert loaded is not None
    assert loaded["id"] == sid

    # 4. 保存消息 (含 ToolCall)
    msgs = [
        Message(role="user", content="请读取文件"),
        Message(
            role="assistant",
            content="我来读取文件",
            tool_calls=[ToolCall(tool_id="call_1", tool_name="read_file", tool_args={"path": "a.txt"})],
        ),
        Message(role="tool", tool_call_id="call_1", content="file content here"),
    ]
    sm.save_session_messages(sid, msgs, auto_title=False)

    # 5. 加载消息并校验结构
    restored = sm.load_session_messages(sid)
    assert len(restored) == 3
    assert restored[0].role == "user"
    assert restored[0].content == "请读取文件"
    assert restored[1].role == "assistant"
    assert restored[1].tool_calls[0].tool_name == "read_file"
    assert restored[2].role == "tool"
    assert restored[2].tool_call_id == "call_1"

    # 6. 更新标题
    updated = sm.update_session_title(sid, "更新后的标题")
    assert updated["title"] == "更新后的标题"

    # 7. 删除会话
    assert sm.delete_session(sid) is True
    assert sm.get_session(sid) is None
    assert sm.list_sessions() == []


def test_web_approval_manager():
    approval_events = []

    def on_need_approval(data):
        approval_events.append(data)

    mgr = WebApprovalManager(on_approval_needed=on_need_approval)

    # 在后台异步响应
    import threading
    def auto_approve():
        import time
        time.sleep(0.05)
        if approval_events:
            mgr.resolve(approval_events[-1]["approval_id"], "y")

    t = threading.Thread(target=auto_approve)
    t.start()

    # 触发单次批准
    ok = mgr.request("write_file", {"path": "test.txt"}, "写入文件")
    t.join()
    assert ok is True
    assert len(approval_events) == 1

    # 测试会话级免问 [s]
    def auto_session_approve():
        import time
        time.sleep(0.05)
        if approval_events:
            mgr.resolve(approval_events[-1]["approval_id"], "s")

    t2 = threading.Thread(target=auto_session_approve)
    t2.start()
    ok2 = mgr.request("bash", {"cmd": "ls"}, "执行命令")
    t2.join()
    assert ok2 is True

    # 再次请求相同工具与参数，无需等待应直接通过
    ok3 = mgr.request("bash", {"cmd": "ls"}, "执行命令")
    assert ok3 is True


def test_rest_api_endpoints():
    client = TestClient(app)

    # 1. 静态主页返回
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "html" in res_root.headers.get("content-type", "")

    # 2. 会话 CRUD
    res_create = client.post("/api/sessions", json={"title": "自动化测试会话"})
    assert res_create.status_code == 200
    ses_data = res_create.json()
    sid = ses_data["id"]

    res_list = client.get("/api/sessions")
    assert res_list.status_code == 200
    assert any(s["id"] == sid for s in res_list.json())

    res_get = client.get(f"/api/sessions/{sid}")
    assert res_get.status_code == 200
    assert res_get.json()["title"] == "自动化测试会话"

    res_patch = client.patch(f"/api/sessions/{sid}", json={"title": "改名测试"})
    assert res_patch.status_code == 200
    assert res_patch.json()["title"] == "改名测试"

    # 3. 协同与任务看板
    res_tasks = client.get("/api/tasks")
    assert res_tasks.status_code == 200
    assert isinstance(res_tasks.json(), list)

    res_team = client.get("/api/team")
    assert res_team.status_code == 200
    assert "members" in res_team.json()

    # 4. 系统指标
    res_status = client.get("/api/system/status")
    assert res_status.status_code == 200
    assert "model" in res_status.json()
    assert "context_window" in res_status.json()

    # 5. 清理会话
    res_del = client.delete(f"/api/sessions/{sid}")
    assert res_del.status_code == 200


def test_websocket_history_handshake():
    client = TestClient(app)
    # 创建一个测试会话
    res = client.post("/api/sessions", json={"title": "WS 测试会话"})
    sid = res.json()["id"]

    # 建立 WebSocket 连接
    with client.websocket_connect(f"/ws/{sid}") as ws:
        # 连接成功后服务端应主动推送历史记录
        data = ws.receive_json()
        assert data["type"] == "history"
        assert isinstance(data["messages"], list)

    # 清理会话
    client.delete(f"/api/sessions/{sid}")
