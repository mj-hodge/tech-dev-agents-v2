# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Criticality | high |
| Feature Name | cancel-propagation |
| Frontend | false |

## Problem Statement

Today (2026-05-05) Mark cancelled 14 dispatch rows via `DELETE /api/dispatch/queue/{story_id}` (v1 cancel route). All 14 returned 200 OK from v1, but the dashboard kept showing them because the dashboard reads the v2 queue (`dispatch_state_current`) which never received a `cancelled` event. Manual SQL was required to clean the dashboard. Every Morris autofix that cancels a story has the same desync.

Root cause: v1 cancel route (`routes/dispatch.py`) cancels the v1 row in the `dispatch_queue` table but does not emit any event into `dispatch_v2_events`. The v2 dashboard trigger only fires on `dispatch_v2_events` inserts.

## Target User / Use Case

**Mark (operator):** `DELETE /api/dispatch/queue/{story_id}` cancels a story cleanly — both v1 and v2 show `cancelled` immediately, no manual SQL cleanup.

**Morris (autofix flows):** Same route works for Morris-initiated cancels. No more orphan rows accumulating in the v2 dashboard.

**Ops console users:** Can use the new `POST /api/dispatch/v2/operator/cancel` endpoint to cancel a v2 job directly without needing to go through the v1 route.

## Success Criteria

- [ ] New `POST /api/dispatch/v2/operator/cancel` route (MANAGER role) accepts `(job_id)` or `(repo, story_id)`, emits `cancelled` event, returns `{job_id, prior_state}`
- [ ] 409 returned when story is already in terminal state
- [ ] 404 returned when story not found
- [ ] 400 returned when neither `job_id` nor `(repo + story_id)` provided
- [ ] `reason` field required, min 10 chars, max 500 chars (Pydantic gate)
- [ ] v1 `cancel_story` handler propagates `cancelled` event to `dispatch_v2_events` after successful v1 cancel
- [ ] Propagation is atomic within v1 cancel transaction
- [ ] No v2 row → idempotent skip (no error, no INSERT)
- [ ] Multiple non-terminal v2 rows → most recent picked (ORDER BY created_at DESC LIMIT 1)
- [ ] Terminal-only v2 rows → skip silently
- [ ] All 7 new v2 operator cancel tests GREEN
- [ ] All 4 new v1 propagation tests GREEN
- [ ] No regressions in pre-existing passing tests

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal |
| Timeline | 1 day |
| Scale | ops_console routes only |

## Security Constraints (Non-Negotiable)

- [ ] `operator/cancel` requires MANAGER role (same as `operator/resume`)
- [ ] Do not break v1 cancel's `enqueued_by`-source gating (Mark-enqueued stories require admin override)
- [ ] Propagation failure must never block v1 cancel response (best-effort, logged)

## Operational Lifecycle

- STORY-897 (queue sweeper, in flight) handles outbound scan of pre-existing orphan rows
- This story handles event propagation going forward
- 14 rows cleaned manually on 2026-05-05; no further manual SQL should be needed after deploy

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/routes/dispatch_v2.py`, `tech_dev_agents/ops_console/routes/dispatch.py` |
| Related components | `dispatch_state_current` (v2 dashboard table), `dispatch_v2_events` (trigger source) |
| Current behavior | v1 cancel updates v1 table only; v2 dashboard shows stale state |
| Desired change | v1 cancel also INSERTs cancelled event into dispatch_v2_events; new v2 operator/cancel route |
| Architecture constraints | Pure addition; no restructuring of existing endpoints |

## Test Criteria

1. `tests/ops_console/test_dispatch_v2_operator_cancel.py` — 7 tests covering:
   - Happy path with `(repo, story_id)` — 200, cancelled event INSERT verified
   - Happy path with `job_id` — 200
   - 409 on terminal state
   - 404 on unknown story
   - 400 when neither identifier provided
   - 422 when reason < 10 chars (Pydantic gate)
   - 403 when agent-role key used (MANAGER required)

2. `tests/ops_console/test_dispatch_v1_cancel_emits_v2_event.py` — 4 tests covering:
   - v1 cancel inserts v2 cancelled event when active v2 row exists
   - v1 cancel idempotent when no v2 row (no error, execute not called)
   - Picks most recent active row when multiple non-terminal exist
   - Does not insert when only terminal v2 rows exist

## Validation

- After deploy to hermes, cancel any pending story via `DELETE /api/dispatch/queue/{story_id}`; verify dashboard immediately shows `cancelled` state without manual SQL
- POST to `/api/dispatch/v2/operator/cancel` with `{repo, story_id, reason}` from Mark's API key; verify 200 and dashboard updates
- Attempt cancel of already-terminal story; verify 409
- Verify Morris autofix cancel flows no longer produce orphan rows in v2 dashboard
