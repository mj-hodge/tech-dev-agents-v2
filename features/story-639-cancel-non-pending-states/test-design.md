# Test Design: STORY-639 — Cancel endpoint accepts non-pending source states

**Phase:** 7 (Test Design)
**Date:** 2026-04-25
**Scope:** Small
**State:** RED — 8 FAIL, 5 PASS (see table below)

---

## Overview

`DELETE /dispatch/queue/{story_id}` currently only cancels `pending` rows;
anything else raises `AlreadyClaimedError` (mapped to 409) or `NotFoundError` (mapped to 404).
STORY-639 extends the allowed source states to `pending, claimed, in_review, needs_info, paused`
and changes terminal-state rejection from `NotFoundError` (404) to `InvalidTransitionError` (409).

Three files change: `dispatch_db_service.py`, `routes/dispatch.py` docstring + error map,
and the test file created here.

---

## Test File

```
tests/ops_console/test_dispatch_cancel.py
```

13 tests, collected clean. All tests are unit-level (mock pool / mock db_svc). No real DB required.

---

## Test Matrix

| ID | Test | Group | Level | RED Reason | Expected GREEN behavior |
|----|------|-------|-------|------------|------------------------|
| T01 | `test_cancel_pending_succeeds_regression` | A | service | — (GREEN) | cancel(pending) → status=cancelled |
| T02 | `test_cancel_claimed_succeeds` | A | service | Explicit `if target["status"] == "claimed": raise AlreadyClaimedError` | Remove guard; accept claimed |
| T03 | `test_cancel_in_review_succeeds` | A | service | SQL assertion: `"in_review"` not in `statuses=("pending","claimed")` | Expand statuses; clear `review_started_at` |
| T04 | `test_cancel_needs_info_succeeds` | A | service | SQL assertion: `"needs_info"` not in statuses | Expand statuses; clear `needs_info_path` |
| T05 | `test_cancel_paused_succeeds` | A | service | SQL assertion: `"paused"` not in statuses | Expand statuses; clear `paused_at` |
| T06 | `test_cancel_completed_returns_409` | B | route | NotFoundError → 404 (no terminal check) | InvalidTransitionError → 409 |
| T07 | `test_cancel_already_cancelled_returns_409` | B | route | NotFoundError → 404 | InvalidTransitionError → 409 |
| T08 | `test_cancel_failed_returns_409` | B | route | NotFoundError → 404 | InvalidTransitionError → 409 |
| T09 | `test_manager_blocked_from_mark_enqueued_regardless_of_state` | C | route | — (GREEN) | 403 role gate fires before state gate |
| T10 | `test_agent_role_blocked_from_any_cancel` | C | route | — (GREEN) | 403 from `require_role(MANAGER)` |
| T11 | `test_reason_missing_returns_422` | C | route | — (GREEN) | 422 FastAPI validation |
| T12 | `test_reason_too_short_returns_422` | C | route | — (GREEN) | 422 FastAPI validation |
| T13 | `test_audit_log_emitted_on_successful_cancel_from_claimed` | C | route | Route returns 409 (AlreadyClaimedError) before reaching audit log block | 200 + dispatch_cancelled WARNING log |

**Summary: 8 RED, 5 GREEN**

---

## Groups

### Group A — Service layer (T01–T05)

Uses the actual `DispatchDBService.cancel()` method with a mock asyncpg pool.
The pool mock wires:
- `conn.transaction()` → async context manager
- `conn.fetch()` → list of records (consumed by `_resolve_row`)
- `conn.fetchrow()` → single record (consumed by the UPDATE RETURNING)

For T02 (claimed): the existing guard `if target["status"] == "claimed": raise AlreadyClaimedError`
fires before the UPDATE, so the test raises unexpectedly.

For T03–T05 (in_review, needs_info, paused): a **SQL-capture function** is installed on
`conn.fetch` to intercept the args passed to `_resolve_row`. The test asserts that the
statuses list passed includes the expected state. Current code passes `("pending","claimed")` —
the assertion `"in_review" in statuses` fails → RED.

The SQL capture approach also locks the `_resolve_row` interface: Phase 8 must pass
the expanded statuses tuple, not just change the UPDATE.

### Group B — Route layer: terminal states (T06–T08)

Uses httpx `AsyncClient` with `inject_mock_services(app, dispatch_db_service=mock)`.
The mock db_svc is configured to raise `NotFoundError` (current service behavior for
terminal rows, since they're not in the `("pending","claimed")` statuses set).

The route maps `NotFoundError → 404`. Tests assert 409.
After Phase 8: service raises `InvalidTransitionError` for terminal rows; route maps it to 409.

**Phase 8 mock update required:** Change `cancel_raises=NotFoundError(...)` →
`cancel_raises=InvalidTransitionError(...)` for T06–T08 once the route handler is
updated to map `InvalidTransitionError → 409`.

### Group C — Route layer: gates, validation, audit (T09–T13)

T09 (manager + mark-enqueued → 403): Verifies role gate fires BEFORE `cancel()` is called.
`db_svc.cancel.assert_not_called()` confirms the order. GREEN — already implemented.

T10 (agent role → 403): `require_role(Role.MANAGER)` dependency rejects AGENT-scoped keys.
Uses a second HTTP client configured with `agent_role_api_key`. GREEN — already enforced.

T11, T12 (reason validation → 422): FastAPI enforces `min_length=10` on `?reason=`.
GREEN — no code change needed.

T13 (audit log for claimed cancel): Sets `cancel_raises=AlreadyClaimedError` (current behavior).
Route returns 409 → `assert resp.status_code == 200` fails. RED.
**Phase 8 mock update:** Change `cancel_raises=AlreadyClaimedError(...)` →
`cancel_return=_cancelled_row()` to verify audit log is emitted on success.

---

## What Phase 8 Must Implement

### Change 1: `dispatch_db_service.py` — `cancel()` method

1. Expand `_resolve_row` statuses from `("pending","claimed")` to
   `("pending","claimed","in_review","needs_info","paused")`.
2. Remove the guard `if target["status"] == "claimed": raise AlreadyClaimedError`.
3. If `_resolve_row` returns `None` (no active row), check for a terminal row:
   ```python
   terminal = await conn.fetchrow(
       "SELECT status FROM dispatch_items WHERE story_id=$1 AND ($2::text IS NULL OR repo=$2)",
       story_id, repo,
   )
   if terminal:
       raise InvalidTransitionError(f"{story_id} is in terminal state '{terminal['status']}'")
   raise NotFoundError(f"{story_id} not found")
   ```
4. Change the UPDATE to clear all ephemeral fields:
   ```sql
   UPDATE dispatch_items
      SET status='cancelled', cancelled_at=NOW(), updated_at=NOW(),
          claimed_by=NULL, claimed_at=NULL,
          review_started_at=NULL, paused_at=NULL,
          needs_info_path=NULL, current_phase=NULL
    WHERE id=$1
   RETURNING *
   ```

### Change 2: `routes/dispatch.py` — `cancel_story` handler

1. Add `InvalidTransitionError` to the except chain, mapped to 409:
   ```python
   except InvalidTransitionError as exc:
       raise HTTPException(409, str(exc))
   ```
2. Update the docstring to reflect the new allowed source states.

### Change 3: Phase 8 test mock updates

After implementing, update the mocks in:
- T06–T08: `cancel_raises=NotFoundError(...)` → `cancel_raises=InvalidTransitionError(...)`
- T13: `cancel_raises=AlreadyClaimedError(...)` → `cancel_return=_cancelled_row()`

---

## Coverage

| Layer | Coverage target |
|-------|----------------|
| `cancel()` state machine | All 8 states (pending/claimed/in_review/needs_info/paused/completed/cancelled/failed) |
| Ephemeral field clearance | claimed_by, claimed_at, review_started_at, paused_at, needs_info_path, current_phase |
| Role gate | MANAGER + mark-enqueued → 403; AGENT → 403 |
| Reason validation | Missing → 422; short → 422 |
| Audit log | dispatch_cancelled event on success |
| Error mapping | NotFoundError → 404; AlreadyClaimedError → 409; InvalidTransitionError → 409 |

---

## RED State Verification

```
$ python3 -m pytest tests/ops_console/test_dispatch_cancel.py -v
PASSED  T01 test_cancel_pending_succeeds_regression
FAILED  T02 test_cancel_claimed_succeeds           (AlreadyClaimedError raised)
FAILED  T03 test_cancel_in_review_succeeds         (statuses assertion: in_review not in set)
FAILED  T04 test_cancel_needs_info_succeeds        (statuses assertion: needs_info not in set)
FAILED  T05 test_cancel_paused_succeeds            (statuses assertion: paused not in set)
FAILED  T06 test_cancel_completed_returns_409      (404 != 409)
FAILED  T07 test_cancel_already_cancelled_returns_409 (404 != 409)
FAILED  T08 test_cancel_failed_returns_409         (404 != 409)
PASSED  T09 test_manager_blocked_from_mark_enqueued_regardless_of_state
PASSED  T10 test_agent_role_blocked_from_any_cancel
PASSED  T11 test_reason_missing_returns_422
PASSED  T12 test_reason_too_short_returns_422
FAILED  T13 test_audit_log_emitted_on_successful_cancel_from_claimed (409 != 200)

8 failed, 5 passed
```

All failures are for the right reasons — no collection errors, no import errors.
