# Analysis — STORY-336

## Root Cause Analysis

### Bug 1: SDK exit code
The `run()` async function in `claude_sdk_tool.py` catches `Exception` at line 194 but only logs — never sets a non-zero exit. The `main()` wrapper at line 212 only catches exceptions from `asyncio.run()` itself, not from the SDK reporting `is_error=True` in a result message.

**Impact**: Every rate-limit, every SDK error returns rc=0. Poller treats as success.

### Bug 2: journalctl detection
`dispatch_poller.py:496-506` runs `journalctl _PID=<sdk_pid>`. But `subprocess.Popen(cmd)` captures nothing — stdout/stderr go to the parent process's stdout, not journald. The journal only has systemd-level messages for the PID, not application output.

**Impact**: `rate_limited_until` is always None. Pause flag never written. Poller keeps claiming stories that instantly fail.

### Bug 3: Branch slug matching
`story_id.split('-')[1]` extracts "315" from "STORY-315". `git branch -r --list *315*` finds nothing when STORY-315 is a remediation story working on branch `story-322/fix-auth`.

**Impact**: Validation always fails for remediation stories, triggering re-enqueue.

### Bug 4: Retry tag stacking
`_report_fail` builds `retry_prompt = f"[RETRY {attempt}/{MAX_RETRY_ATTEMPTS}] {prompt}"` where `prompt` already contains the previous `[RETRY X/Y]` tag from the prior attempt.

**Impact**: Prompt bloat. By attempt 3: `[RETRY 3/3] [RETRY 2/3] [RETRY 1/3] <original>`.

## Approach
Direct targeted fixes in the two files. No architectural changes needed.
