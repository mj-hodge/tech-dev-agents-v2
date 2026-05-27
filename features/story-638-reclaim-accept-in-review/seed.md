# Seed: STORY-638 — Expand `/dispatch/reclaim` to accept `in_review` as a source state

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | `/dispatch/reclaim/{story_id}` accepts `in_review` rows in addition to `pending/claimed/failed` |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-638/reclaim-accept-in-review` |
| Status | Seed written 2026-04-25 ~21:05 UTC |
| Priority | 60 — closes a real escalation path Morris hit today; not blocking pipeline |

---

## 1. Idea / Trigger

On 2026-04-25 ~21:00 UTC, Morris escalated to Mark because STORY-700 was stuck in `status='in_review'` with no API path to recover it:

> "STORY-700 stuck: It's in `in_review` state (claimed by Dan) — the API won't let me DELETE or FAIL it. … The /fail endpoint returns 404 for in_review items — likely needs a different API path or admin intervention."

Morris did the right thing — he tried each capability he knows (`/dispatch/fail`, `/dispatch/reclaim`, the new `/requeue-failed` skill), each correctly refused per its current contract, and he escalated. The wall is real:

- `/dispatch/reclaim` allows `pending/claimed/failed → claimed` (per its docstring at `tech_dev_agents/ops_console/routes/dispatch.py:1372`). `in_review` is NOT included.
- `/dispatch/fail` expects `claimed` source state.
- `/requeue-failed` skill is intentionally scoped to `status='failed'` so it doesn't trample real reviews.
- No other endpoint allows `in_review → anything`.

Result: any row that lands in `in_review` (correctly or by mistake) becomes a one-way trap requiring direct SQL via SSH-jump. That's how STORY-700 got there today — a `/dispatch/review/STORY-700` test call left it in_review with no actual review happening, no `pr_number`, no movement.

## 2. Problem Statement

- **Real escalation path** with no answer: Morris cannot recover any stuck `in_review` row through API.
- **Asymmetric state machine**: every other recoverable state (`pending`, `claimed`, `failed`) has a path to `claimed` via `/reclaim`. `in_review` is the only orphan.
- **Genuine reviews stay protected** because the operator (or skill) decides when to invoke `/reclaim` — no automatic transition. Adding `in_review` to the allowed sources does NOT cause auto-recovery; it just makes manual recovery possible without SQL.

## 3. Scope Classification

**Small.** Three files:
- `tech_dev_agents/ops_console/routes/dispatch.py` — update `reclaim_story` docstring + (if any client-side guard exists) extend the allowed-source list
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — `force_claim()` is the function that does the transition. Read it; ensure its state guard accepts `in_review`. Add to allowed-states if not already.
- `tests/ops_console/test_dispatch_reclaim.py` (new or extended) — RED test then GREEN

No schema, no migration, no front-end, no cross-repo. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`tech_dev_agents/ops_console/routes/dispatch.py`** — `@router.post("/dispatch/reclaim/{story_id}")` around line 1358. The docstring says "Allowed transitions: pending/claimed/failed → claimed". Update to include `in_review`. The route itself probably delegates state validation to the DB service.

- **`tech_dev_agents/ops_console/services/dispatch_db_service.py`** — `force_claim()` (search by name). Look for a list/tuple of allowed source statuses; add `'in_review'`. If raised as `InvalidTransitionError`, ensure that check no longer rejects `in_review`.

- **`tech_dev_agents/ops_console/models/responses.py`** — `ReclaimResponse` already exists; the response payload doesn't change.

### Files NOT to touch

- DB schema or any migration — column already exists, no schema change.
- `/dispatch/fail`, `/dispatch/cancel`, `/dispatch/complete` — different code paths; not fixing those gaps in this story.
- `/requeue-failed` skill — keep its scope narrow (failed-only); the broader recovery path is via expanded reclaim, not skill expansion.

## 5. The Fix

### Change 1: extend allowed source states in `force_claim()`

In `dispatch_db_service.py`'s `force_claim()`, the SQL likely has something like:
```sql
WHERE status IN ('pending', 'claimed', 'failed')
```
or a Python-side guard. Add `'in_review'`:
```sql
WHERE status IN ('pending', 'claimed', 'failed', 'in_review')
```

When transitioning from `in_review`, also clear `review_started_at` (set to NULL) so the row's review marker doesn't carry over to the next claim. Same shape as the other resets.

### Change 2: update the route docstring

```python
"""Force-claim a story regardless of its current queue state.

Allowed transitions: pending/claimed/failed/in_review → claimed
Terminal states (completed/cancelled) → 409
"""
```

Plus update the in-line comment about the env-var gate.

### Change 3: log the source state in the structured log

The route already logs `"Force-reclaimed %s for agent %s (was: %s)"`. Confirm `was=in_review` shows up in production journals when this case fires (no code change needed if the format string is unchanged — just verify).

## 6. Out of Scope

- Adding `/dispatch/cancel/{story_id}` for `in_review/paused → cancelled` — that's a separate, broader admin endpoint (call it STORY-639 if needed).
- Auto-detection of "stuck" in_review rows by Morris — different story; current escalation path is acceptable for now.
- Schema migration for any new column — none needed.
- Updating the requeue-failed skill — explicitly out of scope; the skill should stay narrow.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/ops_console/test_dispatch_reclaim.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| Reclaim from `in_review` succeeds | Insert row status='in_review', review_started_at=NOW(); POST /dispatch/reclaim with valid agent | 200 OK; status='claimed'; claimed_by=agent; claimed_at set; review_started_at cleared (NULL) |
| Reclaim from `pending` still works (regression) | row status='pending'; reclaim | 200 OK; status='claimed' |
| Reclaim from `claimed` still works (regression) | row status='claimed', claimed_by=other; reclaim with new agent | 200 OK; claimed_by switched |
| Reclaim from `failed` still works (regression) | row status='failed'; reclaim | 200 OK; status='claimed' |
| Reclaim from `completed` rejected | row status='completed'; reclaim | 409 Conflict; row unchanged |
| Reclaim from `cancelled` rejected | row status='cancelled'; reclaim | 409 Conflict; row unchanged |
| Gate disabled → 404 | DISPATCH_CLAIM_SYNC_ENABLED=false | 404 (existing behavior preserved) |
| Audit log includes `was=in_review` | reclaim from in_review | log line `Force-reclaimed STORY-X for agent Y (was: in_review)` |

All tests are unit-level with mocked DB / fixtures. No live DB required (use the existing test harness pattern in this directory).

## Validation

After Phase 8 lands + ops-console redeploys:

1. Manually create an `in_review` row (or wait for a real one), call `POST /dispatch/reclaim/STORY-X` — should return 200 and move it to `claimed`.
2. Re-run a representative existing reclaim test against prod data path (smoke).
3. Confirm Morris's `/requeue-failed` skill is unchanged (no scope creep).

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-638/reclaim-accept-in-review`
- Scope: small
- Priority: 60 (closes a Morris escalation path; not blocking pipeline)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~15 min
- Implementing agent should: (a) read `force_claim()` in `dispatch_db_service.py` and identify exactly where the state guard lives, (b) extend it to include `in_review` and reset `review_started_at` to NULL on transition, (c) update the route docstring, (d) write 8 test cases covering the matrix above, (e) ensure existing reclaim tests still pass.
