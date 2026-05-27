# STORY-873 — Stop Duplicate Failed Event Emission

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Frontend | false |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Date | 2026-05-05 |

---

## Problem Statement

`dispatch_failure_policy.apply()` currently emits **two** `failed` events for a single failed attempt when the policy routes to `attention_queue`:

1. **Event 1** — emitted by the caller (dispatcher/poller) when the job actually fails. The DB trigger `dispatch_state_apply_trg` processes this and sets `state=failed, lane=attention_queue` on `dispatch_state_current`.

2. **Event 2** — emitted by `apply()` in the `else` branch (lines 596–609) with `failure_reason=f"policy_routed: {failure_class}"`. This was intended to record the policy decision, but since `dispatch_state_current` is already in the correct state from Event 1, this second event:
   - Adds a redundant row to `dispatch_v2_events`
   - Inflates failure counts (any query `COUNT(*) WHERE event_type='failed'` double-counts)
   - Obscures root cause in dashboards and metrics
   - Causes the attempt-counter query in `apply()` itself to over-count on subsequent calls

The retry/dead-letter/quarantine paths are **not affected** — they emit non-`failed` event types (`requeued`, `dead_lettered`, `quarantined`). The duplicate only occurs in the `attention_queue` branch.

---

## Solution

Replace the secondary `failed` event emission in the `else` block of `apply()` with a **no-op log statement**. The policy routing decision is already captured by:
- The `logger.info` at the top of `apply()` (logs `next_lane`, `failure_class`, `retryable`, `max_attempts`)
- The original `failed` event (Event 1, emitted by caller)

No DB schema changes, no new event types, no state machine changes required.

---

## Acceptance Criteria

- **AC1:** A single failed attempt produces exactly one `failed` event in `dispatch_v2_events` (apply() emits zero additional `failed` events for attention_queue routing)
- **AC2:** Existing retry (`requeued`), dead-letter (`dead_lettered`), and quarantine (`quarantined`) behavior is unchanged
- **AC3:** All tests green (zero regressions)

---

## Acceptance Diff

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` | Replace `record_event("failed", ...)` in `else` block with `logger.info(...)` |
| `tests/test_dispatch_failure_policy_873.py` | New test file: AC1 + AC2 coverage |
| `tests/test_dispatch_failure_classifier.py` | Update stale assertions for attention_queue classes |

## Test Criteria

1. `test_attention_queue_class_emits_no_failed_event` — apply() for `workspace_missing`, `auth_expired`, and `unknown` emits zero `failed` events
2. `test_attention_queue_class_emits_no_events_at_all` — apply() for attention_queue classes is a complete no-op (no events emitted)
3. `test_single_attempt_produces_zero_apply_failed_events` — integration check: zero `failed` events from apply() for unknown class
4. `test_attention_queue_policy_table_rows_confirm_routing` — POLICY_TABLE routes `workspace_missing` and `auth_expired` to `attention_queue` with `retryable=False`
5. `test_retryable_first_attempt_emits_requeued` — `lease_lost` first attempt still emits `requeued` (AC2 regression)
6. `test_max_attempts_exhausted_emits_exactly_one_failed` — exhausted retries still emit exactly one `failed` (AC2 regression)
7. `test_work_queue_classes_still_requeue` — retryable work_queue classes (`sigterm_shutdown`, `quota_exceeded`, `git_push_failed`) still emit `requeued` on first attempt
8. `test_attention_queue_classes_emit_no_events` — classifier test updated: attention_queue classes emit no events from apply()

## Validation

After merge and deploy:

1. Trigger a dispatch job that fails with `workspace_missing` classification — verify only **one** `failed` row in `dispatch_v2_events` (the caller's event), not two
2. Trigger a `lease_lost` failure on first attempt — verify `requeued` event is emitted (retry path unchanged)
3. Query `SELECT COUNT(*) FROM dispatch_v2_events WHERE event_type='failed'` before/after — confirm no double-counting inflation
