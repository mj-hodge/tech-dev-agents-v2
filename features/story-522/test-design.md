# STORY-522: Test Design — SIGTERM (rc=-15) Investigation and Fix

## Test Strategy

Three categories of tests as mandated by the dispatch brief, plus targeted unit tests for each fix candidate.

### Test File Location

`tests/deployment/test_sigterm_protection.py`

---

## Test Matrix

### A. Unit Tests — SIGTERM handler registration (Fix 1: main-thread registration)

| ID | Test | AC | Condition | Expected | Fails Because |
|----|------|----|-----------|----------|---------------|
| T522-01 | `test_install_shutdown_handler_succeeds_in_main_thread` | AC1 | Call `install_shutdown_handler()` from main thread | `signal.getsignal(SIGTERM)` returns `_graceful_shutdown` | Handler registration succeeds — verifies the function works in the right context |
| T522-02 | `test_install_shutdown_handler_fails_silently_in_non_main_thread` | AC1 | Call `install_shutdown_handler()` from a daemon thread | No exception raised; handler NOT registered on SIGTERM | Current behavior: silent failure is documented, not a bug itself |
| T522-03 | `test_main_thread_sigterm_registration_in_poll_loop` | AC1 | `poll_loop()` or `run_dispatch_poller` entry point registers SIGTERM handler | SIGTERM handler is `_graceful_shutdown` after `poll_loop` initialization | Currently unimplemented — handler only called from daemon thread via `run_sdlc_phases` |

### B. Unit Tests — rc=-15 propagation and partial work saving (Dispatch Brief Test 1)

| ID | Test | AC | Condition | Expected | Fails Because |
|----|------|----|-----------|----------|---------------|
| T522-04 | `test_run_phase_sdk_propagates_sigterm_exit_code` | AC5 | `_run_phase_sdk` with mock subprocess returning rc=-15 (killed by SIGTERM) | Returns `(-15, ...)` tuple | This is existing behavior — verifies it's preserved |
| T522-05 | `test_run_phase_sdk_saves_partial_work_on_sigterm` | AC5 | `_run_phase_sdk` with mock subprocess returning rc=-15 | `_save_partial_work` is called even on rc=-15 | Currently DOES call `_save_partial_work` after ANY exit — verifies the path |
| T522-06 | `test_save_partial_work_commits_dirty_files` | AC3 | Call `_save_partial_work` with dirty git workdir | `git add -A` and `git commit` are called | Existing behavior verification |
| T522-07 | `test_save_partial_work_refuses_wrong_branch` | AC3 | Call `_save_partial_work` when current branch != expected story branch | No commit/push; branch_mismatch event emitted | STORY-511 Fix #3 — existing guard |

### C. Unit Tests — systemd cgroup isolation (Fix 2)

| ID | Test | AC | Condition | Expected | Fails Because |
|----|------|----|-----------|----------|---------------|
| T522-08 | `test_systemd_unit_has_killmode_process` | AC2 | Read `dispatch-poller.service` | Contains `KillMode=process` | Currently missing — default is `control-group` which kills children |
| T522-09 | `test_sdk_subprocess_uses_new_session` | AC2 | `_run_phase_sdk` creates subprocess | `subprocess.run()` called with `start_new_session=True` | Currently not set — SDK child inherits parent's process group |

### D. Integration Test — Zero SIGTERMs in normal operation (Dispatch Brief Test 2)

| ID | Test | AC | Condition | Expected | Fails Because |
|----|------|----|-----------|----------|---------------|
| T522-10 | `test_no_sigterm_during_normal_sdk_execution` | AC6 | Run a dummy "SDK" subprocess for 180s while poll_loop runs alongside | Zero SIGTERM signals sent to the dummy PID | If the poller or recovery loop sends SIGTERM, the subprocess would exit early |
| T522-11 | `test_poller_does_not_kill_active_sdk` | AC6 | Poller sees `is_agent_idle() == False` during SDK execution | `poll_once` returns "busy"; no process signals sent | Verifies the idle-detection guard works correctly |

### E. Regression Test — 10-minute Phase 6 scenario (Dispatch Brief Test 3)

| ID | Test | AC | Condition | Expected | Fails Because |
|----|------|----|-----------|----------|---------------|
| T522-12 | `test_phase6_10min_no_sigterm` | AC7 | Simulate "Devon works Phase 6 for 10 minutes" — mock SDK that runs for 600s (accelerated) | rc=0 at completion; no SIGTERM received by child | If any component sends SIGTERM within 10-minute window, the mock SDK would report it |
| T522-13 | `test_stale_claim_recovery_does_not_signal_agents` | AC7 | Run `recover_stale_claims()` while an SDK is active | Only DB state changes; no os.kill/signal sent | Server-side recovery is data-only — this confirms it stays that way |

---

## Implementation Notes

### Mocking Strategy

**For `_run_phase_sdk` (T522-04, T522-05):** Patch `subprocess.run` to return a `MagicMock(returncode=-15, stderr="")`. Also patch `_save_partial_work` to capture calls without doing real git operations.

**For SIGTERM delivery detection (T522-10, T522-12):** Use a real subprocess running `sleep N` (or a Python script that logs signals). Check its exit code — if it's -15 or 143, a SIGTERM was delivered. For accelerated tests, use short durations with patched timeouts.

**For systemd unit file checks (T522-08):** Read the service file from disk and parse for expected directives. No mocking needed.

**For process isolation check (T522-09):** Capture the kwargs passed to `subprocess.run` inside `_run_phase_sdk` and assert `start_new_session=True`.

### RED State Rationale

Tests T522-03, T522-08, and T522-09 are expected to FAIL (RED) because the fixes haven't been implemented yet:
- T522-03: SIGTERM handler not yet registered in main thread
- T522-08: `KillMode=process` not yet added to systemd unit
- T522-09: `start_new_session=True` not yet passed to subprocess

Tests T522-04, T522-05, T522-06, T522-07 should PASS (GREEN) because they verify existing correct behavior that must be preserved.

Tests T522-10, T522-11, T522-12, T522-13 are integration/regression tests that validate the end-to-end fix.

---

## E2E Validation (Post-implementation, NOT in unit tests)

After implementing fixes, run the dispatch queue for 1 hour with Medium-scope stories on at least one agent. Zero rc=-15 events acceptable. If ANY rc=-15 fires during the hour, the fix is incomplete.

This is validated operationally via Loki queries and journal inspection, not via automated test.
