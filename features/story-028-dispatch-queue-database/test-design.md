# Phase 7: Test Design — Dispatch Queue Database Persistence & History

**Story:** STORY-028
**Phase:** 7 (Test Design)
**Date:** 2026-04-12
**State:** RED (all tests fail — module not yet implemented)

---

## Test File

`tests/ops_console/test_dispatch_db_service.py` — 21 tests covering all 15 acceptance criteria.

## Test Matrix

| ID | Test | Class | AC |
|----|------|-------|----|
| T01 | enqueue inserts row with status=pending | TestEnqueue | AC-1 |
| T02 | duplicate active story raises DuplicateDispatchError | TestEnqueue | AC-9 |
| T03 | re-enqueue after completion succeeds (partial index) | TestEnqueue | AC-9 |
| T04 | list_queue returns active only (not terminal) | TestListQueue | AC-2 |
| T05a | next_pending returns oldest (FIFO) | TestNextPending | AC-3 |
| T05b | next_pending empty returns None | TestNextPending | AC-3 |
| T06 | claim transitions pending→claimed | TestClaim | AC-4 |
| T07 | claim non-pending raises error | TestClaim | AC-4 |
| T07b | claim not-found raises NotFoundError | TestClaim | AC-4 |
| T08 | cancel transitions pending→cancelled | TestCancel | AC-5 |
| T09 | cancel claimed raises AlreadyClaimedError | TestCancel | AC-5 |
| T10 | complete transitions claimed→completed | TestComplete | AC-6 |
| T11 | complete non-claimed raises InvalidTransitionError | TestComplete | AC-6 |
| T12 | history returns terminal items, paginated | TestHistory | AC-7 |
| T13 | history with status_filter | TestHistory | AC-7 |
| T14 | recover_stale_claims moves old claims to pending | TestRecoverStaleClaims | AC-8 |
| T15 | recover_stale_claims leaves fresh claims | TestRecoverStaleClaims | AC-8 |
| T16 | register new agent, is_new=True | TestRegisterAgent | AC-10 |
| T17 | register existing agent updates last_seen | TestRegisterAgent | AC-10 |
| T18 | pending_count returns correct count | TestPendingCount | AC-2 |
| T30 | migration runs twice without error | TestMigrationIdempotent | AC-13 |

## AC Coverage

| AC | Tests | Status |
|----|-------|--------|
| AC-1 | T01 | RED |
| AC-2 | T04, T18 | RED |
| AC-3 | T05a, T05b | RED |
| AC-4 | T06, T07, T07b | RED |
| AC-5 | T08, T09 | RED |
| AC-6 | T10, T11 | RED |
| AC-7 | T12, T13 | RED |
| AC-8 | T14, T15 | RED |
| AC-9 | T02, T03 | RED |
| AC-10 | T16, T17 | RED |
| AC-13 | T30 | RED |

## Notes

- Tests use real PostgreSQL (`ops_console_test` database) — no mocking
- Table truncation between tests for isolation
- AC-11 (Teams auto-register) tested at route level, not service level
- AC-12 (pool lifecycle) verified by test fixture setup/teardown
- AC-14 (backward compat) verified by existing STORY-026 route tests
- AC-15 (frontend) requires manual verification
