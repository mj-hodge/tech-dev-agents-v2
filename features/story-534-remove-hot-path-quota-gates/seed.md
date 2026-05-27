# STORY-534: Remove Hot-Path Quota Gates

## Metadata
| Field | Value |
|-------|-------|
| Story ID | STORY-534 |
| Scope | Small |
| Phase Path | 1 → 7 → 8 → Done |
| Story Type | Bug Fix / Cleanup |
| Priority | High |

## Problem Statement

Two pre-execution quota gates remain in the hot path and are causing agents to bench themselves unnecessarily:

1. **`_check_daily_session_cap()` + `_check_ccusage_quota()` in `sdlc_phase_runner.py`** — Before every phase execution, the runner spawns a `ccusage blocks --json` subprocess to probe token quota. If quota looks low or a billing reset is imminent (< 15 min), the phase is refused entirely. This gate has caused agents to sit idle with plenty of real runtime capacity remaining (e.g., Daisy benched at 42M tokens, Devon wedged at 80/80 sessions).

2. **The `count_file` session counter in `_check_daily_session_cap()`** — Still increments and reads a `sdk-sessions-today.count` file that was supposed to be deleted in STORY-538. The per-day cap constants (`_QUOTA_LOW_TOKEN_THRESHOLD`, `_QUOTA_RESET_IMMINENT_SECONDS`) are still present as module-level globals.

The existing `test_story527_session_cap.py` tests reference `_SESSION_COUNT_FILE` and `_DAILY_SESSION_CAP` attributes that no longer exist (causing AttributeError on monkeypatch), so those tests must be replaced.

## Root Cause

STORY-538 partially cleaned up the quota gates by deleting the per-day session ceiling and `_daily_session_count`. However, it left behind:
- `_check_daily_session_cap()` (lines 526–580 in `sdlc_phase_runner.py`)
- `_check_ccusage_quota()` (lines 583–660 in `sdlc_phase_runner.py`)
- The module-level `_QUOTA_LOW_TOKEN_THRESHOLD` and `_QUOTA_RESET_IMMINENT_SECONDS` constants
- The invocation at line 2154: `if not _check_daily_session_cap()`
- `test_story527_session_cap.py` which tests behavior that no longer exists

## What Gets Removed

### `deployment/hermes/sdlc_phase_runner.py`
- `_QUOTA_LOW_TOKEN_THRESHOLD` constant (line 447)
- `_QUOTA_RESET_IMMINENT_SECONDS` constant (line 448)
- `_check_daily_session_cap()` function (lines 526–580)
- `_check_ccusage_quota()` function (lines 583–660)
- The pre-execution gate call at lines 2152–2159 (the `if not _check_daily_session_cap():` block)

### `tests/test_story527_session_cap.py`
- Entire file deleted (tests reference non-existent `_SESSION_COUNT_FILE` and `_DAILY_SESSION_CAP` attributes)

## What Gets Preserved

### Runtime 429 Handling (MUST NOT CHANGE)
- `_detect_rate_limit()` in `sdlc_phase_runner.py` (lines 958–1014) — detects quota errors at runtime
- `_is_paused()` in `dispatch_poller.py` — checks pause flag before claiming
- Pause flag writing on 429 in `dispatch_poller.py` (lines 1132–1154)
- Claim release logic on 429 in `dispatch_poller.py` (lines 1143–1151)
- All `test_poller_rate_limit_recovery.py` tests (Groups B, C, D, E, F, G) — these test runtime handling

## Acceptance Criteria

1. `_check_daily_session_cap` does NOT appear in `sdlc_phase_runner.py` source
2. `_check_ccusage_quota` does NOT appear in `sdlc_phase_runner.py` source
3. `ccusage` does NOT appear in `sdlc_phase_runner.py` source
4. `QUOTA_LOW_TOKEN_THRESHOLD` does NOT appear in `sdlc_phase_runner.py` source
5. `QUOTA_RESET_IMMINENT_SECONDS` does NOT appear in `sdlc_phase_runner.py` source
6. Phase execution proceeds without any pre-flight quota check
7. `_detect_rate_limit()` still exists and correctly detects 429s at runtime
8. `test_story527_session_cap.py` is deleted (replaced by story-534 tests)
9. All existing `test_poller_rate_limit_recovery.py` tests still pass
10. All pre-existing tests still pass (no regressions)

## New Tests (Story 534)

A new test file `tests/deployment/test_story534_quota_gate_removal.py` validates:
- Group A (grep-level): The quota gate functions/constants are absent from source
- Group B (behavioral): Phase execution is not blocked when quota would have triggered the old gate
- Group C (regression): `_detect_rate_limit` still works (runtime 429 handling preserved)

**Frontend:** false

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
