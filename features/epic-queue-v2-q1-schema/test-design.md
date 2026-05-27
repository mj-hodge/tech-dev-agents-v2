# Q1 Test Design — Schema + Event Log as Source of Truth

**Phase:** 7 (Test Design)
**Story:** Epic-Queue-v2 / Q1
**Date:** 2026-05-02

## Test approach

All DB tests require a PostgreSQL `ops_console_test` database with migrations
001–014 already applied. Migration 050 is applied by the test fixture on each
run, then rolled back on teardown, so the tests are isolated.

Tests that can run without a live DB (pure Python unit tests) are marked
`@pytest.mark.unit`. DB tests are skipped when Postgres is unreachable via
the same `_pg_skip` pattern used in `test_507_lifecycle.py`.

Test file: `tests/test_epic_queue_v2_q1.py`

---

## AC1 — Tables + triggers exist after migration 050

**Test:** `TestMigration050Exists`

After applying `050_dispatch_v2_schema.sql`:
- `dispatch_jobs` table exists (pg_tables check)
- `dispatch_v2_events` table exists
- `dispatch_leases` table exists
- `dispatch_state_current` table exists
- `dispatch_state_apply_trg` trigger exists on `dispatch_v2_events`
- `dispatch_failed_required_trg` trigger exists on `dispatch_v2_events`

**Method:** Query `information_schema.tables` and `pg_trigger`.

---

## AC2 — `record_event('failed', {})` raises (failure_class required)

**Test:** `TestFailedEventRequiresFailureClass`

Call `DispatchV2Service.record_event(job_id, 'failed', {})` without
`failure_class` / `failure_reason` in `event_data`.

Expected: raises `asyncpg.exceptions.RaiseError` (or wrapped
`InvalidEventDataError`) with a message mentioning `failure_class`.

Also test:
- `record_event('failed', {'failure_class': 'agent_died'})` → raises (missing reason)
- `record_event('failed', {'failure_class': 'agent_died', 'failure_reason': 'oom'})` → succeeds

---

## AC3 — `record_event('leased', ...)` updates `dispatch_state_current` in same TX

**Test:** `TestLeasedEventUpdatesProjection`

1. `enqueue_job(...)` → inserts job + emits `enqueued` event
2. `record_event(job_id, 'leased', {'agent': 'dan', 'lease_token': '...', 'expires_at': '...'})`
3. Immediately `SELECT * FROM dispatch_state_current WHERE job_id = $1`
4. Assert `state = 'leased'`, `lane = 'in_progress'`, `leased_by = 'dan'`

This verifies the trigger runs within the INSERT transaction (synchronous trigger).

---

## AC4 — `replay_state` matches `dispatch_state_current` for 1000-job seed

**Test:** `TestReplayStateMatchesProjection`

1. Seed 1000 jobs, each with 1–5 random events drawn from the full event type list.
   (Filtered to valid sequences: no `failed` without fields, terminal states get no
   further events.)
2. For each job: `replay_state(job_id)` must return the same value as
   `dispatch_state_current.state`.
3. Assert 0 divergences across all 1000 jobs.

---

## AC5 — Existing `dispatch_items`/`dispatch_events` untouched

**Test:** `TestV1TablesUntouched`

1. Enqueue a v1 item via `DispatchDBService.enqueue(...)` before running the
   v2 service.
2. Apply migration 050 (in the fixture).
3. Assert `dispatch_items` row is still present and unmodified.
4. Assert no v2 tables have rows (v2 service has not been called).

This is a pure schema isolation test — no code in migration 050 references
`dispatch_items` or `dispatch_events` (v1 observability table).

---

## AC6 — Contract test on canonical state values

**Test:** `TestCanonicalStateValues`

Insert events of every possible `event_type` and assert that
`dispatch_state_current.state` is a member of the canonical set:

```
{'pending', 'leased', 'in_review', 'needs_info', 'completed',
 'failed', 'cancelled', 'dead_letter', 'quarantined'}
```

Also assert `dispatch_state_current.lane` is in:
```
{'work_queue', 'in_progress', 'in_review', 'human_queue',
 'attention_queue', 'terminal', 'quarantined'}
```

One sub-test per event type (14 tests total for full coverage).

---

## AC7 — SQL CHECK constraint on `event_type` enforced

**Test:** `TestEventTypeCheckConstraint`

Attempt a raw INSERT into `dispatch_v2_events` with `event_type = 'garbage'`.
Expected: `asyncpg.exceptions.CheckViolationError`.

Also test that all 14 valid values insert without error.

---

## Event → state/lane mapping (from architecture.md § 4)

Used as the oracle for AC4 and AC6:

| event_type     | state        | lane              | Notes                          |
|----------------|-------------|-------------------|--------------------------------|
| enqueued       | pending      | work_queue        | Default; quarantined if deps unmet |
| leased         | leased       | in_progress       |                                |
| heartbeat      | (unchanged)  | (unchanged)       | Not a state transition         |
| released       | pending      | work_queue        |                                |
| submitted      | in_review    | in_review         |                                |
| accepted       | completed    | terminal          |                                |
| rejected       | pending      | work_queue        |                                |
| needs_info     | needs_info   | human_queue or attention_queue | By event_data.kind |
| resumed        | leased       | in_progress       |                                |
| failed         | failed       | attention_queue   | Q1 default (Q3 adds policy)    |
| cancelled      | cancelled    | terminal          |                                |
| dead_lettered  | dead_letter  | terminal          |                                |
| quarantined    | quarantined  | quarantined       |                                |
| requeued       | pending      | work_queue        |                                |

---

## File structure

```
tests/
  test_epic_queue_v2_q1.py      ← all Q1 tests (RED state until Phase 8)
```

Tests are parametrized where multiple sub-cases exist (AC6, AC7) to keep
output readable and failures isolated.
