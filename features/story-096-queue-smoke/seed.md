# STORY-096: Queue Smoke Tests

## Classification

| Field | Value |
|-------|-------|
| Story ID | STORY-096 |
| Scope | Medium |
| Phase Path | 1 → 6 → 7 → 8 → Done |
| Advance Category | auto |
| Category | Test |
| Frontend | false |

## Problem Statement

The dispatch queue system (local WorkQueue + central dispatch API + dispatch poller) lacks `@pytest.mark.smoke`-annotated tests. The pre-deploy gate (`08_smoke_dry_run.sh`) requires at least one smoke test to pass, and currently only `test_runtime_identity.py` carries the smoke marker. Queue subsystem failures (enqueue, claim, complete lifecycle) would not be caught by the smoke gate.

## Desired Outcome

Add a focused set of smoke tests covering the critical queue paths:

1. **Local WorkQueue** — enqueue/set_active/complete lifecycle round-trip
2. **Dispatch API** — POST enqueue, GET queue listing, POST claim, POST complete (via FastAPI TestClient)
3. **Stale detection** — verify stale entry detection fires for dead PIDs

All tests use `@pytest.mark.smoke` so they run during pre-deploy validation.

## Success Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| SC-1 | Local WorkQueue round-trip (enqueue → set_active → complete) passes as smoke test | `pytest -m smoke -k queue` collects and passes |
| SC-2 | Dispatch API lifecycle (enqueue → list → claim) passes as smoke test | `pytest -m smoke -k dispatch` collects and passes |
| SC-3 | Stale queue detection covered by smoke test | `pytest -m smoke -k stale` collects and passes |
| SC-4 | All new tests are marked `@pytest.mark.smoke` | grep confirms marker on every test |
| SC-5 | Pre-deploy smoke gate collects 4+ smoke tests (existing 1 + new 3+) | `pytest -m smoke --collect-only` shows >= 4 |

## Scope Boundaries

### In Scope
- Smoke test coverage for WorkQueue, dispatch API, stale detection
- All tests self-contained (no external DB, no network calls)
- Minimal `DispatchFallbackService` additions required to support the tests:
  - `has_active_claim(agent_name)` — agent active-claim guard
  - `list_queue()` returning both `in_progress` and `claimed` keys for route compatibility
  - `repo=` keyword argument on `claim()` for DB-service interface parity

### Out of Scope
- Dispatch poller integration tests (requires running subprocess + HTTP server)
- Performance or load tests
- POST `/dispatch/complete` smoke step (requires `commit_sha` proof-of-work via GitHub API — SC-2 ends at claim)

## Codebase Context

| Component | File |
|-----------|------|
| Local WorkQueue | `scripts/work_queue.py` |
| Dispatch API routes | `tech_dev_agents/ops_console/routes/dispatch.py` |
| Dispatch DB service | `tech_dev_agents/ops_console/services/dispatch_db_service.py` |
| Stale queue logic | `deployment/hermes/dispatch_poller.py` |
| Existing queue tests | `tests/test_work_queue.py` (14 tests, no smoke marker) |
| Existing dispatch tests | `tests/ops_console/test_routes_dispatch.py` |
| Existing stale tests | `tests/deployment/test_stale_work_queue.py` |
| Smoke marker config | `conftest.py` (registers `@pytest.mark.smoke`) |

## Dependencies

- None. All tests use in-memory fixtures or tmp_path.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Smoke tests slow down pre-deploy gate | Low | Tests use tmp files and mocks, < 1s total |
| False positives from flaky queue file I/O | Low | Use `tmp_path` fixture for isolation |

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Test granularity | One smoke test per subsystem (3 total) | Smoke tests should be fast and focused, not exhaustive |
| Test location | `tests/test_queue_smoke.py` | Co-located with existing test files |
| DB dependency | Mock dispatch DB service | Smoke tests must run without PostgreSQL |

## Test Criteria

- 3 smoke tests added in `tests/test_queue_smoke.py`, all carrying `@pytest.mark.smoke`
- Tests cover: WorkQueue lifecycle, dispatch API enqueue/claim, stale-PID detection
- Tests run in <1s total without PostgreSQL (mock-based)
- `pytest -m smoke tests/test_queue_smoke.py` exits 0
- Pre-deploy gate (`08_smoke_dry_run.sh`) passes with the new tests included

## Validation

- Phase 7 (test design): RED tests authored, all 3 fail as expected without implementation hooks
- Phase 8 (implementation): all 3 tests GREEN
- Pre-deploy gate run: smoke selection includes the new tests, suite passes
- Done when: PR merged, smoke marker config still registers `@pytest.mark.smoke`, no regressions in `tests/test_work_queue.py` or `tests/ops_console/test_routes_dispatch.py`
