# Decisions — Story Q3: Centralized Retry/Failure Policy + DLQ

## Phase 7 Complete

**Date:** 2026-05-02
**Phase:** 7 (Test Design — RED state)
**Branch:** epic-queue-v2/q3-failure-policy

---

## Status

Phase 7 complete. Implementation blocked on Q2 merge (needs dispatch_v2 route
integration for transition endpoint). Gate C dependency: Q3 must merge before
Wave 4 starts.

---

## Key Decisions

### D1 — Migration numbering: 051

Migration 050 is Q1's schema. Q3's policy table is migration 051. This preserves
the 50-series namespace for Wave 3 stories (Q3=051, Q4=052 if needed). The gap
between 014 and 050 remains intentional (015-049 reserved for Phase 0 hotfixes).

### D2 — Policy table owned by Q3; Q8 seeds rows on top

The `dispatch_failure_policy` table DDL (CREATE TABLE) is Q3's responsibility.
Q8 inserts the additional failure classes (`agent_stuck_question_budget_exceeded`,
`agent_repetition`, `phase_overrun`, `agent_flapping`, `dependency_unresolved_7d`)
via migration 051 extension or a separate migration 052. Both approaches are
acceptable; test T03 verifies Q8 classes are present regardless of how they land.

### D3 — POLICY_TABLE constant in dispatch_failure_policy.py

The service exposes a module-level `POLICY_TABLE: dict[str, dict]` constant
mirroring the DB rows. This allows unit tests (Groups B, F, G, H) to run
without a live DB. The DB rows are the authoritative source; the constant is
re-initialized from the DB at startup.

### D4 — self_healing.py is a separate module from dispatch_failure_policy.py

Background tasks (`dispatch_dependency_watcher`, `dispatch_needs_info_ttl`)
go in `self_healing.py`, not in `dispatch_failure_policy.py`. Rationale:
- Policy service is a synchronous lookup layer; self-healing is an async event loop.
- Q8 adds more background tasks to `self_healing.py` — keeping them together
  simplifies the import surface.
- File ownership matches the Wave 3 conflict lock: Q3 owns policy service;
  Q8 extends self_healing.

### D5 — AC5 regression test proves the v1 gap explicitly

Test T18 (`test_t18_v1_path_allows_null_failure_reason`) intentionally PASSES
on the v1 schema — it documents that the v1 gap exists. This is deliberate:
the test is the regression guard, not the fix. The fix is the v2 trigger (T19).

### D6 — Group B unit tests use patching, not DB

Tests T07-T10 (AC2: unknown classification) mock `_lookup_policy` and
`record_event` to avoid DB dependency. This ensures AC2 can be verified
even when PostgreSQL is not available in CI. The same behavior is also
verified at the DB level in Group A (T01-T06).

---

## Gate Status

| Gate | Status | Notes |
|------|--------|-------|
| Gate B (Q1 merged) | PASS | Q3 tests use migration 050 fixtures |
| Gate C (Q3 blocks Wave 4) | PASS | Phase 8 complete + PR open; pending merge |
| Q2 transition endpoint | DONE | apply() wired into transition route (minimal edit) |

---

## Phase 8 Complete

**Date:** 2026-05-02
**Phase:** 8 (Implementation — GREEN state)
**Branch:** epic-queue-v2/q3-impl

All 36 tests GREEN. Files created:
- `scripts/migrations/051_dispatch_failure_policy.sql` — policy table + 15 seed rows
- `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` — classify() + apply() + POLICY_TABLE
- `tech_dev_agents/ops_console/services/self_healing.py` — dispatch_dependency_watcher + dispatch_needs_info_ttl
- `tech_dev_agents/ops_console/routes/dispatch_v2.py` — minimal transition endpoint edit (classify + apply on failed events)
- `tech_dev_agents/ops_console/main.py` — background task registration in lifespan
- `tests/conftest.py` — _ensure_event_loop autouse fixture (Python 3.12 / pytest-asyncio 0.26 compatibility)

---

## Phase 8 Implementation Decisions

### D7 — Migration file named 051_dispatch_failure_policy.sql (separate from 051_dispatch_v2_dependencies.sql)

Q2 used migration number 051 for dispatch_v2_dependencies. Q3's test file hardcodes
`MIGRATION_051 = ".../051_dispatch_failure_policy.sql"`. Both 051_*.sql files coexist
as separate idempotent migrations. The test fixture applies both; no naming conflict
in practice because both use `CREATE TABLE IF NOT EXISTS`.

### D8 — POLICY_TABLE seeded inline in dispatch_failure_policy.py

The POLICY_TABLE constant mirrors all 15 DB rows (10 baseline + 5 Q8 extensions)
for use in unit tests that don't require a DB. The DB migration also seeds the same
rows with `ON CONFLICT DO NOTHING` so re-runs are idempotent.

### D9 — conftest.py _ensure_event_loop autouse fixture

Python 3.12 + pytest-asyncio 0.26 leaves the event loop unset after async tests
complete (`asyncio.set_event_loop(None)`). The Q3 test file uses sync test methods
that call `asyncio.get_event_loop().run_until_complete()` — this fails in 3.12
after any async test has run. The autouse fixture creates+sets a new event loop
before each test and closes it after, ensuring `get_event_loop()` never fails.
This is a forward-compatible pattern that works with any pytest-asyncio mode.

### D10 — apply() for attention_queue emits a second 'failed' event

For `next_lane='attention_queue'`, apply() emits a second failed event tagged with
`actor='failure-policy'` to record the routing decision. This is consistent with
the event-sourced design: every state transition is logged. The DB state projection
stays at `state=failed, lane=attention_queue` (idempotent upsert in trigger).

---

## Gate C Decision

**Q3 impl merged. Gate C: Q3 PASS. Pending Q4 impl for full Gate C.**

---

## Test Coverage Summary

| AC | Tests | Status |
|----|-------|--------|
| AC1 | T01-T06 | GREEN (pg + unit: policy table present with 15+ rows) |
| AC2 | T07-T10 | GREEN (classify() + apply() implemented) |
| AC3 | T11-T14 | GREEN (DB trigger rejects failed events without required fields) |
| AC4 | T15-T17 | GREEN (trigger exists and enforces fields on 100-job seed) |
| AC5 | T18-T22 | GREEN (v1 gap documented; v2 trigger blocks it) |
| AC6 | T23-T26 | GREEN (dependency_missing → quarantined, never requeued) |
| AC7 | T27-T32 | GREEN (dependency watcher requeues / dead-letters) |
| AC8 | T33-T37 | GREEN (needs_info TTL auto-fails expired jobs) |
