# STORY-534: Test Design — Remove Hot-Path Quota Gates

## Phase 7 — RED State

All tests are written before implementation. They define the target state and will fail (RED) until the quota gate functions and constants are deleted from `sdlc_phase_runner.py`.

---

## Test File

`tests/deployment/test_story534_quota_gate_removal.py`

---

## Test Groups

### Group A — Quota Gate Functions Absent (grep-level)

These tests assert that the quota gate code has been completely removed from the production module. They will fail RED because the functions still exist.

| ID | Test Name | Assertion | Expected RED Reason |
|----|-----------|-----------|---------------------|
| A1 | `test_check_daily_session_cap_not_in_source` | `_check_daily_session_cap` NOT in sdlc_phase_runner source | Function still exists |
| A2 | `test_check_ccusage_quota_not_in_source` | `_check_ccusage_quota` NOT in sdlc_phase_runner source | Function still exists |
| A3 | `test_ccusage_not_in_phase_runner_source` | `ccusage` NOT in sdlc_phase_runner source | Function calls ccusage |
| A4 | `test_quota_low_token_threshold_not_in_source` | `QUOTA_LOW_TOKEN_THRESHOLD` NOT in sdlc_phase_runner source | Constant still present |
| A5 | `test_quota_reset_imminent_not_in_source` | `QUOTA_RESET_IMMINENT_SECONDS` NOT in sdlc_phase_runner source | Constant still present |
| A6 | `test_no_pre_execution_quota_gate_call` | No `_check_daily_session_cap()` invocation in run_phase block | Gate still called at line 2154 |

### Group B — Runtime 429 Handling Preserved (regression guard)

These tests confirm that removing the pre-execution gate did NOT accidentally delete the runtime 429 handling. They should be GREEN both before and after the implementation (they validate the preserved code).

| ID | Test Name | Assertion | Expected State |
|----|-----------|-----------|----------------|
| B1 | `test_detect_rate_limit_still_exists` | `_detect_rate_limit` callable from sdlc_phase_runner | GREEN (preserved) |
| B2 | `test_detect_rate_limit_primary_path` | 'hit your limit' text → `rate_limited=True` | GREEN (preserved) |
| B3 | `test_detect_rate_limit_silent_429` | stop_sequence + rc=1 + duration<10 → `rate_limited=True` | GREEN (preserved) |
| B4 | `test_detect_rate_limit_no_false_positive` | rc=0 → `rate_limited=False` regardless of output | GREEN (preserved) |
| B5 | `test_poller_pause_flag_preserved` | dispatch_poller still references `dispatch-poller-paused-until` | GREEN (preserved) |
| B6 | `test_poller_release_on_429_preserved` | dispatch_poller still calls `/dispatch/release/` on 429 | GREEN (preserved) |

### Group C — Session Count File Absent

The `sdk-sessions-today.count` write logic was inside `_check_daily_session_cap()`. Once the function is deleted, no count file is written.

| ID | Test Name | Assertion | Expected RED Reason |
|----|-----------|-----------|---------------------|
| C1 | `test_no_sdk_sessions_count_file_logic` | `sdk-sessions-today.count` NOT referenced in sdlc_phase_runner source | Reference still in _check_daily_session_cap |

### Group D — Old Test File Deleted

| ID | Test Name | Assertion | Expected RED Reason |
|----|-----------|-----------|---------------------|
| D1 | `test_story527_test_file_deleted` | `tests/test_story527_session_cap.py` does NOT exist | File still exists |

---

## RED State Summary

Before implementation:
- **Group A** (6 tests): All FAIL — functions/constants still present
- **Group B** (6 tests): All PASS — runtime handling already correct
- **Group C** (1 test): FAIL — count file reference in _check_daily_session_cap
- **Group D** (1 test): FAIL — test_story527_session_cap.py still exists
- **Total**: 8 FAIL, 6 PASS

After implementation:
- **All 14 tests**: GREEN

---

## Implementation Plan (Phase 8)

1. Delete `_QUOTA_LOW_TOKEN_THRESHOLD` and `_QUOTA_RESET_IMMINENT_SECONDS` from `sdlc_phase_runner.py`
2. Delete `_check_daily_session_cap()` function from `sdlc_phase_runner.py`
3. Delete `_check_ccusage_quota()` function from `sdlc_phase_runner.py`
4. Delete the pre-execution gate block (lines 2152–2159) from `run_phase()`
5. Delete `tests/test_story527_session_cap.py`
6. Create `tests/deployment/test_story534_quota_gate_removal.py` (this file)
7. Verify all existing tests still pass
