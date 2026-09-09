import re
import shlex
from pathlib import Path


MULTI_SUBCOMMAND_TOOLS = {
    "git",
    "npm",
    "cargo",
    "docker",
    "docker-compose",
    "yarn",
    "pnpm",
    "kubectl",
    "gh",
    "ruff",
}


def extract_command_prefix(command: str) -> str:
    """Extract a command prefix for session authorization.

    For multi-subcommand CLI tools (git, npm, cargo, docker, etc.),
    it extracts the binary and the primary subcommand (e.g. 'git commit', 'npm test').
    For python -m, it extracts 'python -m <module>' (e.g. 'python -m pytest').
    For single-level tools (pytest, ruff, make, etc.), it extracts the binary name.
    If prefixed with 'cd <dir> &&', it preserves the cd prefix and extracts the actual command.
    """
    cmd = command.strip()
    if not cmd:
        return ""

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()

    if not tokens:
        return ""

    prefix_parts: list[str] = []
    idx = 0

    # Handle 'cd <dir> &&' or 'cd <dir> ;' prefix
    if len(tokens) >= 3 and tokens[0] == "cd" and tokens[2] in {"&&", ";"}:
        prefix_parts.extend(["cd", f'"{tokens[1]}"', tokens[2]])
        idx = 3

    if idx >= len(tokens):
        return tokens[0]

    sub_tokens = tokens[idx:]
    first = Path(sub_tokens[0]).name

    if first == "python" and len(sub_tokens) >= 3 and sub_tokens[1] == "-m":
        prefix_parts.append(f"python -m {sub_tokens[2]}")
    elif first in MULTI_SUBCOMMAND_TOOLS and len(sub_tokens) >= 2:
        s_idx = 1
        subcmd = first
        while s_idx < len(sub_tokens):
            t = sub_tokens[s_idx]
            if t in {"-C", "-c"}:
                s_idx += 2
                continue
            if t.startswith("-"):
                s_idx += 1
                continue
            subcmd = f"{first} {t}"
            break
        prefix_parts.append(subcmd)
    else:
        prefix_parts.append(first)

    return " ".join(prefix_parts)


class CommandClassifier:
    """Classifies shell commands to identify safe read-only operations.

    Safe read-only commands:
    1. Only use tools from a verified read-only whitelist (cat, ls, grep, git status, etc.).
    2. Contain no output redirections (>, >>, etc.) and no writing tools (tee, etc.).
    3. Allow pipelines and chaining only if ALL stages are verified safe read-only.
    4. Do not contain command substitutions ($(cmd), `cmd`) or suspicious expansions.
    5. Do not access sensitive files (.env, private keys, credentials).
    6. Do not escape the workspace directory.
    """

    SAFE_READ_COMMANDS = {
        "cat",
        "ls",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "find",
        "pwd",
        "which",
        "whereis",
        "file",
        "stat",
        "tree",
        "echo",
        "printf",
        "diff",
        "uniq",
        "sort",
        "cut",
        "strings",
        "column",
        "col",
        "true",
        "false",
        "cd",
        "sed",
    }

    GIT_SAFE_SUBCOMMANDS = {
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "tag",
        "rev-parse",
        "remote",
        "describe",
        "ls-files",
        "shortlog",
        "cat-file",
        "stash",
    }

    DANGEROUS_FIND_FLAGS = {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fprint",
        "-fprint0",
        "-fls",
    }

    SENSITIVE_KEYWORDS = {
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "authorized_keys",
        "credentials",
    }

    SENSITIVE_EXTENSIONS = {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }

    ALLOWED_SYSTEM_PATHS = {
        "/dev/null",
        "/dev/stdin",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/zero",
    }

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def classify(self, command: str) -> tuple[bool, str]:
        """Classifies a command as safe read-only or requiring approval.

        Returns:
            (is_safe, reason)
        """
        if not isinstance(command, str) or not command.strip():
            return False, "Empty or non-string command"

        cmd = command.strip()

        # Check for command substitution and dynamic expansions
        if "$(" in cmd:
            return False, "Command substitution ($(...)) detected"
        if "`" in cmd:
            return False, "Command substitution (backticks) detected"
        if "${" in cmd:
            return False, "Shell parameter expansion (${...}) detected"

        # Tokenize with punctuation support
        try:
            lexer = shlex.shlex(cmd, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as e:
            return False, f"Failed to tokenize command: {e}"

        if not tokens:
            return False, "No tokens parsed from command"

        # Check for redirection operators (allowing safe discards to /dev/null and stream dups)
        is_safe_redir, redir_reason = self._check_redirections(tokens)
        if not is_safe_redir:
            return False, redir_reason

        # Split into sub-commands by pipeline or chaining operators
        chain_operators = {"|", "&&", "||", ";", "&"}
        sub_commands: list[list[str]] = []
        current_sub_cmd: list[str] = []

        for token in tokens:
            if token in chain_operators:
                if current_sub_cmd:
                    sub_commands.append(current_sub_cmd)
                    current_sub_cmd = []
            else:
                current_sub_cmd.append(token)

        if current_sub_cmd:
            sub_commands.append(current_sub_cmd)

        if not sub_commands:
            return False, "No executable sub-commands found"

        # Validate each sub-command
        for sub_cmd in sub_commands:
            is_sub_safe, reason = self._validate_sub_command(sub_cmd)
            if not is_sub_safe:
                return False, reason

        return True, "Safe read-only command"

    def _check_redirections(self, tokens: list[str]) -> tuple[bool, str]:
        safe_discards = {">/dev/null", "2>/dev/null", "1>/dev/null", "&>/dev/null", "2>&1", "1>&2"}
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in safe_discards or token.endswith(">/dev/null"):
                i += 1
                continue

            if token in {">", ">>", ">|", "&>", ">&", "<&", "<<<", "<>"}:
                if i + 1 < len(tokens):
                    target = tokens[i + 1]
                    if target in {"/dev/null", "/dev/zero"} or (token == ">&" and target in {"1", "2"}):
                        i += 2
                        continue
                return False, f"Output redirection operator {token!r} detected"

            if token == "<" or token == "<<":
                return False, f"Input redirection operator {token!r} detected"

            if any(op in token for op in (">", ">>")):
                return False, f"Output redirection operator {token!r} detected"

            i += 1

        return True, ""

    def _validate_sub_command(self, tokens: list[str]) -> tuple[bool, str]:
        if not tokens:
            return False, "Empty sub-command"

        # Skip leading environment variable assignments: VAR=val
        idx = 0
        while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", tokens[idx]):
            idx += 1

        if idx >= len(tokens):
            return False, "Sub-command only contains environment assignments"

        cmd_tokens = tokens[idx:]
        prog = Path(cmd_tokens[0]).name

        # Check for tee which writes output to files
        if prog == "tee":
            return False, "tee command writes output to files"

        # Check git commands
        if prog == "git":
            is_git_safe, git_reason = self._validate_git_command(cmd_tokens)
            if not is_git_safe:
                return False, git_reason
        elif prog not in self.SAFE_READ_COMMANDS:
            return False, f"Command {prog!r} is not in safe read-only whitelist"

        # Check dangerous find flags
        if prog == "find":
            for t in cmd_tokens[1:]:
                if t in self.DANGEROUS_FIND_FLAGS:
                    return False, f"find flag {t!r} can execute commands or modify files"

        # Check sed commands (in-place modification modifies files)
        if prog == "sed":
            for t in cmd_tokens[1:]:
                if t == "-i" or t.startswith("-i") or t.startswith("--in-place"):
                    return False, "sed in-place editing flag (-i) modifies files"

        # Check all tokens in sub-command for sensitive files and workspace escape
        for t in cmd_tokens[1:]:
            clean_t = t.strip("\"'")
            if not clean_t:
                continue

            # Sensitive file check
            is_sensitive, sensitive_reason = self._check_sensitive_token(clean_t)
            if is_sensitive:
                return False, sensitive_reason

            # Workspace boundary check (if argument looks like a path)
            if not clean_t.startswith("-"):
                is_within, path_reason = self._check_path_bounds(clean_t)
                if not is_within:
                    return False, path_reason

        return True, "Sub-command is safe read-only"

    def _validate_git_command(self, tokens: list[str]) -> tuple[bool, str]:
        # Skip git global options: -C <dir>, -c <config>, --no-pager, --git-dir, --work-tree
        idx = 1
        subcmd: str | None = None

        while idx < len(tokens):
            t = tokens[idx]
            if t in {"-C", "-c", "--git-dir", "--work-tree"}:
                idx += 2
                continue
            if t.startswith("-"):
                idx += 1
                continue
            subcmd = t
            break

        if subcmd is None:
            # bare 'git' or 'git --version'
            return True, "Safe git command"

        if subcmd not in self.GIT_SAFE_SUBCOMMANDS:
            return False, f"git subcommand {subcmd!r} is not in safe read-only whitelist"

        sub_args = tokens[idx + 1:]

        # Specific safety checks for subcommands
        if subcmd == "branch":
            for arg in sub_args:
                if arg in {"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy"}:
                    return False, f"git branch flag {arg!r} modifies branches"
                # If non-flag arguments are present, it might be creating a branch unless --list is used
                if not arg.startswith("-") and "--list" not in sub_args:
                    return False, f"git branch argument {arg!r} might create a branch"

        elif subcmd == "tag":
            for arg in sub_args:
                if arg in {"-d", "--delete", "-a", "-s", "-f", "--force"}:
                    return False, f"git tag flag {arg!r} creates or deletes tags"

        elif subcmd == "stash":
            # Only allow 'git stash list' or 'git stash show'
            if not sub_args or sub_args[0] not in {"list", "show"}:
                return False, "git stash without 'list' or 'show' modifies working directory"

        elif subcmd == "remote":
            disallowed_remote = {"add", "rename", "remove", "rm", "set-url", "prune"}
            for arg in sub_args:
                if arg in disallowed_remote:
                    return False, f"git remote operation {arg!r} modifies remote configuration"

        return True, "Safe git subcommand"

    def _check_sensitive_token(self, token: str) -> tuple[bool, str]:
        t_lower = token.lower()
        t_path = Path(token)
        file_name = t_path.name.lower()

        # .env or .env.local etc.
        if file_name == ".env" or file_name.startswith(".env."):
            return True, f"Access to environment configuration file {token!r} requires approval"

        # Specific sensitive keywords
        for keyword in self.SENSITIVE_KEYWORDS:
            if keyword in file_name or keyword in t_lower:
                return True, f"Access to sensitive credential or key file {token!r} requires approval"

        # Sensitive extensions
        if t_path.suffix.lower() in self.SENSITIVE_EXTENSIONS:
            return True, f"Access to key or certificate file {token!r} requires approval"

        return False, ""

    def _check_path_bounds(self, token: str) -> tuple[bool, str]:
        # Whitelist standard virtual system devices
        if token in self.ALLOWED_SYSTEM_PATHS:
            return True, ""

        # Only evaluate paths that appear to be filesystem paths
        # (e.g. starts with /, ~, or contains .. or path separator)
        if token.startswith(("/", "~")) or ".." in token:
            try:
                expanded = Path(token).expanduser()
                if expanded.is_absolute():
                    resolved = expanded.resolve()
                else:
                    resolved = (self._workspace / expanded).resolve()

                if not resolved.is_relative_to(self._workspace):
                    return False, f"Path {token!r} escapes workspace root"
            except (ValueError, RuntimeError):
                return False, f"Could not resolve path {token!r}"

        return True, ""
