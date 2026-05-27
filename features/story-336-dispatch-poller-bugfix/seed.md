# Seed — STORY-336: Fix dispatch_poller.py and claude_sdk_tool.py

## Problem
4 bugs in the agent dispatch infrastructure causing 41 story failures:

1. **SDK exits rc=0 on rate-limit**: `claude_sdk_tool.py` exception handler catches errors but never calls `sys.exit(1)`. Rate-limited SDK returns rc=0, poller thinks success, validation finds no branch, re-enqueues — 60-second failure loop.

2. **Rate-limit detection broken**: Poller uses `journalctl _PID=<sdk_pid>` but SDK stdout goes through subprocess pipes, not journald. The `rate_limited_until` flag is NEVER set. Derrick had 67 fast-failures in one day.

3. **Branch validation wrong for remediation stories**: Branch name derived from `story_id.split('-')[1]`. Remediation stories working on foreign branches (e.g. STORY-315 fixing PR #39 on branch story-322/...) always fail validation.

4. **Retry prompt stacking**: Each retry wraps entire previous prompt with `[RETRY N/3]`. By attempt 3, there are 3 nested wrappers bloating the prompt.

## Scope
Medium — 2 files, 4 targeted fixes.

## Files
- `/opt/agent/claude_sdk_tool.py` — Bug 1
- `/opt/agent/dispatch_poller.py` — Bugs 2, 3, 4
