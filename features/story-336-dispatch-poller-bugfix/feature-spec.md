# Feature Spec — STORY-336

## Changes

### claude_sdk_tool.py
1. Track `is_error_result` flag through the async loop
2. Set flag when SDK result message has `is_error=True`
3. Set flag when exception handler catches an error
4. After the loop, `sys.exit(1)` if `is_error_result` is True
5. `sys.exit(130)` on KeyboardInterrupt

### dispatch_poller.py — Rate-limit detection
1. Replace `journalctl _PID=` with reading `/tmp/claude-sdlc-logs/session-*.log` files
2. Scan the 3 most recent log files for "hit your limit" pattern
3. Add fast-failure backoff: if story completes in <10s, pause poller for 5 minutes
4. New pause file `/var/run/dispatch-poller-fast-failure-until` with unix timestamp
5. Poll loop checks fast-failure file and clears when expired

### dispatch_poller.py — Branch validation
1. When branch-slug match fails, check all remote branches for recent commits (last 30 min)
2. Use `git branch -r --sort=-committerdate` to find recently-pushed branches
3. Accept validation if any remote branch has commits newer than 30 minutes

### dispatch_poller.py — Retry prompt stripping
1. Before adding `[RETRY N/M]`, strip any existing `[RETRY X/Y]` prefix via regex
