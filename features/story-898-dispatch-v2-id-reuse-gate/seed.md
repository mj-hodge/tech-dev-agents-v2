# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | critical |
| Feature Name | dispatch-v2-id-reuse-gate |
| Frontend | false |

## Problem Statement

On 2026-05-05 Morris's autofix dispatcher hijacked previously-used `(story_id, repo)` slots — STORY-885, STORY-887, STORY-874, and others — reusing those pairs for completely unrelated rework dispatches once the prior rows reached terminal states. Nine orphaned rows were manually cancelled. The hijack vector: the existing idempotency check only returns an existing job if `(repo, story_id)` has an **active** row. If the only existing rows are terminal, the new payload inserts a fresh job without protest. Without an enqueue-time gate, this will repeat on every Morris autofix cycle.

## Target User / Use Case

**Morris (engineering manager agent):** Every autofix dispatch cycle, Morris calls `/api/dispatch/v2/enqueue` with a story_id that may have been previously used by a different work item. Without the gate, Morris silently overwrites the historical slot. With the gate enabled, Morris gets an explicit 409 with a clear error message explaining that the story_id has a prior terminal history and must either use a fresh story_id or set `rework_of` to acknowledge intentional lineage.

**Operators (Mark):** Enable the flag once confirmed clean, knowing that no new hijacks can occur without an explicit `rework_of` signal.

## Success Criteria

- [ ] `DISPATCH_V2_ID_REUSE_GATE` env var (default `"false"`) controls the gate
- [ ] Gate can be overridden via `app.state.dispatch_v2_id_reuse_gate` for tests
- [ ] When gate=off: existing behavior unchanged — terminal rows ignored
- [ ] When gate=on + no `rework_of`: POST `/enqueue` with prior terminal `(repo, story_id)` → HTTP 409 with `{detail, story_id, repo, prior_terminal_states, fix}`
- [ ] When gate=on + `rework_of` set: allow the insert (intentional rework lineage)
- [ ] Active row idempotency is unchanged: gate never runs when idempotency check returns an active row
- [ ] All 6 test cases in `tests/ops_console/test_dispatch_v2_enqueue_id_reuse_gate.py` pass GREEN
- [ ] No regressions in full `tests/ops_console/` suite

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal |
| Timeline | 1 day |
| Scale | Single route handler modification |

## Security Constraints (Non-Negotiable)

- [ ] Gate is OFF by default — no behavior change until explicitly enabled
- [ ] Gate never modifies data — read-only query before any INSERT
- [ ] `rework_of` acceptance is explicit — a field must be set, not inferred
- [ ] No changes to `dispatch_v2_service.py` — route file only

## Operational Lifecycle

- Enable the gate by setting `DISPATCH_V2_ID_REUSE_GATE=true` in the ops-console environment.
- To intentionally reuse a terminal slot, set `rework_of=<original_story_id>` in the enqueue payload.
- Gate can be toggled at runtime without a restart (env var read per-request via app.state or os.environ fallback).

## Codebase Context (Feature Updates Only)
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/routes/dispatch_v2.py` |
| Related components | `/api/dispatch/v2/enqueue` handler, `EnqueueRequest` model |
| Current behavior | `/enqueue` silently reuses `(story_id, repo)` after terminal state |
| Desired change | 409 gate when prior terminal rows exist and `rework_of` is not set |
| Architecture constraints | Must not modify `dispatch_v2_service.py` |

## Test Criteria

All tests in `tests/ops_console/test_dispatch_v2_enqueue_id_reuse_gate.py`:

1. `test_gate_off_allows_reuse_after_terminal` — flag=false, prior cancelled row, POST → 200 (existing behavior)
2. `test_gate_on_rejects_reuse_after_cancelled` — flag=true, prior cancelled row, POST without rework_of → 409 with `{story_id, repo, prior_terminal_states, fix}`
3. `test_gate_on_rejects_reuse_after_completed` — same with completed prior state → 409
4. `test_gate_on_allows_reuse_with_rework_of` — flag=true, prior cancelled row, POST with `rework_of=STORY-XXX` → 200
5. `test_gate_on_allows_first_use` — flag=true, no prior row → 200 (unchanged)
6. `test_gate_on_idempotency_unchanged_for_active_row` — flag=true, prior ACTIVE row → 200 (idempotency not 409)

## Validation

- After deploy, set `DISPATCH_V2_ID_REUSE_GATE=true` in production environment.
- Attempt to re-enqueue a completed STORY-XXX without `rework_of` — confirm 409.
- Attempt same with `rework_of=STORY-XXX` — confirm 200.
- Verify no new orphaned rows appear in the next Morris autofix cycle.
