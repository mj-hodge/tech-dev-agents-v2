# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Criticality | important |
| Feature Name | in-review-pr-link-db-invariant |
| Frontend | false |

## Problem Statement

Jobs can land in `dispatch_state_current.state='in_review'` with `dispatch_jobs.pr_number IS NULL`. Once unlinked, the PR-merge sweeper cannot transition them to `completed`, so they sit forever until the new `dispatch_stale_unlinked_in_review_sweeper` requeues them after 30 minutes. Two service-layer fixes already exist:

1. STORY-902 backfill sweeper — best-effort linking via GitHub PR search
2. Working-tree guard (2026-05-12) in `dispatch_v2_service.transition()` — rejects `submitted → in_review` without `pr_number`

Both live in Python. A future caller bypassing the service (direct SQL, ad-hoc migration, new code path) can still create violators. **The invariant belongs in the data layer.**

## Target User / Use Case

**Ops automation correctness:** The queue must never reach a state where a known-bad-shape row exists. The DB enforces the invariant so all callers, present and future, get a clear failure instead of silently leaking work.

## Success Criteria

- [ ] New migration `scripts/migrations/057_in_review_requires_pr_link.sql`
- [ ] Migration is **two-phase**:
  - **Phase 1 (data cleanup):** any pre-existing rows with `state='in_review' AND pr_number IS NULL` get a `requeued` event emitted via `dispatch_v2_events` INSERT (using `actor='migration_057'`, `event_data={"reason":"cleanup_for_invariant","source":"migration_057"}`). The DB trigger that maintains `dispatch_state_current` from the event log handles the state flip — do not write to `dispatch_state_current` directly.
  - **Phase 2 (invariant):** Create a `BEFORE INSERT OR UPDATE` trigger on `dispatch_state_current` that joins `dispatch_jobs` and raises `EXCEPTION 'in_review state requires dispatch_jobs.pr_number to be set (job_id=%)'` when `NEW.state='in_review' AND dispatch_jobs.pr_number IS NULL`.
- [ ] Migration is idempotent — re-running it must be a no-op (use `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` before `CREATE TRIGGER`, conditional cleanup `WHERE NOT EXISTS (...)`).
- [ ] New tests in `tests/test_epic_queue_v2_q2.py` under a new class `TestInReviewPrLinkInvariant`:
  - Pre-existing violator gets requeued by the migration (insert a violator on a clean schema BEFORE applying 057, apply 057, assert state is now `pending` and a `requeued` event with `actor='migration_057'` exists).
  - Direct INSERT/UPDATE attempting to put a job in `in_review` without `pr_number` raises a clear error.
  - Legitimate `in_review` with `pr_number` set succeeds.
  - Idempotency: apply 057 twice, both succeed.
- [ ] Migration 057 is added to the test fixture in `test_epic_queue_v2_q2.py::raw_conn` alongside 050/051/056.
- [ ] All existing Q2 tests still pass with 057 applied.

## Constraints
| Constraint | Value |
|------------|-------|
| Language | SQL (Postgres 15+) + Python tests |
| Migration style | Idempotent, transactional (BEGIN/COMMIT) |
| State trigger | Must use existing event-driven state machine — do NOT mutate `dispatch_state_current` directly in cleanup |
| Test DB | Use the same `TEST_DATABASE_URL` pattern as 050/051/056 |
| Backward compat | Service-layer guard in `dispatch_v2_service.transition()` stays — DB trigger is defense-in-depth, not replacement |
| Mutation guard | Do NOT change `dispatch_v2_events` triggers or `dispatch_state_apply` — additive only |

## Security Constraints (Non-Negotiable)

- [ ] Trigger error message must NOT leak `pr_number` from other rows (it shouldn't need to — only references the offending job_id)
- [ ] Cleanup phase must not bulk-delete any rows — only emit events
- [ ] Migration must be reversible-by-design: dropping the trigger un-enforces the invariant; cleanup events are durable history

## Test Criteria

- [ ] T01 — clean schema, no violators: migration applies, trigger exists, no `requeued` events emitted
- [ ] T02 — violator present: migration emits exactly one `requeued` event per violator, state flips to `pending`
- [ ] T03 — multiple violators: each gets its own event, all flipped
- [ ] T04 — post-migration direct INSERT into `dispatch_state_current` with `state='in_review'` and underlying `pr_number IS NULL` → raises trigger exception
- [ ] T05 — post-migration UPDATE of `state` to `in_review` on a row whose `pr_number IS NULL` → raises trigger exception
- [ ] T06 — legitimate `in_review` transition (pr_number set first) → succeeds
- [ ] T07 — idempotency: apply 057 a second time → no error, no extra events emitted
- [ ] T08 — running existing Q2 tests with 057 in fixture → all pass

## Validation

- Apply 057 to a snapshot of prod DB (or a copy) and confirm:
  - Number of `requeued` events emitted equals the count of pre-existing violators reported by `SELECT count(*) FROM dispatch_state_current sc JOIN dispatch_jobs j USING (job_id) WHERE sc.state='in_review' AND j.pr_number IS NULL`
  - Query `SELECT count(*) FROM dispatch_state_current sc JOIN dispatch_jobs j USING (job_id) WHERE sc.state='in_review' AND j.pr_number IS NULL` returns 0 after migration
  - Trigger is listed in `pg_trigger` on `dispatch_state_current`

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `scripts/migrations/057_in_review_requires_pr_link.sql` (new), `tests/test_epic_queue_v2_q2.py` (new test class + fixture update) |
| Reference migrations | `050_dispatch_v2_schema.sql` (state trigger pattern), `051_dispatch_failure_policy.sql`, `056_dispatch_v2_correlation_index_fix.sql` |
| Reference service code | `tech_dev_agents/ops_console/services/dispatch_v2_service.py::transition` (the existing service-layer guard, mirror its check semantics in SQL) |
| Reference sweeper logic | `tech_dev_agents/ops_console/services/self_healing.py::_stale_unlinked_in_review_tick` (the cleanup pattern this migration runs once at apply time) |
| Pairs with | Working-tree guard merged 2026-05-12 (defense-in-depth — keep both) |
