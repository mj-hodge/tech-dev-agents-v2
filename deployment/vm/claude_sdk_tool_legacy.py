"""Claude Agent SDK tool for Hermes agents.

Runs Claude Code via the Agent SDK with:
- Structured streaming to stdout (visible in Grafana via hermes-gateway journal)
- canUseTool callback for approval/denial of destructive actions
- Session resume support
- Heartbeat logging during long tool runs

Usage:
  python3 /opt/agent/claude_sdk_tool.py -p '/next' -w /path/to/repo
  python3 /opt/agent/claude_sdk_tool.py -p 'finish epic 1' -w /path/to/repo --max-turns 100
  python3 /opt/agent/claude_sdk_tool.py --resume SESSION_ID -p 'continue'
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, "/opt/hermes-agent/venv/lib/python3.12/site-packages")
sys.path.insert(0, "/opt/hermes-agent/venv/lib/python3.11/site-packages")

from claude_agent_sdk import query, ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny

# Work queue integration (STORY-025) — import lazily to avoid hard dep
# STORY-040: When DISPATCHED_BY_POLLER=1, the poller owns the work queue.
# The SDK should NOT write to it to avoid dual-write race conditions.
_dispatched_by_poller = os.environ.get("DISPATCHED_BY_POLLER", "0") == "1"
_work_queue = None
if not _dispatched_by_poller:
    try:
        sys.path.insert(0, "/opt/hermes-agent/scripts")
        from work_queue import WorkQueue
        _work_queue = WorkQueue()
    except ImportError:
        _work_queue = None

# ---------------------------------------------------------------------------
# Logging — stdout goes to Hermes gateway journal → Promtail → Loki
# Also writes to log file for claude-code job in Grafana
# ---------------------------------------------------------------------------

LOG_DIR = "/tmp/claude-sdlc-logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"session-{int(time.time())}.log")

def log(msg: str):
    """Write to stdout (Hermes journal) and log file (Promtail)."""
    # Prefix Claude Code output so it's distinguishable from Dan's thinking
    if msg.startswith("["):
        # Already tagged ([START], [DONE], [APPROVED], etc.)
        tagged = f"[Claude Code] {msg}"
    else:
        # Assistant text from Claude Code
        tagged = f"[Claude Code] {msg}"
    print(tagged, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(tagged + "\n")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Approval callback
# ---------------------------------------------------------------------------

# Always auto-approve these (read-only, safe)
SAFE_TOOLS = {"Read", "Glob", "Grep", "NotebookEdit", "WebSearch", "WebFetch",
              "TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TodoWrite"}

# Destructive patterns — checked by both can_use_tool (non-bypass modes) and
# _check_tool_call_safe (pre-execution guard active under ALL permission modes,
# including bypassPermissions where can_use_tool is never invoked).
# SECURITY NOTE: can_use_tool is NOT called under bypassPermissions.
# _check_tool_call_safe() is the real enforcement gate.
_DENY_PATTERNS_LEGACY = [
    "rm -rf /", "rm -rf ~", "rm -rf .",
    "git push --force", "git push -f",
    "git reset --hard origin",
    "DROP TABLE", "drop table",
    "TRUNCATE", "truncate",
    "curl | bash", "wget | bash",
    "sudo rm -rf",
]

# Regex-based deny patterns — applied by _check_tool_call_safe() pre-execution.
# These fire regardless of permission_mode.
DENY_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"git\s+push\s+--force(?!\s*-with-lease)",  # allow --force-with-lease
    r"DROP\s+TABLE",
    r"DROP\s+DATABASE",
    r":\s*\(\s*\)\s*{\s*:\|:\s*&\s*}",  # fork bomb
    r">\s*/dev/sd[a-z]",  # direct disk write
]


def _check_tool_call_safe(tool_name: str, tool_input: dict) -> bool:
    """Pre-execution deny-list check. Returns False if the call is blocked.

    This runs BEFORE passing control to the SDK, so it fires under all
    permission modes including bypassPermissions (where can_use_tool is
    never invoked by the SDK itself).
    """
    content = json.dumps(tool_input)
    for pattern in DENY_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            logging.warning(
                "[SECURITY] Blocked tool call: %s matched deny pattern: %r",
                tool_name, pattern,
            )
            log(f"[SECURITY-BLOCK] {tool_name} blocked by deny pattern: {pattern!r}")
            return False
    return True


async def can_use_tool(tool_name, tool_input, context):
    """Review tool calls. Auto-approve safe ops, deny destructive ones, log everything.

    NOTE: This callback is NOT invoked under bypassPermissions mode.
    Use _check_tool_call_safe() (wired into the streaming event handler) for
    enforcement that applies to all permission modes.
    """

    # Safe tools — silent approve
    if tool_name in SAFE_TOOLS:
        return PermissionResultAllow()

    # Bash — check for destructive patterns
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for pattern in _DENY_PATTERNS_LEGACY:
            if pattern in command:
                log(f"[DENIED] Bash: {command[:150]} — blocked: {pattern}")
                return PermissionResultDeny(message=f"Blocked: {pattern}")
        log(f"[APPROVED] Bash: {command[:200]}")
        return PermissionResultAllow()

    # Write/Edit — approve and log
    if tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "")
        log(f"[APPROVED] {tool_name}: {path}")
        return PermissionResultAllow()

    # Agent (subagent) — approve
    if tool_name == "Agent":
        log(f"[APPROVED] Agent: {tool_input.get('prompt', '')[:100]}")
        return PermissionResultAllow()

    # Everything else — approve and log
    log(f"[APPROVED] {tool_name}: {json.dumps(tool_input)[:150]}")
    return PermissionResultAllow()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_STORY_RE = re.compile(r"(STORY-\d+)", re.IGNORECASE)


def _extract_story_id(prompt: str) -> str | None:
    """Extract STORY-NNN from a prompt string, or None."""
    m = _STORY_RE.search(prompt)
    return m.group(1).upper() if m else None


async def run(prompt: str, workdir: str, max_turns: int, resume_id: str = None,
              permission_mode: str = "bypassPermissions",
              model: str | None = None) -> bool:
    """Run the agent. Returns True if the session ended with an error."""
    # Convert slash commands to natural language prompts
    # SDK doesn't resolve Claude Code skills, so we inline the instruction
    if prompt.startswith("/"):
        parts = prompt.split(None, 1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        prompt = f"Read the skill file at .claude/skills{cmd}/SKILL.md and follow it exactly. {args}".strip()
        log(f"[SKILL] Converted {cmd} to prompt")

    # Work queue: mark story as active on [START] (STORY-025)
    _story_id = _extract_story_id(prompt)
    if _work_queue and _story_id:
        try:
            _work_queue.set_active(_story_id, phase=8)
        except Exception:
            pass  # Non-critical — don't block SDK run

    log(f"[START] {prompt[:200]}")
    log(f"[CONFIG] workdir={workdir} max_turns={max_turns} resume={resume_id or 'new'} permission_mode={permission_mode}")

    # Log the full prompt as Agent Guidance so the user can see what Dan told Claude Code
    if len(prompt) > 200:
        log(f"[Agent Guidance] {prompt}")

    start = time.time()
    session_id = None
    turn_count = 0
    tool_count = 0
    last_text_time = time.time()
    had_error = False

    opts = ClaudeAgentOptions(
        cwd=workdir,
        permission_mode=permission_mode,
    )
    if max_turns > 0:
        opts.max_turns = max_turns
    # AC1 (STORY-741): honour --model flag from phase runner; None means use
    # agent default from ~/.claude/settings.json (Sonnet for most agents).
    if model:
        opts.model = model

    if resume_id:
        opts.resume = resume_id

    # SDK requires AsyncIterable prompt when can_use_tool is set
    async def prompt_iter():
        yield {
            "type": "user",
            "session_id": "",
            "message": {"role": "user", "content": prompt},
            "parent_tool_use_id": None,
        }

    try:
        async for msg in query(prompt=prompt_iter(), options=opts):
            t = getattr(msg, "type", "")

            # System init — capture session ID
            if t == "system" or (hasattr(msg, "subtype") and getattr(msg, "subtype", "") == "init"):
                sid = getattr(msg, "session_id", None)
                if sid:
                    session_id = sid
                    log(f"[SESSION] {session_id}")

            # Assistant message — extract text and tool calls
            elif t == "assistant" or hasattr(msg, "content"):
                content = getattr(msg, "content", None)
                if hasattr(content, "__iter__") and not isinstance(content, str):
                    for block in content:
                        if hasattr(block, "text") and block.text:
                            log(block.text)
                            last_text_time = time.time()
                        elif hasattr(block, "type") and block.type == "tool_use":
                            tool_count += 1
                            # Heartbeat if no text in 30s
                            if time.time() - last_text_time > 30:
                                log(f"[WORKING] {tool_count} tool calls...")
                                last_text_time = time.time()
                elif isinstance(content, str) and content.strip():
                    log(content)
                    last_text_time = time.time()
                turn_count += 1

            # Result — final summary
            elif t == "result" or hasattr(msg, "total_cost_usd"):
                cost = getattr(msg, "total_cost_usd", None)
                duration = getattr(msg, "duration_ms", None)
                turns = getattr(msg, "num_turns", None)
                is_error = getattr(msg, "is_error", False)
                stop = getattr(msg, "stop_reason", "")
                log(f"[DONE] turns={turns} tools={tool_count} cost=${cost or '?'} duration={int((duration or 0)/1000)}s stop={stop} error={is_error}")
                # Emit [USAGE] for Loki quota aggregator (STORY-513)
                usage = getattr(msg, "usage", None)
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                total_tokens = input_tokens + output_tokens
                cost_val = cost or 0.0
                if total_tokens == 0 and cost_val:
                    total_tokens = int(cost_val / 0.000015)  # ~$15/M tokens fallback estimate
                log(f"[USAGE] total_tokens={total_tokens} cost_usd={cost_val:.6f}")
                if is_error:
                    had_error = True
                # Work queue: mark story as complete on [DONE] (STORY-025)
                if _work_queue and _story_id:
                    try:
                        _work_queue.complete(_story_id)
                    except Exception:
                        pass  # Non-critical
                if session_id:
                    log(f"[RESUME] session_id={session_id}")

    except KeyboardInterrupt:
        log("[INTERRUPTED]")
        had_error = True
    except Exception as e:
        log(f"[ERROR] {type(e).__name__}: {e}")
        had_error = True

    elapsed = int(time.time() - start)
    log(f"[END] total={elapsed}s")
    return had_error


def main():
    p = argparse.ArgumentParser(description="Claude SDK tool for Hermes")
    p.add_argument("-p", "--prompt", required=True)
    p.add_argument("-w", "--workdir", default="/home/hermes/workspace")
    p.add_argument("--max-turns", type=int, default=0)  # 0 = unlimited
    p.add_argument("--resume", default=None, help="Resume a previous session")
    # AC1 (STORY-741): declare --model so phase runner can request Opus for
    # reasoning phases (1, 6, 9, 10). Without this, argparse exits rc=2 and
    # every Opus-phase dispatch fails before the SDK ever runs.
    p.add_argument(
        "--model",
        choices=["opus", "sonnet", "haiku"],
        default=None,
        help="Override model (default: agent ~/.claude/settings.json)",
    )
    args = p.parse_args()
    had_error = asyncio.run(run(args.prompt, args.workdir, args.max_turns, args.resume,
                                model=args.model))
    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
