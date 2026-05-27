# STORY-512: Test Design — push-code.sh SDK Safety

> Phase 7 | Scope: Small | Date: 2026-04-22
> Test file: `tests/deployment/test_push_code_safety.sh`

---

## Overview

`push-code.sh` unconditionally restarts `dispatch-poller` after copying code, killing any
active `claude_sdk_tool.py` subprocess mid-story. This test design validates the safety
guard layer: check for a running SDK before restarting, and offer `--force` / `--wait`
escape hatches.

Tests are **bash integration tests** that mock `ssh` and `scp` via PATH prepending.
No actual VM connections are made. Mock SSH parses the command string passed to it and
returns preconfigured responses based on environment variables.

---

## Mock Environment Design

### Mock `ssh` binary (`$TMPDIR/bin/ssh`)
Intercepts all SSH calls made by `push-code.sh`. Behavior is controlled by env vars:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `SDK_RUNNING` | `""` | PID to return from `pgrep claude_sdk_tool.py`. Empty = not running |
| `SDK_FINISH_AFTER` | `99999` | pgrep call count after which SDK "stops" (for --wait tests) |
| `SDK_ACTIVE_IPS` | `""` | Space-separated IPs with SDK running (selective agent test) |
| `PGREP_COUNT_FILE` | `/tmp/pgrep_cnt` | State file for tracking pgrep call count across invocations |
| `RESTART_LOG` | `/tmp/restarts.log` | File where mock SSH appends IP when restart is called |
| `MOCK_MD5` | `deadbeef` | Hash returned for md5sum remote calls |

### Mock `scp` binary (`$TMPDIR/bin/scp`)
Controlled by `SCP_FAIL` env var (default `0`). Returns exit 1 when set to `1`.

### Mock `md5sum` binary (`$TMPDIR/bin/md5sum`)
Returns `$MOCK_MD5  <filename>` to match remote hash, ensuring hash verification passes.

### Configurable poll interval
`PUSH_CODE_POLL_INTERVAL` env var (default: `60` seconds) is used instead of hardcoded
`sleep 60` in the `--wait` polling loop. Tests set this to `1` for fast iteration.

---

## Acceptance Criteria → Test Case Mapping

| AC | Description | Test Cases |
|----|-------------|------------|
| AC-1 | SSH pgrep check before restart | TC-1, TC-2 (absence = restart; presence = skip) |
| AC-2 | Default: skip restart when SDK running | TC-2, TC-8 |
| AC-3 | `--force` / `FORCE_RESTART=1` overrides skip | TC-3, TC-4 |
| AC-4 | `--wait N` polls until idle or timeout | TC-5, TC-6 |
| AC-5 | Exit codes: 0=clean, 0=warned, 1=failure, 2=wait timeout | TC-1,TC-2,TC-7,TC-9 |
| AC-7 | Integration test with mock SDK process | All TCs via mock SSH |

---

## Test Cases

### TC-1: No SDK running — normal restart

**Setup:** `SDK_RUNNING=""`
**Invoke:** `push-code.sh dan`
**Assert:**
- Exit code `0`
- `$RESTART_LOG` has at least one entry (restart was called)
- Output does NOT contain "SDK active"
- Output contains "DONE"

**Covers:** AC-1 (pgrep called but returns empty), AC-5 (exit 0 clean)

---

### TC-2: SDK running, no flags — skip restart (default safe mode)

**Setup:** `SDK_RUNNING="12345"`, no CLI flags
**Invoke:** `push-code.sh dan`
**Assert:**
- Exit code `0` (warn-only, not failure)
- `$RESTART_LOG` is empty (restart NOT called)
- Output contains `"SDK active"`
- Output contains `"restart deferred"`
- Output contains `"DONE (files only"`

**Covers:** AC-1 (pgrep finds PID), AC-2 (default skip), AC-5 (exit 0 with warning)

---

### TC-3: SDK running + `--force` flag — restart anyway

**Setup:** `SDK_RUNNING="12345"`
**Invoke:** `push-code.sh --force dan`
**Assert:**
- Exit code `0`
- `$RESTART_LOG` has an entry (restart WAS called)
- Output does NOT contain "DONE (files only"

**Covers:** AC-3 (`--force` override), AC-5 (exit 0)

---

### TC-4: SDK running + `FORCE_RESTART=1` env — restart anyway

**Setup:** `SDK_RUNNING="12345"`, `FORCE_RESTART=1` in environment
**Invoke:** `push-code.sh dan`
**Assert:**
- Exit code `0`
- `$RESTART_LOG` has an entry (restart WAS called)

**Covers:** AC-3 (env var override), AC-5 (exit 0)

---

### TC-5: `--wait N` — SDK finishes before timeout — restart proceeds

**Setup:** `SDK_RUNNING="12345"`, `SDK_FINISH_AFTER=2`, `PUSH_CODE_POLL_INTERVAL=1`
**Invoke:** `push-code.sh --wait 2 dan`
**Assert:**
- Exit code `0`
- `$RESTART_LOG` has an entry (restart called after SDK stopped)
- Output contains `"waiting"`

**Covers:** AC-4 (--wait polling, SDK finishes), AC-5 (exit 0 after successful wait)

---

### TC-6: `--wait 0.1` — timeout — skip restart, exit 2

**Setup:** `SDK_RUNNING="12345"`, `SDK_FINISH_AFTER=99999`, `PUSH_CODE_POLL_INTERVAL=1`
**Invoke:** `push-code.sh --wait 0.1 dan`
**Assert:**
- Exit code `2`
- `$RESTART_LOG` is empty (restart NOT called)
- Output contains `"waiting"`
- Output contains "timeout" or "deferred"

**Covers:** AC-4 (--wait timeout), AC-5 (exit 2)

---

### TC-7: `--force` + `--wait` together — `--force` wins

**Setup:** `SDK_RUNNING="12345"`
**Invoke:** `push-code.sh --force --wait 30 dan`
**Assert:**
- Exit code `0`
- `$RESTART_LOG` has an entry (immediate restart, no waiting)
- Output does NOT contain `"waiting"`

**Covers:** Edge case from seed.md: "--force wins over --wait"

---

### TC-8: Selective — SDK on one agent, not another

**Setup:** `SDK_ACTIVE_IPS="20.228.224.243"` (dan), no SDK for derrick
**Invoke:** `push-code.sh dan derrick`
**Assert:**
- Exit code `0`
- `$RESTART_LOG` contains derrick's IP (`20.121.210.186`) but NOT dan's (`20.228.224.243`)
- Output for `[dan]` contains "SDK active" / "restart deferred"
- Output for `[derrick]` contains "DONE"

**Covers:** AC-2 (selective restart), edge case: SDK on one VM but not another

---

### TC-9: `scp` failure — exit 1

**Setup:** `SCP_FAIL=1`, `SDK_RUNNING=""`
**Invoke:** `push-code.sh dan`
**Assert:**
- Exit code `1`
- `$RESTART_LOG` is empty (no restart attempted after scp failure)
- Output contains "ERROR"

**Covers:** AC-5 (exit 1 for real failures, distinct from exit 2)

---

### TC-10: pgrep check verifiably called before restart decision

**Setup:** `SDK_RUNNING=""`, capture pgrep call count
**Invoke:** `push-code.sh dan`
**Assert:**
- `$PGREP_COUNT_FILE` exists and count >= 1 (pgrep was called)
- Restart also happened (count >= 1 AND restart called)

**Covers:** AC-1 (check must happen before restart block)

---

### TC-11: Warning message format — AC-2 log line

**Setup:** `SDK_RUNNING="12345"`
**Invoke:** `push-code.sh dan`
**Assert:**
- Output line matching: `[dan] SDK active (PID 12345, story ...)`
- Output line matching: `Use --force to override`

**Covers:** AC-2 specific message format requirement

---

## Edge Cases Covered

| Edge Case | Test |
|-----------|------|
| SDK running on one VM, not another | TC-8 |
| `--force` + `--wait` coexistence | TC-7 |
| `FORCE_RESTART=1` env (not `--force` flag) | TC-4 |
| scp failure before SDK check | TC-9 |
| Exit code 2 distinct from exit code 1 | TC-6 vs TC-9 |
| `--wait 0.1` (sub-minute float) | TC-6 |

---

## Out of Scope

- Hash mismatch test (existing behavior, not changed by this story)
- Smoke test failure path (existing behavior)
- Rate-limited agent path (`masked`/`disabled` — existing behavior unchanged)
- `--wait` with ENV var `WAIT_FOR_IDLE=N` (same code path as `--wait N`, covered by TC-5/6)
- `WAIT_FOR_IDLE_SECONDS` unit (minutes × 60, validated by TC-6 timeout arithmetic)

---

## Running the Tests

```bash
# From repo root:
bash tests/deployment/test_push_code_safety.sh

# Exit code: 0 = all pass, 1 = one or more failures
```
