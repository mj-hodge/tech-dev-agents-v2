# Q1 Decisions Log

**Story:** Epic-Queue-v2 / Q1 — Schema + Event Log as Source of Truth
**Branch:** epic-queue-v2/q1-schema
**Date:** 2026-05-02

---

## D01 — Migration idempotency

**Decision:** Use `CREATE TABLE IF NOT EXISTS` and `CREATE OR REPLACE FUNCTION`
throughout migration 050.

**Rationale:** Allows the migration to be re-applied safely in test fixtures
without failing on duplicate objects. Also protects against accidental
double-run in production (belt-and-suspenders alongside the migration tracking
table that will be added in a later story). The `DO $$ ... END $$` pattern is
used for triggers since `CREATE TRIGGER IF NOT EXISTS` is not supported in
PostgreSQL before 14.

---

## D02 — heartbeat does not upsert dispatch_state_current

**Decision:** The `dispatch_state_apply()` trigger returns `NEW` immediately
on `heartbeat` without touching `dispatch_state_current`.

**Rationale:** architecture.md § 4 explicitly marks heartbeat as "(unchanged)".
Updating the projection on heartbeat would thrash `updated_at` on
`dispatch_state_current` and would make it impossible to distinguish "state
changed" from "agent is alive" in audit queries. The service layer updates
`dispatch_leases.heartbeat_at` directly (outside the trigger path).

---

## D03 — Service accepts bare Connection or Pool

**Decision:** `DispatchV2Service.__init__` accepts either an `asyncpg.Pool`
or a bare `asyncpg.Connection`. A thin `_BareConnContext` wrapper normalises
both to the same `async with self._acquire() as conn:` pattern.

**Rationale:** Tests that manage their own connection (for TRUNCATE-based
isolation without locking) can pass a bare connection. Production code passes
a pool. Avoids duplicating the service or creating a separate test-only
subclass.

---

## D04 — failed lane defaults to attention_queue in Q1

**Decision:** The `dispatch_state_apply()` trigger maps `failed` events to
`lane = 'attention_queue'` unconditionally in Q1.

**Rationale:** The Q3 story will add `dispatch_failure_policy` table which
routes `failure_class` → `next_lane` (work_queue for retryable, attention_queue
for manual, quarantined for loop-detected, dead_letter for exhausted). In Q1
there is no policy table, so all failures go to attention_queue (human review)
as the safe default.

---

## D05 — replay_state Python mirror of trigger

**Decision:** `DispatchV2Service.replay_state()` reimplements the same
event→state mapping as `dispatch_state_apply()` in Python.

**Rationale:** The invariant `replay_state(job_id) == dispatch_state_current.state`
is tested by AC4 (1000-job seed). Having both implementations is a deliberate
redundancy; any divergence between them is a bug. The Python layer is also
used by the daily audit job (Q4+) to detect projection drift without a
dedicated DB function.

---

## Gate A — Q1 Migration Replay Gate

**Criterion:** Migration 050 applies cleanly from a migrations-001–014
baseline, all 7 AC tests pass GREEN, and `replay_state` matches projection
for 100% of jobs in a 1000-job seed.

**Evidence:**

```
62/62 tests PASSED
tests/test_epic_queue_v2_q1.py::TestMigration050Exists (6 tests)           PASSED
tests/test_epic_queue_v2_q1.py::TestFailedEventRequiresFailureClass (4)    PASSED
tests/test_epic_queue_v2_q1.py::TestLeasedEventUpdatesProjection (2)       PASSED
tests/test_epic_queue_v2_q1.py::TestReplayStateMatchesProjection (1)       PASSED
  - 1000 jobs, 0 divergences
tests/test_epic_queue_v2_q1.py::TestV1TablesUntouched (3)                  PASSED
tests/test_epic_queue_v2_q1.py::TestCanonicalStateValues (15)              PASSED
  - All 12 state-changing event_types produce correct (state, lane) pairs
  - needs_info.kind=question → human_queue, needs_info.kind=attention → attention_queue
  - heartbeat does not change state/lane
tests/test_epic_queue_v2_q1.py::TestEventTypeCheckConstraint (16)          PASSED
  - 2 invalid types rejected with CheckViolationError
  - All 14 valid types accepted
tests/test_epic_queue_v2_q1.py::TestDispatchV2ModelImports (8)             PASSED
tests/test_epic_queue_v2_q1.py::TestDispatchV2ServiceImports (7)           PASSED

Total: 62 passed in 18.67s
```

**Decision: PASS**

Q2 can proceed. The schema foundation is stable, the trigger is correct for
all event types, and the replay invariant holds at scale.
