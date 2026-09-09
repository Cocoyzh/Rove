import unittest
from pathlib import Path

from rove.permissions import ApprovalMode, PermissionDecision, PermissionPolicy
from rove.security.command_classifier import CommandClassifier
from rove.tools.file_tools import _run_bash


class TestWorkspaceBoundary(unittest.TestCase):
    """Issue #9：bash 参数路径逃逸工作区一律硬拒绝，任何权限模式下不变"""

    def setUp(self):
        self.workspace = Path(__file__).parent.parent.resolve()
        self.classifier = CommandClassifier(self.workspace)
        # 用最宽松的 FULL_ACCESS 模式验证边界仍然生效
        self.policy = PermissionPolicy(self.workspace, ApprovalMode.FULL_ACCESS)

    def test_classifier_reports_escape_token(self):
        self.assertEqual(self.classifier.find_workspace_escape("ls -la .."), "..")
        self.assertEqual(self.classifier.find_workspace_escape("find / -name 'x'"), "/")
        self.assertEqual(self.classifier.find_workspace_escape("cd /tmp && python x.py"), "/tmp")
        self.assertEqual(self.classifier.find_workspace_escape("cat ~/.bashrc"), "~/.bashrc")

        self.assertIsNone(self.classifier.find_workspace_escape("ls -la"))
        self.assertIsNone(self.classifier.find_workspace_escape("cat src/rove/main.py"))
        self.assertIsNone(self.classifier.find_workspace_escape("cat /dev/null"))
        self.assertIsNone(self.classifier.find_workspace_escape("git log --oneline -5 2>/dev/null | head -20"))

    def test_policy_hard_denies_boundary_escape_even_in_full_access(self):
        for cmd in ["ls -la ..", "find / -name '*.py'", "cd /tmp && python x.py", "cat ~/.bashrc"]:
            decision, reason = self.policy.decide("bash", {"command": cmd})
            self.assertEqual(decision, PermissionDecision.DENY, cmd)
            self.assertIn("escapes workspace", reason)

    def test_policy_denies_boundary_in_background_tool(self):
        decision, _ = self.policy.decide("run_background", {"command": "ls .."})
        self.assertEqual(decision, PermissionDecision.DENY)


class TestTieredTimeoutAndTmpIsolation(unittest.TestCase):
    """Issue #9：分级超时与受控临时空间"""

    def test_readonly_command_runs(self):
        out = _run_bash("echo rove-sandbox-ok")
        self.assertIn("rove-sandbox-ok", out)

    def test_tmpdir_isolated_to_rove_tmp(self):
        out = _run_bash('python3 -c "import tempfile; print(tempfile.gettempdir())"')
        self.assertIn(".rove/tmp", out)

    def test_readonly_timeout_argument_accepted(self):
        # 只读命令的 timeout 即使传大值也会被压到 30s 上限；echo 快速验证链路可用
        out = _run_bash("echo capped-timeout", timeout=5000)
        self.assertIn("capped-timeout", out)


if __name__ == "__main__":
    unittest.main()
