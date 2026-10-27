"""SWE-Bench 适配层：实例加载、提示词渲染、patch 提取、predictions 聚合。"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SwebenchInstance:
    """一条 SWE-Bench 任务实例（字段与 HF 数据集对齐）"""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    patch: str = ""          # gold patch，评测方使用，Rove 不读取
    test_patch: str = ""
    version: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "SwebenchInstance":
        return cls(
            instance_id=str(data.get("instance_id", "")),
            repo=str(data.get("repo", "")),
            base_commit=str(data.get("base_commit", "")),
            problem_statement=str(data.get("problem_statement", "")),
            hints_text=str(data.get("hints_text", "") or ""),
            patch=str(data.get("patch", "") or ""),
            test_patch=str(data.get("test_patch", "") or ""),
            version=str(data.get("version", "") or ""),
        )


def load_instances(path: Path) -> list[SwebenchInstance]:
    """从 JSONL 加载实例；跳过缺 instance_id 的坏行而非中断整个跑批。"""
    instances = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("instance_id"):
            instances.append(SwebenchInstance.from_dict(data))
    return instances


def build_task_prompt(instance: SwebenchInstance, max_chars: int = 12_000) -> str:
    """把 issue 渲染成任务指令，problem_statement 过长时截断。"""
    statement = instance.problem_statement.strip()
    if len(statement) > max_chars:
        statement = statement[:max_chars] + "\n...[issue 内容过长已截断]"

    parts = [
        f"你在仓库 {instance.repo}（commit {instance.base_commit[:12]}）的工作副本中。"
        f"请解决以下 GitHub issue。",
        "",
        "<issue>",
        statement,
        "</issue>",
    ]
    if instance.hints_text.strip():
        parts += ["", "<hints>", instance.hints_text.strip()[:4000], "</hints>"]
    parts += [
        "",
        "验收标准：仓库的现有测试（含 hidden tests）在你的修改后全部通过。",
        "约束：不要修改或删除任何测试文件；完成后用 git 提交你的改动（不推送）。",
    ]
    return "\n".join(parts)


def extract_patch(workspace: Path, base_commit: str = "") -> str:
    """git add -A 后对 base_commit 取 --cached diff；仓库无效或无改动返回空串。"""
    workspace = Path(workspace)
    if not (workspace / ".git").exists():
        return ""

    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=workspace,
            capture_output=True, text=True, timeout=60,
        )
        return result.stdout if result.returncode == 0 else ""

    _git("add", "-A")
    commit = base_commit if base_commit else "HEAD"
    return _git("diff", "--cached", commit).strip()


def write_predictions(rows: list[dict], out_path: Path, model_name: str = "rove") -> int:
    """聚合为官方 harness 的 predictions JSONL，返回写出条数。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            record = {
                "instance_id": row["instance_id"],
                "model_patch": row.get("model_patch", ""),
                "model_name_or_path": row.get("model_name_or_path", model_name),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def run_bench(instances: list[SwebenchInstance], workspaces: dict[str, Path],
              agent_fn, out_path: Path, model_name: str = "rove") -> list[dict]:
    """逐实例运行 agent_fn(prompt, cwd) 并产出 predictions（评测机入口）。"""
    rows = []
    for instance in instances:
        workspace = workspaces.get(instance.instance_id)
        patch = ""
        if workspace is not None:
            agent_fn(build_task_prompt(instance), workspace)
            patch = extract_patch(workspace, instance.base_commit)
        rows.append({"instance_id": instance.instance_id, "model_patch": patch,
                     "model_name_or_path": model_name})
    write_predictions(rows, out_path, model_name)
    return rows
