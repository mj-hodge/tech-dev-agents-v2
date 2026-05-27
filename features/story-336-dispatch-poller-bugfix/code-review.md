# Code Review — STORY-336

## Changes Reviewed

### claude_sdk_tool.py
- **`is_error_result` flag**: Correctly initialized to `False`, set on `is_error=True` in result message and in exception handler. Checked after loop exits.
- **`sys.exit(1)`**: Called when `is_error_result` is True. This ensures non-zero rc propagates to the poller.
- **`sys.exit(130)`**: Standard SIGINT exit code for KeyboardInterrupt.
- **Risk**: Low. Only adds exit calls after the existing logging. No changes to the SDK query loop itself.

### dispatch_poller.py — Rate-limit detection
- **Session log reading**: Reads `/tmp/claude-sdlc-logs/session-*.log` sorted by mtime descending, checks top 3. Correct — the SDK writes to these files.
- **Fast-failure backoff**: Checks `elapsed_secs < 10` when `rc == 0`. Writes two files: the existing pause flag and a new timestamp file. Poll loop correctly handles both file types.
- **Risk**: Medium. The 10-second threshold could theoretically catch a legitimately fast small story. Acceptable tradeoff — a 5-minute pause is minor vs. 67 fast-failures/day.

### dispatch_poller.py — Branch validation
- **Recent-push detection**: Uses `git branch -r --sort=-committerdate` with 30-min cutoff. Falls back gracefully on errors.
- **`effective_branch`**: `has_branch or has_recent_push` — correct OR logic.
- **Risk**: Low. Only activates when the primary branch-slug check fails.

### dispatch_poller.py — Retry stripping
- **Regex**: `re.sub(r"^\s*\[RETRY\s+\d+/\d+\]\s*", "", prompt)` — correctly anchored to start of string, handles whitespace.
- **Risk**: None. Pure string manipulation before constructing the new prompt.

## Verdict
All fixes are targeted, low-risk, and address the documented bugs. Approved.
