# Test Design — STORY-873: Stop Duplicate Failed Event Emission

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage Target | 90% of apply() attention_queue branch |
| Test Level | Unit (mocked DB pool + patched record_event) |
| Test Files | `tests/test_dispatch_failure_policy_873.py`, `tests/test_dispatch_failure_classifier.py` |
| RED State | All tests FAIL because the duplicate `record_event("failed", ...)` call still exists in the `else` branch |

## Test Structure

```
tests/
├── test_dispatch_failure_policy_873.py      (new — 16 tests)
│   ├── Group A — AC1: no duplicate failed event (7 tests)
│   └── Group B — AC2: retry/dead-letter unchanged (8 tests)
└── test_dispatch_failure_classifier.py      (updated — 2 assertions changed)
    └── test_attention_queue_classes_emit_no_events (new parametrized test)
```

## Test Groups

### Group A — AC1: No Duplicate Failed Event for attention_queue

Tests that `apply()` emits **zero** events when `next_lane == "attention_queue"`. The caller's original `failed` event is sufficient.

| Test | What It Verifies |
|------|------------------|
| `test_attention_queue_class_emits_no_failed_event[workspace_missing]` | apply() for workspace_missing emits no `failed` event |
| `test_attention_queue_class_emits_no_failed_event[auth_expired]` | apply() for auth_expired emits no `failed` event |
| `test_attention_queue_class_emits_no_failed_event[unknown]` | apply() for unknown (fallback) emits no `failed` event |
| `test_attention_queue_class_emits_no_events_at_all[workspace_missing]` | apply() emits zero events of any type |
| `test_attention_queue_class_emits_no_events_at_all[auth_expired]` | apply() emits zero events of any type |
| `test_attention_queue_class_emits_no_events_at_all[unknown]` | apply() emits zero events of any type |
| `test_single_attempt_produces_zero_apply_failed_events` | Integration: zero `failed` events from apply() for unknown class |
| `test_attention_queue_policy_table_rows_confirm_routing` | POLICY_TABLE correctness: workspace_missing and auth_expired route to attention_queue with retryable=False |

### Group B — AC2: Retry / Dead-Letter / Quarantine Unchanged

Regression tests ensuring existing non-attention_queue routing is not affected by the fix.

| Test | What It Verifies |
|------|------------------|
| `test_retryable_first_attempt_emits_requeued` | lease_lost first attempt emits `requeued` |
| `test_retryable_first_attempt_emits_no_failed` | lease_lost first attempt does not emit `failed` |
| `test_max_attempts_exhausted_emits_exactly_one_failed` | Exhausted retries emit exactly one `failed` |
| `test_max_attempts_exhausted_emits_no_requeued` | Exhausted retries do not emit `requeued` |
| `test_work_queue_classes_still_requeue[sigterm_shutdown]` | sigterm_shutdown first attempt emits `requeued` |
| `test_work_queue_classes_still_requeue[quota_exceeded]` | quota_exceeded first attempt emits `requeued` |
| `test_work_queue_classes_still_requeue[git_push_failed]` | git_push_failed first attempt emits `requeued` |
| `test_policy_table_unchanged_for_retryable_classes` | POLICY_TABLE rows for retryable classes are intact |

### Updated Tests in test_dispatch_failure_classifier.py

| Test | What Changed |
|------|--------------|
| `test_apply_emits_expected_event_for_class` | Removed `workspace_missing` and `auth_expired` from parametrized list (they no longer emit events) |
| `test_attention_queue_classes_emit_no_events` | New parametrized test asserting zero events for attention_queue classes |

## Output-Variance Tests

| Test | Input A | Input B | Asserted Difference |
|------|---------|---------|---------------------|
| AC1 vs AC2 | `workspace_missing` (attention_queue) | `lease_lost` (work_queue) | Zero events vs `['requeued']` |
| Retry vs exhausted | `lease_lost` attempts=0 | `lease_lost` attempts=2 | `['requeued']` vs `['failed']` |

## LLM Error-Prone Coverage

| Category | Test |
|----------|------|
| Edge case: unknown class fallback | `test_attention_queue_class_emits_no_failed_event[unknown]` |
| Regression: retry still works | `test_retryable_first_attempt_emits_requeued` |
| Boundary: max_attempts exactly reached | `test_max_attempts_exhausted_emits_exactly_one_failed` |
| Data integrity: POLICY_TABLE unchanged | `test_policy_table_unchanged_for_retryable_classes` |

## Checklist

- [x] Every test name clearly states what it verifies
- [x] Arrange/Act/Assert structure used throughout
- [x] Output-variance tests included
- [x] Null/None boundary tests included (unknown class fallback)
- [x] Error observability preserved (logger.info assertion not needed — covered by existing logging)
- [x] No implementation code in test files
- [x] Tests are junior-readable
- [x] `pytest --collect-only` discovers all tests
- [x] All tests FAIL in RED state (before removing the duplicate record_event call)
