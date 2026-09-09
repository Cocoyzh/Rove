import unittest
from pathlib import Path
from unittest.mock import patch

from rove.permissions import (
    ApprovalManager,
    PermissionDecision,
    PermissionPolicy,
)
from rove.security.approval_store import (
    CommandPrefixRule,
    ExactMatchRule,
    PathRule,
    SessionApprovalStore,
    ToolWideRule,
)
from rove.security.command_classifier import (
    CommandClassifier,
    extract_command_prefix,
)
from rove.web.web_approval import WebApprovalManager


class TestCommandClassifier(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(__file__).parent.parent.resolve()
        self.classifier = CommandClassifier(self.workspace)

    def test_safe_read_commands(self):
        safe_cases = [
            "ls",
            "ls -la",
            "cat README.md",
            "head -n 10 src/rove/permissions.py",
            "tail -f log.txt",
            "wc -l pyproject.toml",
            "grep -rn 'def decide' src/",
            "pwd",
            "which python",
            "file src/rove/main.py",
            "stat pyproject.toml",
            "find . -name '*.py'",
            "echo 'Hello world'",
            "diff file1 file2",
            "cd src && ls",
            "sed -n '1,50p' pyproject.toml",
        ]
        for cmd in safe_cases:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertTrue(is_safe, f"Expected safe for {cmd!r}, got reason: {reason}")

    def test_safe_git_commands(self):
        safe_git = [
            "git status",
            "git status -s",
            "git diff",
            "git diff HEAD~1",
            "git log -n 5",
            "git show HEAD",
            "git branch -a",
            "git tag -l",
            "git rev-parse HEAD",
            "git remote -v",
            "git describe --tags",
            "git ls-files",
            "git stash list",
            "git stash show",
            "git status 2>/dev/null",
            "git log --oneline -5 2>/dev/null | head -20",
        ]
        for cmd in safe_git:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertTrue(is_safe, f"Expected safe for git: {cmd!r}, got: {reason}")

    def test_safe_pipelines_and_chains(self):
        safe_chains = [
            "cat README.md | grep -i 'rove'",
            "git log -n 20 | head -n 5",
            "ls -la | wc -l",
            "pwd && ls -l",
            "git status || git log -n 1",
        ]
        for cmd in safe_chains:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertTrue(is_safe, f"Expected safe chain for {cmd!r}, got: {reason}")

    def test_blocks_redirection_and_tee(self):
        unsafe_redirections = [
            "cat file > out.txt",
            "echo 'text' >> append.txt",
            "cat file >| force.txt",
            "cat file &> all.txt",
            "ls | tee out.txt",
            "cat < input.txt",
        ]
        for cmd in unsafe_redirections:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertFalse(is_safe, f"Expected unsafe for redirection {cmd!r}")

    def test_blocks_command_substitution(self):
        unsafe_substitutions = [
            "cat $(which python)",
            "cat `which python`",
            "echo ${PATH}",
        ]
        for cmd in unsafe_substitutions:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertFalse(is_safe, f"Expected unsafe for substitution {cmd!r}")

    def test_blocks_sensitive_files(self):
        sensitive_cases = [
            "cat .env",
            "cat .env.local",
            "grep 'API_KEY' .env.prod",
            "cat ~/.ssh/id_rsa",
            "cat cert.pem",
            "head server.key",
            "cat credentials.json",
        ]
        for cmd in sensitive_cases:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertFalse(is_safe, f"Expected sensitive block for {cmd!r}")

    def test_blocks_dangerous_find_flags(self):
        dangerous_find = [
            "find . -name '*.py' -delete",
            "find . -name '*.tmp' -exec rm -f {} \\;",
            "find . -ok rm {} \\;",
        ]
        for cmd in dangerous_find:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertFalse(is_safe, f"Expected unsafe for find flag in {cmd!r}")

    def test_blocks_dangerous_git_commands(self):
        dangerous_git = [
            "git checkout -b new-branch",
            "git commit -m 'commit'",
            "git push origin main",
            "git reset --hard HEAD~1",
            "git clean -fd",
            "git branch -D feature",
            "git tag -d v1.0",
            "git stash drop",
            "git stash pop",
            "git remote add origin https://evil.com",
        ]
        for cmd in dangerous_git:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertFalse(is_safe, f"Expected unsafe for git operation in {cmd!r}")

    def test_workspace_escaping(self):
        escape_cases = [
            "cat ../outside_workspace.txt",
            "ls /etc/hosts",
            "cat ~/.bashrc",
        ]
        for cmd in escape_cases:
            is_safe, reason = self.classifier.classify(cmd)
            self.assertFalse(is_safe, f"Expected escape block for {cmd!r}")

        # Virtual device files are allowed
        is_safe, reason = self.classifier.classify("cat /dev/null")
        self.assertTrue(is_safe)


class TestApprovalStore(unittest.TestCase):
    def test_extract_command_prefix(self):
        self.assertEqual(extract_command_prefix("pytest tests/test_a.py -v"), "pytest")
        self.assertEqual(extract_command_prefix("git commit -m 'initial'"), "git commit")
        self.assertEqual(extract_command_prefix("git status -s"), "git status")
        self.assertEqual(extract_command_prefix("git -C dir diff"), "git diff")
        self.assertEqual(extract_command_prefix("python -m unittest tests/test.py"), "python -m unittest")
        self.assertEqual(extract_command_prefix("npm test -- --watch"), "npm test")
        self.assertEqual(extract_command_prefix("cargo check"), "cargo check")
        self.assertEqual(extract_command_prefix("ruff check src/"), "ruff check")
        self.assertEqual(extract_command_prefix("make build"), "make")

    def test_session_approval_store_rules(self):
        store = SessionApprovalStore()

        # ExactMatchRule
        store.add_exact_rule("execute_python", {"code": "1 + 1"})
        self.assertTrue(store.is_approved("execute_python", {"code": "1 + 1"}))
        self.assertFalse(store.is_approved("execute_python", {"code": "2 + 2"}))

        # CommandPrefixRule
        store.add_prefix_rule("bash", "pytest")
        self.assertTrue(store.is_approved("bash", {"command": "pytest tests/test_1.py"}))
        self.assertTrue(store.is_approved("bash", {"command": "pytest tests/test_2.py -v"}))
        self.assertFalse(store.is_approved("bash", {"command": "pytest_extra"}))
        self.assertFalse(store.is_approved("bash", {"command": "git status"}))

        # PathRule
        store.add_path_rule("write_file", "src/foo.py")
        self.assertTrue(store.is_approved("write_file", {"path": "src/foo.py", "content": "v1"}))
        self.assertTrue(store.is_approved("write_file", {"path": "./src/foo.py", "content": "v2"}))
        self.assertFalse(store.is_approved("write_file", {"path": "src/bar.py", "content": "v1"}))

        # ToolWideRule
        store.add_tool_rule("edit_file")
        self.assertTrue(store.is_approved("edit_file", {"path": "any_file.py", "content": "..."}))


class TestPermissionPolicy(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(__file__).parent.parent.resolve()
        self.policy = PermissionPolicy(self.workspace)

    def test_hard_deny_invariants(self):
        hard_cases = [
            ("bash", {"command": "rm -rf /"}),
            ("bash", {"command": "sudo rm -rf /tmp"}),
            ("bash", {"command": "shutdown -h now"}),
            ("bash", {"command": "reboot"}),
            ("bash", {"command": "dd if=/dev/zero of=/dev/sda"}),
            ("run_background", {"command": "mkfs.ext4 /dev/sdb"}),
        ]
        for tool, args in hard_cases:
            decision, reason = self.policy.decide(tool, args)
            self.assertEqual(decision, PermissionDecision.DENY, f"Expected DENY for {args}")
            self.assertIn("hard deny rule", reason)

    def test_workspace_path_escape(self):
        decision, reason = self.policy.decide("write_file", {"path": "../secret.txt", "content": "x"})
        self.assertEqual(decision, PermissionDecision.DENY)
        self.assertIn("escapes workspace", reason)

    def test_safe_bash_auto_allow(self):
        decision, reason = self.policy.decide("bash", {"command": "cat README.md"})
        self.assertEqual(decision, PermissionDecision.ALLOW)
        self.assertIn("Safe read-only", reason)

        decision, reason = self.policy.decide("bash", {"command": "git status"})
        self.assertEqual(decision, PermissionDecision.ALLOW)

    def test_unsafe_bash_requires_ask(self):
        decision, reason = self.policy.decide("bash", {"command": "pytest tests/test.py"})
        self.assertEqual(decision, PermissionDecision.ASK)

        decision, reason = self.policy.decide("bash", {"command": "cat .env"})
        self.assertEqual(decision, PermissionDecision.ASK)


class TestApprovalManagerInteraction(unittest.TestCase):
    def setUp(self):
        self.store = SessionApprovalStore()
        self.manager = ApprovalManager(self.store)

    def test_prefix_approval_flow(self):
        args1 = {"command": "pytest tests/test_a.py"}
        args2 = {"command": "pytest tests/test_b.py -v"}

        # Simulate user selecting 'c' (command prefix)
        with patch("builtins.input", return_value="c"):
            approved = self.manager.request("bash", args1, "run test")
            self.assertTrue(approved)

        # Subsequent call with same prefix should be auto-approved without prompting input
        with patch("builtins.input", side_effect=AssertionError("Should not prompt!")):
            approved2 = self.manager.request("bash", args2, "run test b")
            self.assertTrue(approved2)

    def test_path_approval_flow(self):
        args1 = {"path": "src/app.py", "content": "version 1"}
        args2 = {"path": "src/app.py", "content": "version 2 with modifications"}
        args3 = {"path": "src/other.py", "content": "other"}

        # Simulate user selecting 'p' (path)
        with patch("builtins.input", return_value="p"):
            approved = self.manager.request("write_file", args1, "write code")
            self.assertTrue(approved)

        # Subsequent write to the same path should not prompt
        with patch("builtins.input", side_effect=AssertionError("Should not prompt!")):
            approved2 = self.manager.request("write_file", args2, "write code updated")
            self.assertTrue(approved2)

        # Write to different path should still prompt
        with patch("builtins.input", return_value="y"):
            approved3 = self.manager.request("write_file", args3, "write other")
            self.assertTrue(approved3)

    def test_deny_flow(self):
        with patch("builtins.input", return_value="n"):
            approved = self.manager.request("bash", {"command": "rm file.txt"}, "delete")
            self.assertFalse(approved)


class TestWebApprovalManager(unittest.TestCase):
    def test_web_approval_prefix_and_path(self):
        received_events = []
        mgr = WebApprovalManager(on_approval_needed=lambda data: received_events.append(data))

        import threading

        # Test bash prefix approval in background thread
        def worker_bash():
            return mgr.request("bash", {"command": "pytest tests/test_web.py"}, "run test")

        t = threading.Thread(target=worker_bash)
        t.start()

        # Wait until event is sent
        import time
        for _ in range(50):
            if received_events:
                break
            time.sleep(0.02)

        self.assertEqual(len(received_events), 1)
        event_data = received_events[0]
        self.assertEqual(event_data["suggested_prefix"], "pytest")
        self.assertIn("c", event_data["available_choices"])

        # Resolve with 'c'
        mgr.resolve(event_data["approval_id"], "c")
        t.join(timeout=2)
        self.assertFalse(t.is_alive())

        # Next call with pytest should be auto-approved without creating pending request
        self.assertTrue(mgr.request("bash", {"command": "pytest tests/test_another.py"}, "test"))


if __name__ == "__main__":
    unittest.main()
