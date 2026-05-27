# Test Design: STORY-641 — Auto-retry classifies failure type before re-dispatching

**Phase:** 7 — Test Design  
**Scope:** Small  
**Coverage target:** 50% (critical paths)  
**Date:** 2026-04-25  

---

## Problem

`_report_fail` in `dispatch_poller.py` treats every non-zero exit code as retryable.
On 2026-04-25, five rework dispatches hit a deterministic `branch_mismatch` guard and
auto-retried twice each — 15 wasted retry cycles, zero useful work.

---

## What's Being Tested

Two new items added in Phase 8:
1. **`_classify_failure(error_message: str) -> str`** — helper that maps error text to a failure class
2. **`_report_fail(... error_message: str = "")`** — new param + never-retry gate using the classification

Test file: `tests/deployment/test_dispatch_poller_retry_classification.py`

---

## Test Groups

### Group A — `_classify_failure` unit tests (14 tests)

Pure function: takes error text, returns class name. No mocks needed.

| ID | Test name | Input | Expected class |
|----|-----------|-------|---------------|
| A1 | `test_branch_mismatch_text_returns_branch_mismatch_code_bug` | `"branch_mismatch expected ... got ..."` | `branch_mismatch_code_bug` |
| A2 | `test_acceptance_diff_missing_returns_gate_rejected_code_bug` | `"Acceptance Diff missing for STORY-629"` | `gate_rejected_code_bug` |
| A3 | `test_tests_failed_text_returns_gate_rejected_code_bug` | `"5 tests failed in ..."` | `gate_rejected_code_bug` |
| A4 | `test_gate_rejected_literal_returns_gate_rejected_code_bug` | `"gate_rejected: deliverable missing"` | `gate_rejected_code_bug` |
| A5 | `test_permission_denied_returns_auth_credential` | `"Permission denied (publickey)"` | `auth_credential` |
| A6 | `test_http_403_returns_auth_credential` | `"HTTP 403 Forbidden"` | `auth_credential` |
| A7 | `test_http_401_returns_auth_credential` | `"HTTP 401 Unauthorized"` | `auth_credential` |
| A8 | `test_no_space_left_returns_disk_full` | `"No space left on device"` | `disk_full` |
| A9 | `test_enospc_returns_disk_full` | `"npm ERR! ENOSPC: no space left"` | `disk_full` |
| A10 | `test_rate_limit_text_returns_unknown` | `"429 rate limit exceeded"` | `unknown` |
| A11 | `test_arbitrary_unrecognized_error_returns_unknown` | `"unhandled exception..."` | `unknown` |
| A12 | `test_empty_string_returns_unknown` | `""` | `unknown` |
| A13 | `test_none_error_message_returns_unknown` | `None` | `unknown` |
| A14 | `test_never_retry_classes_set_contains_four_classes` | n/a | `NEVER_RETRY_CLASSES` == `{branch_mismatch_code_bug, gate_rejected_code_bug, auth_credential, disk_full}` |

**Why A10 returns `unknown` (not a never-retry class):** Rate limits are handled by the existing `exit_code=429` gate in `_report_fail`. Classification would be redundant and could shadow the pause-flag mechanism.

---

### Group B — `_report_fail` never-retry gate (7 tests)

Mock `session.post`; inspect which URLs are called.

Key assertions:
- `/api/dispatch/fail/{story_id}` — MUST be called (story IS failed; ghost-claim prevention)
- `/api/dispatch` — must NOT be called for never-retry classes

| ID | Test name | Error message | Expected |
|----|-----------|--------------|---------|
| B1 | `test_branch_mismatch_skips_retry_posts_fail` | `"branch_mismatch expected X got Y"` | `/fail` ✓, `/dispatch` ✗ |
| B2 | `test_acceptance_diff_missing_skips_retry` | `"Acceptance Diff missing..."` | `/fail` ✓, `/dispatch` ✗ |
| B3 | `test_tests_failed_skips_retry` | `"5 tests failed..."` | `/fail` ✓, `/dispatch` ✗ |
| B4 | `test_permission_denied_skips_retry` | `"Permission denied (publickey)"` | `/fail` ✓, `/dispatch` ✗ |
| B5 | `test_disk_full_skips_retry` | `"No space left on device"` | `/fail` ✓, `/dispatch` ✗ |
| B6 | `test_never_retry_still_posts_fail_endpoint` | `"branch_mismatch..."` | `/fail` always called |
| B7 | `test_output_variance_retryable_vs_never_retry` | unknown vs branch_mismatch | retry POST count differs |

B7 is the **output-variance gate** required by Phase 7 policy: two different inputs must produce demonstrably different outputs.

---

### Group C — Regression: retryable classes still retry (5 tests)

Guards against over-restriction. These must stay GREEN through Phase 8.

| ID | Test name | Scenario | Expected |
|----|-----------|----------|---------|
| C1 | `test_unknown_error_still_retries` | `"unhandled exception..."` | retry posted |
| C2 | `test_empty_error_message_still_retries` | `error_message=None` | retry posted |
| C3 | `test_rate_limit_exit_code_still_skips_retry` | `exit_code=429` | no retry (429 gate wins) |
| C4 | `test_phantom_claim_sub30s_skips_retry_even_with_retryable_error` | `duration=5s` | no retry (phantom guard wins) |
| C5 | `test_no_repo_skips_retry_regardless_of_error_class` | `repo=""` | no retry (pre-641 guard wins) |

C3–C5: existing guards must not be defeated by the new classification layer.

---

### Group D — Audit log emission (5 tests)

Every classification decision must emit structured log lines to stdout (journalctl) for production audit.

| ID | Test name | What it checks |
|----|-----------|---------------|
| D1 | `test_never_retry_logs_failure_class_and_skip_decision` | `failure_class=` + `retry_decision=skip` in stdout |
| D2 | `test_retryable_logs_failure_class_and_allowed_decision` | `failure_class=` + `retry_decision=allowed` in stdout |
| D3 | `test_never_retry_log_includes_dispatch_prefix` | `[DISPATCH]` prefix on all log lines |
| D4 | `test_never_retry_log_includes_story_id` | `STORY-641` in the log line |
| D5 | `test_never_retry_log_includes_failure_class_value` | class name (`branch_mismatch_code_bug`) in log |

---

## Test Coverage Summary

| Group | Tests | Focus |
|-------|-------|-------|
| A | 14 | `_classify_failure` unit (every pattern + edge cases) |
| B | 7 | Never-retry gate in `_report_fail` |
| C | 5 | Regression: retryable behavior preserved |
| D | 5 | Audit log structure |
| **Total** | **31** | |

---

## RED State Reasons

All 31 tests fail because:
- `dispatch_poller._classify_failure` does not exist
- `dispatch_poller.NEVER_RETRY_CLASSES` does not exist
- `dispatch_poller._report_fail` has no `error_message` parameter
- No never-retry gate or audit log in `_report_fail`

Failure mode: `AttributeError` or `TypeError` (unexpected kwarg) — not import errors.

---

## Phase 8 Implementation Checklist

Phase 8 must turn all 31 RED tests GREEN by:

1. **Add `NEVER_RETRY_PATTERNS` and `_classify_failure()`** before `_report_fail` in `dispatch_poller.py`
2. **Add `NEVER_RETRY_CLASSES`** set
3. **Add `error_message: str = ""`** parameter to `_report_fail`
4. **After the 429 gate**, insert:
   ```python
   failure_class = _classify_failure(error_message)
   print(f"[DISPATCH] STORY-{story_id} failure_class={failure_class} retry_decision={'skip' if failure_class in NEVER_RETRY_CLASSES else 'allowed'}", flush=True)
   if failure_class in NEVER_RETRY_CLASSES:
       # emit structured event if available
       return
   ```
5. **Thread `error_message`** from the call sites in `_run_and_complete` (the else-branch at line ~935 and the finally-block at line ~1165) — trace stderr/phase_reason to find the right source

---

## Defensive Gates Applied

| Gate | Status |
|------|--------|
| Gate 1 (Null/None boundary) | ✅ A12, A13: None and empty string return 'unknown', don't crash |
| Gate 2a (External API isolation) | N/A — no external API writes in this story |
| Gate 2b (API degradation) | N/A |
| Gate 10 (Error observability) | ✅ D1–D5: every classification is logged |
| Output-variance gate | ✅ B7: two different inputs → two different retry outcomes |

---

## Files Changed

| File | Change |
|------|--------|
| `tests/deployment/test_dispatch_poller_retry_classification.py` | New — 31 RED tests |
| `features/story-641-auto-retry-classify-failure/test-design.md` | This file |
| `.project` | Phase 7 marked complete |
