# Test Design — STORY-336

## Verification Tests

### Bug 1: SDK exit code
- **Test**: Simulate `is_error=True` in SDK result — verify process exits non-zero
- **Test**: Simulate exception in async loop — verify `sys.exit(1)` called
- **Verify**: `is_error_result` flag is initialized False, set True on error, checked at end

### Bug 2: Rate-limit detection
- **Test**: Create a fake session log with "hit your limit" text, verify detection reads it
- **Test**: Fast-failure detection: mock a <10s completion, verify pause file is written
- **Test**: Poll loop correctly reads and expires fast-failure-until file after 5 min

### Bug 3: Branch validation
- **Test**: Remediation story with no branch-slug match but recent commits on foreign branch — passes
- **Test**: Story with no branch match AND no recent commits — still fails validation
- **Verify**: `has_recent_push` flag is checked alongside `has_branch`

### Bug 4: Retry prompt stripping
- **Test**: `_report_fail` with prompt already containing `[RETRY 1/3]` — output has single `[RETRY 2/3]` prefix
- **Test**: `_report_fail` with clean prompt — output has `[RETRY 1/3]` prefix
- **Regex test**: `re.sub(r"^\s*\[RETRY\s+\d+/\d+\]\s*", "", "[RETRY 1/3] do stuff")` → `"do stuff"`

## Manual Verification
```bash
# Bug 1: Check exit code handling exists
grep -n "sys.exit" /opt/agent/claude_sdk_tool.py

# Bug 2: Check log file reading (not journalctl)
grep -n "session-\*\.log" /opt/agent/dispatch_poller.py
grep -cL "journalctl" /opt/agent/dispatch_poller.py  # should NOT match

# Bug 3: Check recent-push detection
grep -n "has_recent_push" /opt/agent/dispatch_poller.py

# Bug 4: Check retry stripping
grep -n "stripped_prompt" /opt/agent/dispatch_poller.py
```
