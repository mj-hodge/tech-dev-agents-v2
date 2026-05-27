# Seed: STORY-639 — Cancel endpoint accepts claimed/in_review/needs_info/paused source states

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | `DELETE /dispatch/queue/{story_id}` accepts non-`pending` source states (keeps role gates intact) |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-639/cancel-non-pending-states` |
| Status | Seed written 2026-04-25 ~21:30 UTC |
| Priority | 60 — sister story to STORY-638; closes the second escalation path Morris hit |

---

## 1. Idea / Trigger

On 2026-04-25 ~21:00 UTC, Morris escalated to Mark twice in quick succession. The first (STORY-638) was that `/dispatch/reclaim` doesn't accept `in_review` rows. The second was hunting for a cancel endpoint:

> "So cancel only works on pending items, not claimed/needs_info. For needs_info stories, the right action is to answer the QUESTION.md … or use a different endpoint. Let me check for a fail or release endpoint."

Morris is RIGHT on the diagnosis. The cancel endpoint exists at `DELETE /dispatch/queue/{story_id}` (route in `routes/dispatch.py:660`). It has two gates:

1. **Role gate** (STORY-514, 2026-04-22 tightening): Morris can cancel agent/retry-wrapper stories but NOT Mark-dispatched ones — that's intentional and stays.
2. **State gate**: `db_svc.cancel()` raises `AlreadyClaimedError` (route maps to 409) on anything not in `pending`. This is the real gap. Once a story leaves the queue, there's no API path to terminate it short of running it to completion / failure.

This becomes a problem when an agent gets stuck (STORY-700 today: my test left it in `in_review`; alternatively, a `claimed` row whose SDK died early; a `needs_info` row whose answer is "skip this"; a `paused` row that's superseded). Operators need to be able to gracefully terminate, with audit reason, without going to SQL.

## 2. Problem Statement

- **Real escalation path** with no API answer: Morris (or any operator) cannot cancel a story past `pending`.
- **Asymmetric state machine**: `pending` can be cancelled, but `claimed/in_review/needs_info/paused` cannot — yet all four are non-terminal and recoverable from. The fact that they're recoverable doesn't mean they should be.
- **Audit trail already in place**: the cancel endpoint requires `reason` (≥10 chars) and emits a structured event. Extending the state gate doesn't bypass that — every cancel is still audited.

## 3. Scope Classification

**Small.** Three files:
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — `cancel()` and the underlying state guard
- `tech_dev_agents/ops_console/routes/dispatch.py` — the route at line 660 (docstring update)
- `tests/ops_console/test_dispatch_cancel.py` (new or extended) — RED test then GREEN

No schema, no migration, no front-end. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`tech_dev_agents/ops_console/services/dispatch_db_service.py`** — `cancel()`. Today it raises `AlreadyClaimedError` for any row not `pending`. Extend the allowed source states to `pending`, `claimed`, `in_review`, `needs_info`, `paused`. Keep `completed`, `cancelled`, `failed` as 409 — those are terminal.

  When transitioning from a non-pending source state, also clear the related ephemeral fields:
  - `claimed_by`, `claimed_at` → NULL
  - `review_started_at` → NULL (if it was set)
  - `paused_at` → NULL
  - `needs_info_path` → NULL
  - `current_phase` → NULL
  
  Set `cancelled_at = NOW()`, `status='cancelled'`, `updated_at=NOW()`.

- **`tech_dev_agents/ops_console/routes/dispatch.py`** at line 660 — `cancel_story`:
  - Update docstring: "Cancel a story regardless of in-flight state. Allowed sources: pending, claimed, in_review, needs_info, paused. Terminal sources (completed/cancelled/failed): 409."
  - Map any new error type if introduced (probably reuse `InvalidTransitionError` if needed for terminal states; or just keep `AlreadyClaimedError` mapped to 409 for the terminal cases).
  - Role gates STAY EXACTLY AS-IS. Mark-enqueued stories still require ADMIN. Manager (Morris) can still only cancel non-Mark-enqueued. Agents still 403.

### Files NOT to touch

- The role/auth logic in the route — STORY-514 + 2026-04-22 hardening is correct, do not weaken it.
- The `reason` requirement (min 10 chars) — keep verbatim, that's the audit trail.
- Any schema or migration — none needed.
- `/dispatch/reclaim` — handled by STORY-638 separately.

## 5. The Fix

### Change 1: extend `cancel()` in dispatch_db_service.py

Pseudocode of the fix:

```python
async def cancel(self, story_id: str, repo: str | None = None) -> None:
    # PREVIOUSLY: rejected anything but pending
    # NOW: accept pending/claimed/in_review/needs_info/paused
    async with self._pool.acquire() as conn:
        async with conn.transaction():
            row = await _resolve_row(
                conn, story_id, repo,
                statuses=("pending", "claimed", "in_review", "needs_info", "paused"),
                for_update=True,
            )
            if row is None:
                # Either truly missing or in a terminal state
                terminal = await conn.fetchrow(
                    "SELECT status FROM dispatch_items WHERE story_id=$1 AND ($2::text IS NULL OR repo=$2)",
                    story_id, repo,
                )
                if terminal:
                    raise InvalidTransitionError(
                        f"{story_id} is in terminal state '{terminal['status']}' — cannot cancel"
                    )
                raise NotFoundError(...)
            
            await conn.execute(
                """UPDATE dispatch_items
                   SET status='cancelled',
                       cancelled_at=NOW(),
                       updated_at=NOW(),
                       claimed_by=NULL,
                       claimed_at=NULL,
                       review_started_at=NULL,
                       paused_at=NULL,
                       needs_info_path=NULL,
                       current_phase=NULL
                   WHERE id=$1""",
                row["id"],
            )
```

The implementer should adapt to the actual function shape — this pseudocode shows intent, not literal copy-paste.

### Change 2: route handler error mapping

If `InvalidTransitionError` is the cleanest signal (story in completed/cancelled/failed terminal states), map it to 409 in the route. If `AlreadyClaimedError` is reused, that's also fine — the implementer's call. Keep the response shape identical.

### Change 3: docstring + audit log

Update the route docstring to reflect the new allowed-source matrix. The audit log line already includes the prior status (`was: claimed`) — verify it's emitted for the new sources too.

## 6. Out of Scope

- Removing or relaxing role gates (Mark-dispatched stories still need ADMIN). NOT this story — that's a security regression and explicitly out of bounds.
- Adding bulk-cancel — single story per call. Loops belong on the caller side.
- Distinguishing soft-cancel from hard-delete. We mark `status='cancelled'`; the row stays in the table for history.
- Any additional state transitions (`in_review → pending`, `paused → claimed`) — STORY-638 handles `in_review→claimed` via reclaim; other transitions can be filed as future stories if needed.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/ops_console/test_dispatch_cancel.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| Cancel from `pending` succeeds (regression) | row pending; DELETE with reason="testing pending cancel" | 200; status=cancelled; cancelled_at set |
| Cancel from `claimed` succeeds | row claimed by agent; valid reason | 200; status=cancelled; claimed_by/claimed_at cleared |
| Cancel from `in_review` succeeds | row in_review with review_started_at; valid reason | 200; status=cancelled; review_started_at cleared |
| Cancel from `needs_info` succeeds | row needs_info with needs_info_path; valid reason | 200; status=cancelled; needs_info_path cleared |
| Cancel from `paused` succeeds | row paused with paused_at; valid reason | 200; status=cancelled; paused_at cleared |
| Cancel from `completed` rejected | terminal | 409 `cannot cancel terminal state` |
| Cancel from `cancelled` rejected (idempotency-not-implemented signal) | already cancelled | 409 |
| Cancel from `failed` rejected | terminal-failed | 409 |
| Manager role attempting to cancel a Mark-enqueued claimed story | enqueued_by="mark", status=claimed, role=MANAGER | 403 (role gate fires BEFORE state gate) |
| Agent role attempting any cancel | role=AGENT | 403 |
| Reason missing | no `?reason=` query | 422 (FastAPI validation) |
| Reason < 10 chars | reason="x" | 422 |
| Audit log line emitted | any successful cancel | structured event with story_id, prior status, reason, role |

All tests are unit-level using the existing FastAPI test client + DB fixture pattern in this directory.

## Validation

After Phase 8 lands + ops-console redeploys:

1. Manually test: claim a fresh story to a test agent, then `DELETE /api/dispatch/queue/STORY-X?reason=manual%20test%20of%20claimed%20cancel` → 200, row status=cancelled.
2. Re-run STORY-700 scenario: any state, cancel, observe transition + ephemeral fields cleared.
3. Confirm Morris's `/requeue-failed` skill is unchanged.
4. Confirm role gate still blocks Manager from cancelling `mark`-enqueued claimed stories.

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-639/cancel-non-pending-states`
- Scope: small
- Priority: 60 (closes Morris's second escalation path)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20 min
- Implementing agent should: (a) read `cancel()` in `dispatch_db_service.py` and the route at `routes/dispatch.py:660`, (b) extend the allowed source state set to {pending, claimed, in_review, needs_info, paused}, (c) clear all ephemeral fields on transition, (d) keep role gates and reason requirement EXACTLY AS-IS, (e) write the 13 test cases per seed §7.
