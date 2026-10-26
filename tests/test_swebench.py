"""SWE-Bench 适配层测试：全部离线，临时 git 仓库构造（不触碰用户数据）"""
import json
import subprocess
import unittest
import tempfile
from pathlib import Path

from rove.benchmarks.swebench import (
    SwebenchInstance,
    build_task_prompt,
    extract_patch,
    load_instances,
    write_predictions,
)


def _init_repo(workdir: Path) -> Path:
    """在 tmp 下构造一个带基线 commit 的最小 git 仓库"""
    repo = workdir / "repo"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=repo, check=True, capture_output=True)
    git("init", "-q")
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    return repo


class TestInstanceLoading(unittest.TestCase):
    def test_load_jsonl_and_skip_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instances.jsonl"
            good = {"instance_id": "django__django-16379", "repo": "django/django",
                    "base_commit": "abc123", "problem_statement": "Bug in ORM",
                    "hints_text": "look at query.py", "patch": "gold", "test_patch": "tp",
                    "version": "4.1"}
            lines = [json.dumps(good), "not json", json.dumps({"repo": "no-id"}),
                     "", json.dumps(good | {"instance_id": "sympy__sympy-1"})]
            path.write_text("\n".join(lines), encoding="utf-8")

            instances = load_instances(path)
            self.assertEqual([i.instance_id for i in instances],
                             ["django__django-16379", "sympy__sympy-1"])
            self.assertEqual(instances[0].base_commit, "abc123")
            self.assertEqual(instances[0].hints_text, "look at query.py")


class TestTaskPrompt(unittest.TestCase):
    def test_prompt_contains_issue_and_constraints(self):
        instance = SwebenchInstance(
            instance_id="x__y-1", repo="x/y", base_commit="abc123def456",
            problem_statement="Crash when calling foo(None)",
            hints_text="Traceback points to utils.py",
        )
        prompt = build_task_prompt(instance)
        self.assertIn("Crash when calling foo(None)", prompt)
        self.assertIn("<hints>", prompt)
        self.assertIn("abc123def456", prompt[:200])
        self.assertIn("不要修改或删除任何测试文件", prompt)

    def test_oversized_statement_truncated(self):
        instance = SwebenchInstance(
            instance_id="x__y-1", repo="x/y", base_commit="a",
            problem_statement="P" * 50_000,
        )
        prompt = build_task_prompt(instance, max_chars=1000)
        self.assertIn("已截断", prompt)
        self.assertLess(len(prompt), 2000)


class TestPatchExtraction(unittest.TestCase):
    def test_extract_patch_from_modified_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            # 模拟 Agent 的修改：改文件 + 新增文件
            (repo / "app.py").write_text("def main():\n    return 42\n", encoding="utf-8")
            (repo / "new_mod.py").write_text("VALUE = 7\n", encoding="utf-8")

            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                  capture_output=True, text=True).stdout.strip()
            patch = extract_patch(repo, base)

            self.assertIn("diff --git", patch)
            self.assertIn("return 42", patch)
            self.assertIn("new_mod.py", patch)

    def test_no_changes_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(extract_patch(repo, base), "")

    def test_non_git_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(extract_patch(Path(tmp)), "")


class TestPredictionsWriter(unittest.TestCase):
    def test_writes_official_jsonl_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "predictions.jsonl"
            n = write_predictions(
                [{"instance_id": "a__b-1", "model_patch": "diff --git ..."},
                 {"instance_id": "a__b-2"}],
                out, model_name="rove-verified-50",
            )
            self.assertEqual(n, 2)
            lines = out.read_text(encoding="utf-8").strip().splitlines()
            rec1 = json.loads(lines[0])
            self.assertEqual(set(rec1.keys()),
                             {"instance_id", "model_patch", "model_name_or_path"})
            self.assertEqual(rec1["model_name_or_path"], "rove-verified-50")
            rec2 = json.loads(lines[1])
            self.assertEqual(rec2["model_patch"], "")  # 失败实例也要占位，便于全量对账


if __name__ == "__main__":
    unittest.main()
