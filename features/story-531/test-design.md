# STORY-531: Test Design — Composite `(story_id, repo)` Unique Key

## Summary

18 integration tests in `tests/ops_console/test_dispatch_composite_key.py` covering the composite-key schema change, service disambiguation, route `?repo=` param, and history preservation.

All tests require a PostgreSQL `ops_console_test` database with migrations 001–007 applied. Tests skip automatically when PostgreSQL is not reachable (standard pattern for this repo).

**RED state:** 16/18 tests FAIL without Phase 8 implementation. 2/18 (T02, T07) cover existing behaviour that is preserved unchanged.

---

## Test File

`tests/ops_console/test_dispatch_composite_key.py`

### Infrastructure

```
TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"
REPO_ALPHA = "repo-alpha"
REPO_BETA  = "repo-beta"
STORY      = "STORY-531-TEST"
```

- `_pg_is_reachable()` — probes the test DB; `pytestmark = skipif(not _PG_AVAILABLE)`
- `db_pool` — `asyncpg.create_pool` + `TRUNCATE dispatch_items, agents` between tests
- `svc` — `DispatchDBService(db_pool)`
- `_enqueue_kw()` — helper for building `enqueue()` kwargs
- `AmbiguousStoryError` import is wrapped in `try/except ImportError` so the file collects even before Phase 8; tests that require it assert `_AMBIGUOUS_IMPLEMENTED` first

---

## Test Groups and Cases

### Group A — Cross-repo enqueue (T01–T02)

**T01: `test_cross_repo_both_enqueue_succeed`**
- Setup: call `enqueue(STORY, repo=REPO_ALPHA)` then `enqueue(STORY, repo=REPO_BETA)`
- Assert: both return 200, both rows have `status="pending"`, both have distinct `id` values
- RED reason: old `uq_story_active_idx (story_id)` raises `DuplicateDispatchError` on 2nd enqueue
- AC coverage: AC2

**T02: `test_same_repo_duplicate_active_raises`**
- Setup: `enqueue(STORY, repo=REPO_ALPHA)` × 2
- Assert: second raises `DuplicateDispatchError`
- GREEN with current code (existing behaviour preserved)
- AC coverage: AC3

---

### Group B — Terminal-state isolation (T03–T04)

**T03: `test_reenqueue_does_not_delete_terminal_in_other_repo`**
- Setup: enqueue+claim+complete STORY in `REPO_BETA`; then `enqueue(STORY, repo=REPO_ALPHA)`
- Assert: the `REPO_BETA` completed row still exists with `status="completed"` (raw SQL check)
- RED reason: `enqueue()` line 116 `DELETE WHERE story_id = $1` removes the beta row
- AC coverage: AC5, AC8

**T04: `test_complete_one_repo_does_not_affect_other_repo`**
- Setup: enqueue+claim STORY in both repos; `complete(STORY, repo=REPO_ALPHA)`
- Assert: `REPO_BETA` row still has `status="claimed"`
- RED reason: requires cross-repo active rows (impossible under old single-column index)
- AC coverage: AC4

---

### Group C — `complete()` disambiguation (T05–T07)

**T05: `test_complete_with_repo_scopes_to_correct_row`**
- Setup: enqueue+claim STORY in both repos; `complete(STORY, repo=REPO_ALPHA)`
- Assert: returned row has `repo=REPO_ALPHA`, `status="completed"`; DB check confirms `REPO_BETA` still `claimed`
- RED reason: `repo=` kwarg not accepted on `complete()` + requires cross-repo rows
- AC coverage: AC6

**T06: `test_complete_raises_ambiguous_without_repo_two_active_rows`**
- Setup: enqueue+claim STORY in both repos; `complete(STORY)` (no repo)
- Assert: `AmbiguousStoryError` raised; `exc.story_id == STORY`; `set(exc.candidate_repos) == {REPO_ALPHA, REPO_BETA}`
- RED reason: `AmbiguousStoryError` not implemented; cross-repo rows not possible under old index
- AC coverage: AC6

**T07: `test_complete_backward_compat_single_row_no_repo`**
- Setup: enqueue+claim STORY in `REPO_ALPHA` only; `complete(STORY)` (no repo)
- Assert: row returned with `status="completed"`, `repo=REPO_ALPHA`
- GREEN with current code (single-row backward-compat path unchanged)
- AC coverage: AC6

---

### Group D — Other mutating methods (T08–T11)

All four tests follow the same structure:
1. Enqueue STORY in both repos; claim where needed
2. Call the method with `repo=REPO_ALPHA` qualifier
3. Assert the ALPHA row changed to the expected status
4. Assert the BETA row is unchanged (raw SQL check)

| Test | Method | Transition | BETA expected |
|------|--------|------------|---------------|
| T08 | `claim(STORY, "dan", repo=REPO_ALPHA)` | pending→claimed | pending |
| T09 | `fail(STORY, repo=REPO_ALPHA)` | claimed→failed | claimed |
| T10 | `cancel(STORY, repo=REPO_ALPHA)` | pending→cancelled | pending |
| T11 | `pause(STORY, "dan", repo=REPO_ALPHA)` | claimed→paused | claimed |

RED reason for all: `repo=` kwarg not accepted on any of these methods; cross-repo rows impossible.
AC coverage: AC6

---

### Group E — `get()` scoping (T12)

**T12: `test_get_with_nonexistent_repo_returns_none`**
- Setup: enqueue STORY in `REPO_ALPHA`; call `get(STORY, repo=REPO_BETA)`
- Assert: returns `None`
- RED reason: `get()` doesn't accept `repo=` kwarg (TypeError) + the current `get()` would return the alpha row ignoring repo
- AC coverage: AC6

---

### Group F — History preservation (T13)

**T13: `test_history_preserved_across_reenqueue_same_repo`**
- Setup: enqueue+claim+complete STORY in `REPO_ALPHA`; capture `first_id`; re-enqueue STORY in `REPO_ALPHA`
- Assert: raw SQL query for `id = first_id` returns 1 row with `status="completed"` (not deleted); new row is distinct pending entry
- RED reason: `enqueue()` deletes by `story_id` alone; first row is wiped before re-insert
- AC coverage: AC5, AC8

---

### Group G — Route-level tests (T14–T15)

**T14: `test_route_claim_accepts_repo_query_param`**
- Setup: inject PG-backed `DispatchDBService` into `app.state`; enqueue STORY in both repos
- Call: `POST /api/dispatch/claim/STORY?repo=repo-alpha` with `{"agent_name": "dan"}`
- Assert: HTTP 200; `data["item"]["repo"] == REPO_ALPHA`; DB check confirms `REPO_BETA` still pending
- RED reason: route has no `?repo=` param; also cross-repo rows not possible until migration 007
- AC coverage: AC7

**T15: `test_route_complete_returns_409_on_ambiguous`**
- Setup: inject PG-backed service; enqueue+claim STORY in both repos; `settings.github_token = ""`
- Call: `POST /api/dispatch/complete/STORY` with `{"commit_sha": "a"*40}` (no `?repo=`)
- Assert: HTTP 409; body has `candidate_repos` list containing both repos
- RED reason: `AmbiguousStoryError` not implemented; route has no ambiguity handler; cross-repo rows not possible
- AC coverage: AC7

---

### Group H — Schema validation (T16–T18)

**T16: `test_composite_active_index_exists`**
- Query: `SELECT COUNT(*) FROM pg_indexes WHERE tablename='dispatch_items' AND indexname='uq_story_repo_active_idx'`
- Assert: count == 1
- RED reason: migration 007 not yet applied
- AC coverage: AC1

**T17: `test_old_single_column_index_dropped`**
- Query: `SELECT COUNT(*) FROM pg_indexes WHERE tablename='dispatch_items' AND indexname='uq_story_active_idx'`
- Assert: count == 0
- RED reason: old index still present until migration 007 runs
- AC coverage: AC1

**T18: `test_in_review_status_covered_by_composite_index`**
- Setup: enqueue+claim+`transition_to_review(STORY)` in `REPO_ALPHA`; then direct INSERT of a second `(STORY, REPO_ALPHA, status='in_review')` row
- Assert: `asyncpg.UniqueViolationError` raised by the direct INSERT
- RED reason: old index doesn't cover `in_review`; duplicate in_review rows were silently allowed
- AC coverage: AC1

---

## Coverage Matrix

| AC from seed.md | Tests |
|-----------------|-------|
| AC1: schema migration + reversible down | T16, T17, T18 |
| AC2: cross-repo both succeed | T01 |
| AC3: same-repo active duplicate → 409 | T02 |
| AC4: terminal isolation | T04 |
| AC5: history preserved | T03, T13 |
| AC6: service optional repo (backward-compat) | T05–T12 |
| AC7: route `?repo=` | T14, T15 |
| AC8: enqueue DELETE scoped to (story_id, repo) | T03, T13 |
| AC9: new test file | This file |
| AC10: migration reversibility | Documented in migration SQL; not automated |
| AC11: deployment validation | Manual smoke test in feature-spec.md §6 |
| AC12: docstring updates | Verified during code review |

---

## RED State Declaration

| Test | Expected state pre-Phase-8 | Reason |
|------|---------------------------|--------|
| T01 | FAIL (DuplicateDispatchError) | Old single-column index blocks 2nd enqueue |
| T02 | PASS | Existing behaviour unchanged |
| T03 | FAIL (AssertionError — beta row deleted) | enqueue() DELETE is by story_id alone |
| T04 | FAIL (DuplicateDispatchError in setup) | Can't create cross-repo active rows under old index |
| T05 | FAIL (TypeError or DuplicateDispatchError) | repo= kwarg not accepted; setup fails |
| T06 | FAIL (AssertionError — `_AMBIGUOUS_IMPLEMENTED` is False) | AmbiguousStoryError not yet defined |
| T07 | PASS | Existing behaviour unchanged |
| T08 | FAIL (TypeError or setup failure) | repo= kwarg not accepted on claim() |
| T09 | FAIL | repo= kwarg not accepted on fail() |
| T10 | FAIL | repo= kwarg not accepted on cancel() |
| T11 | FAIL | repo= kwarg not accepted on pause() |
| T12 | FAIL (TypeError) | repo= kwarg not accepted on get() |
| T13 | FAIL (AssertionError — history row deleted) | enqueue() DELETE is by story_id alone |
| T14 | FAIL (setup fails or wrong row claimed) | Route has no ?repo= param; cross-repo rows impossible |
| T15 | FAIL (AssertionError — `_AMBIGUOUS_IMPLEMENTED` is False) | AmbiguousStoryError not yet defined |
| T16 | FAIL (count == 0) | Migration 007 not applied |
| T17 | FAIL (count == 1) | Old index still present |
| T18 | FAIL (no UniqueViolationError) | in_review not in old index |

**16 FAIL / 2 PASS** pre-implementation. Target: **18 PASS** after Phase 8.

---

## Prerequisite Checklist for Phase 8

Before implementation begins:
- [ ] PostgreSQL `ops_console_test` accessible at `postgresql://ops_console:ops_console@localhost/ops_console_test`
- [ ] Migrations 001–006 applied to test DB
- [ ] Migration 007 NOT yet applied (to confirm RED state first)
- [ ] Confirm `python3 -m pytest tests/ops_console/test_dispatch_composite_key.py -v` shows 16 FAIL / 2 PASS (PG) or 18 SKIP (no PG)
