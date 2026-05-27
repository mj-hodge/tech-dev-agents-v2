# Test Design: STORY-638 — Expand `/dispatch/reclaim` to accept `in_review`

## Overview

Unit tests for the expanded `force_claim()` method and `/dispatch/reclaim` route
to verify that `in_review` is accepted as a valid source state, that
`review_started_at` is cleared on transition, and that all existing transitions
remain unaffected (regression coverage).

## Test File

`tests/ops_console/test_dispatch_reclaim.py`

## Test Harness

Uses the same `FakeConn` / `FakePool` mock pattern established in
`tests/ops_console/test_dispatch_claim_sync.py`. No live database required.

Route-level tests use `httpx.AsyncClient` with a mocked `DispatchDBService` via
`app.state` patching, or direct service-level tests with the fake pool.

## Test Matrix (8 cases from seed §7)

| # | Test Name | Layer | Setup | Action | Expected |
|---|-----------|-------|-------|--------|----------|
| 1 | `test_reclaim_from_in_review_succeeds` | Service | Row with `status='in_review'`, `review_started_at=<timestamp>` | `force_claim(story, agent)` | Returns row with `status='claimed'`, `claimed_by=agent`, `review_started_at=NULL` |
| 2 | `test_reclaim_from_pending_still_works` | Service | Row with `status='pending'` | `force_claim(story, agent)` | 200-equivalent; `status='claimed'` |
| 3 | `test_reclaim_from_claimed_still_works` | Service | Row with `status='claimed'`, `claimed_by='other'` | `force_claim(story, agent)` | `status='claimed'`, `claimed_by=agent` (switched) |
| 4 | `test_reclaim_from_failed_still_works` | Service | Row with `status='failed'` | `force_claim(story, agent)` | `status='claimed'` |
| 5 | `test_reclaim_from_completed_rejected` | Service | Row with `status='completed'` | `force_claim(story, agent)` | Raises `InvalidTransitionError` |
| 6 | `test_reclaim_from_cancelled_rejected` | Service | Row with `status='cancelled'` | `force_claim(story, agent)` | Raises `InvalidTransitionError` |
| 7 | `test_reclaim_gate_disabled_returns_404` | Route | `DISPATCH_CLAIM_SYNC_ENABLED=false` | POST `/dispatch/reclaim/STORY-X` | 404 |
| 8 | `test_audit_log_includes_was_in_review` | Route | Row transitions from `in_review` | POST `/dispatch/reclaim/STORY-X` | Log line contains `was: in_review` |

## Assertions

- **State transitions**: `status` field in returned row matches expected value.
- **review_started_at cleared**: When source is `in_review`, the SQL UPDATE must
  set `review_started_at = NULL`. Verified by inspecting the query args passed to
  `FakeConn.fetchrow()`.
- **Terminal rejection**: `InvalidTransitionError` raised for `completed`/`cancelled`.
- **Gate**: 404 when env var disabled.
- **Audit**: Logger `.info()` call includes `was: in_review` substring.
