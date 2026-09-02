"""任务板冒烟测试：系统能启动、核心动作（创建/认领）能走通。"""
import json

from rove.task_manager import TaskManager


def test_create_and_claim_task(tmp_path):
    tm = TaskManager(task_dir=tmp_path)

    task = json.loads(tm.create(subject="冒烟测试任务"))
    assert task["status"] == "pending"
    assert task["owner"] == ""

    result = tm.claim_task(task["id"], owner="ci")
    assert result == f"Claimed Task #{task['id']} for ci."

    claimed = json.loads(tm.get(task["id"]))
    assert claimed["status"] == "in_progress"
    assert claimed["owner"] == "ci"


def test_double_claim_rejected(tmp_path):
    tm = TaskManager(task_dir=tmp_path)
    task_id = json.loads(tm.create(subject="只许认领一次"))["id"]

    assert "Claimed" in tm.claim_task(task_id, owner="alice")

    result = tm.claim_task(task_id, owner="bob")
    assert result.startswith("Error:")
    # 任务仍归第一次认领者所有
    assert json.loads(tm.get(task_id))["owner"] == "alice"
