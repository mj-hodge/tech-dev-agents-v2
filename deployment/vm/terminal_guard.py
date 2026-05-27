"""Terminal command guard — restricts Hermes to whitelisted commands only.

Deployed to /opt/agent/terminal_guard.py on each agent VM.
Patched into Hermes via hermes-config.yaml hooks or monkey-patch at gateway start.

Commands not matching the whitelist are DENIED with an error message
instructing the agent to use Claude Code SDK instead.
"""

import re
import shlex
import sys

# ---------------------------------------------------------------------------
# Whitelist patterns — commands the agent is allowed to run
# ---------------------------------------------------------------------------

# Exact prefix matches (checked after splitting on &&, ;, |)
ALLOWED_PREFIXES = [
    # Claude Code SDK — the primary coding tool
    "python3 /opt/agent/claude_sdk_tool.py",
    "python3 /home/hermes/claude_sdk_tool.py",

    # Git read-only
    "git log",
    "git status",
    "git diff",
    "git branch",
    "git show",
    "git rev-parse",
    "git remote",

    # Git write (needed after SDK commits)
    "git push",
    "git pull",
    "git fetch",
    "git checkout",
    "git switch",

    # GitHub CLI
    "gh pr",
    "gh run",
    "gh issue",
    "gh repo",

    # Hermes tools
    "hermes cron",
    "hermes skill",

    # System health + service management
    "systemctl status",
    "systemctl is-active",
    "systemctl restart",
    "systemctl start",
    "systemctl stop",
    "systemctl show",
    "systemctl is-enabled",
    "id",
    "hostname",
    "df ",
    "df -h",
    "free",
    "uptime",
    "docker ps",
    "docker images",
    "docker stats",

    # Directory listing (non-source, for orientation)
    "ls ",
    "ls -",
    "pwd",
    "which ",
    "whoami",

    # SSH to other agent VMs (for deployment tasks)
    "ssh ",
    "scp ",

    # Claude Code CLI
    "claude ",
    "claude-real ",

    # Test running (read-only verification)
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "npm test",
    "npm run test",

    # Basic shell (Claude Code SDK needs these)
    "echo ",
    "echo",
    "git --version",
    "git -C",
    "git add",
    "git commit",
    "git stash",
    "git merge",
    "git rebase",
    "git clone",
    "git config",
    "git init",
    "git tag",
    "git worktree",
    "cat ",
    "head ",
    "tail ",
    "grep ",
    "find ",
    "wc ",
    "mkdir ",
    "touch ",
    "date",
    "env",
    "printenv",
    "true",
    "false",
    "test ",
    "[",
    "node ",
    "npm ",
    "npx ",
    "pip ",
    "pip3 ",
    "python3 -m ",
    "python3 /opt/agent/",

    # Read-only process inspection (Morris/manager fleet monitoring).
    "ps ",
    "ps",
    "pgrep ",

    # Pipeline filter for monitoring output (df -h | awk '{print $5}').
    "awk ",

    # Manager operational commands — Morris needs these for ad-hoc debugging,
    # API testing, cron verification, and fleet monitoring. The dangerous form
    # (curl | bash) is still blocked by DENY_PATTERNS.
    "curl ",
    "curl -",
    "sleep ",
    "ss ",
    "ss -",
    "netstat ",
    "netstat -",
    "jq ",
    "sort ",
    "uniq ",
    "tr ",
    "cut ",
    "xargs ",
    "basename ",
    "dirname ",
    "realpath ",
    "timeout ",

    # Agent operational scripts (deployed by ops, not user-writable).
    # Morris needs to trigger fleet-check manually for testing.
    "/opt/agent/",
    "bash /opt/agent/",
    "sudo /opt/agent/",
    "sudo -u hermes /opt/agent/",
    "sudo -u hermes bash /opt/agent/",
    "sudo -u hermes claude ",
]

# Patterns that are ALWAYS denied, even if they match a prefix
DENY_PATTERNS = [
    r"\bcat\b.*\.(py|ts|tsx|js|jsx|md|json|yaml|yml|toml|cfg|html|css|sql)\b",
    r"\bhead\b.*\.(py|ts|tsx|js|jsx|md|json|yaml|yml|toml|cfg|html|css|sql)\b",
    r"\btail\b.*\.(py|ts|tsx|js|jsx|md|json|yaml|yml|toml|cfg|html|css|sql)\b",
    r"\bsed\b",
    # awk is allowed for read-mode pipelines (df | awk '{print $5}'); block
    # the dangerous forms: in-place edit and output redirect.
    r"\bawk\b\s+-i\b",
    r"\bawk\b.*>",
    r"\btee\b",
    r"\becho\b.*>",
    r"\bprintf\b.*>",
    # cat with output redirect is a file write — covers `cat > f`, `cat >> f`,
    # and `cat > f << EOF` heredoc form. Read-only `cat file` and pipes
    # (`cat f | head`) are unaffected because they have no `>`.
    r"\bcat\b\s+>",
    r"python3?\s+-c\b",
    r"\bpatch\b",
    r"\bvi\b",
    r"\bvim\b",
    r"\bnano\b",
    r"\bcp\b.*\.(py|ts|tsx|js|jsx|md|json|yaml|yml)\b",
    r"\bmv\b.*\.(py|ts|tsx|js|jsx|md|json|yaml|yml)\b",
    r"\brm\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bcurl\b.*\|\s*bash",
    r"\bwget\b.*\|\s*bash",
    # Process killing is dangerous — `ps` is on the allowlist for read-only
    # inspection but `pkill`/`kill` could take down hermes itself.
    r"\bpkill\b",
    r"\bkillall\b",
    r"\bkill\b\s+-?\d",  # kill <pid> or kill -SIGNAL <pid>
]

DENY_MESSAGE = (
    "DENIED: Direct file/code manipulation is not allowed. "
    "You MUST use Claude Code SDK for all coding work:\n"
    "  python3 /opt/agent/claude_sdk_tool.py -p '<prompt>' -w <repo_path>\n\n"
    "If the SDK is broken, message Mark immediately:\n"
    "  'Blocked: Claude Code SDK failed — [error]. Cannot proceed without it.'"
)


def _split_compound_quote_aware(command: str) -> list[str]:
    """Split a command on &&, ||, ;, | while respecting single/double quotes
    and backslash-escaped characters.

    Critical for commands like `gh pr list --jq '.[] | .title'` — the `|`
    inside the jq filter is NOT a pipe, so a naive regex-split would
    incorrectly break the command into unrelated segments.
    """
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(command)
    in_single = False
    in_double = False
    while i < n:
        c = command[i]
        # Backslash escape — pass both chars through untouched
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(command[i + 1])
            i += 2
            continue
        # Toggle quote state (but only one type at a time)
        if c == "'" and not in_double:
            in_single = not in_single
            buf.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            buf.append(c)
            i += 1
            continue
        # Only honor operators when outside both quote types
        if not in_single and not in_double:
            if c == "&" and i + 1 < n and command[i + 1] == "&":
                parts.append("".join(buf))
                buf = []
                i += 2
                continue
            if c == "|" and i + 1 < n and command[i + 1] == "|":
                parts.append("".join(buf))
                buf = []
                i += 2
                continue
            if c == "|":
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
            if c == ";":
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
        buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def check_command(command: str) -> tuple[bool, str]:
    """Check if a command is allowed.

    Returns:
        (True, "") if allowed.
        (False, reason) if denied.
    """
    stripped = command.strip()

    # Always allow empty commands
    if not stripped:
        return True, ""

    # Split compound commands (&&, ||, ;, |) — quote-aware so that pipes
    # inside quoted strings (like `gh pr list --jq '.[] | .title'`) are
    # treated as part of the argument, not as shell operators.
    parts = _split_compound_quote_aware(stripped)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Manager operational carve-out: allow read + copy within hermes-
        # owned paths BEFORE the deny-pattern check fires. Source-tree
        # files in ~/dev/ still route through the SDK.
        _SAFE_PATHS = (
            "/home/hermes/state/",
            "/home/hermes/.hermes/",
            "/var/log/",
            "/tmp/",
            "/opt/agent/",
        )
        _READ_CMDS = ("cat ", "head ", "tail ", "less ", "wc ")
        _WRITE_CMDS = ("cp ", "mv ", "ln ", "mkdir ", "touch ")
        _is_safe_op = False

        def _path_in_safe(path: str) -> bool:
            """Check path is within safe dirs AND has no traversal."""
            if ".." in path:
                return False
            return any(path.startswith(p) for p in _SAFE_PATHS)

        # Read carve-out (cat/head/tail on safe paths, no stdout redirect).
        # Strip stderr redirects (2>/dev/null, 2>&1) before checking — those
        # are error suppression, not file writes.
        _part_no_stderr = re.sub(r'\s*2>[>&/\w]+', '', part)
        if ">" not in _part_no_stderr:
            for rcmd in _READ_CMDS:
                if _part_no_stderr.startswith(rcmd):
                    tokens = _part_no_stderr[len(rcmd):].strip().split()
                    path = tokens[-1] if tokens else ""
                    if _path_in_safe(path):
                        _is_safe_op = True
                    break

        # Write/copy carve-out: cp/mv/ln/mkdir/touch where ALL path
        # args are within safe paths (prevents copying INTO ~/dev/)
        if not _is_safe_op:
            for wcmd in _WRITE_CMDS:
                if part.startswith(wcmd):
                    tokens = part[len(wcmd):].strip().split()
                    paths = [t for t in tokens if not t.startswith("-")]
                    if paths and all(_path_in_safe(p) for p in paths):
                        _is_safe_op = True
                    break

        if _is_safe_op:
            continue

        # Operational script execution — skip deny patterns for /opt/agent/
        # scripts. The `\bpatch\b` pattern would otherwise false-positive on
        # filenames like "weekly-patch.sh".
        _OPS_SCRIPT_PREFIXES = (
            "/opt/agent/", "bash /opt/agent/", "sudo /opt/agent/",
            "sudo -u hermes /opt/agent/", "sudo -u hermes bash /opt/agent/",
            "sudo -u hermes claude ",
        )
        if any(part.startswith(p) for p in _OPS_SCRIPT_PREFIXES):
            continue

        # SSH inner command carve-out: when the part starts with `ssh`,
        # the inner command runs on the REMOTE VM where that VM's own
        # guard handles it. Don't double-check deny patterns locally —
        # remote `awk`, `sed`, `python3 -c` are the remote agent's
        # problem, not ours. We already verified `ssh ` is allowlisted.
        if part.startswith("ssh "):
            continue

        # Claude Code invocation from any cwd (cd X && claude -p ...)
        if "claude -p " in part or "claude --" in part:
            # Check the claude portion is using an allowed prefix
            if any(frag in part for frag in ("claude -p ", "claude --permission-mode", "claude --max-turns")):
                continue

        # Check deny patterns first (overrides allow)
        for pattern in DENY_PATTERNS:
            if re.search(pattern, part):
                return False, DENY_MESSAGE

        # Check if command matches any allowed prefix
        allowed = False
        for prefix in ALLOWED_PREFIXES:
            if part.startswith(prefix) or part.lstrip("cd /;&& ").startswith(prefix):
                allowed = True
                break

        # Also allow 'cd' followed by an allowed command
        if part.startswith("cd "):
            allowed = True

        # Allow 'source' for venv activation before pytest
        if part.startswith("source ") and ".venv" in part:
            allowed = True

        if not allowed:
            return False, DENY_MESSAGE

    return True, ""
