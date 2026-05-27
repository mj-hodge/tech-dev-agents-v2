# STORY-100 — Queue Smoke Tests

## 1. Overview

| Field       | Value                                                        |
| ----------- | ------------------------------------------------------------ |
| Story ID    | STORY-100                                                    |
| Title       | Dispatch Queue Smoke Tests                                   |
| Mode        | test                                                         |
| Scope       | small                                                        |
| Frontend    | false                                                        |
| Phase Path  | 1 → 7 → 8 → Done                                            |
| Priority    | 80 (HIGH — smoke tests are the pre-deploy gate safety net)   |
| Branch      | `story-100/story-100`                                        |
| Repo        | tech-dev-agents                                              |
| Related Files | `tech_dev_agents/ops_console/routes/dispatch.py`, `tech_dev_agents/ops_console/services/dispatch_db_service.py`, `tests/ops_console/test_routes_dispatch.py`, `tests/predeploy/check_smoke_tests.sh` |

---

## 2. Problem Statement

The dispatch queue is the critical path for all automated story processing. The pre-deploy gate runs `pytest -m smoke` to verify system health before deployments. Currently there are **no `@pytest.mark.smoke`-tagged tests** covering the dispatch queue API. This means:

1. A broken queue endpoint can pass the pre-deploy gate undetected.
2. The smoke-test runner (`check_smoke_tests.sh`) falls back to running the full test suite when no smoke tests are collected (exit code 5), masking the gap.
3. Existing dispatch queue tests in `test_routes_dispatch.py` require a live PostgreSQL connection and are skipped in most CI/pre-deploy contexts — they cannot serve as lightweight smoke tests.

## 3. Solution

Add a lightweight smoke test module (`tests/ops_console/test_dispatch_queue_smoke.py`) that:

1. Exercises the core dispatch queue lifecycle — enqueue, list, claim, complete — against the FastAPI test client with a **mocked** `DispatchDBService` (no PostgreSQL dependency).
2. Is tagged `@pytest.mark.smoke` so `pytest -m smoke` collects it.
3. Verifies FIFO ordering, status transitions, and error responses for the most critical endpoints.
4. Runs in < 2 seconds with zero external dependencies.

## 4. Acceptance Criteria

| ID   | Criterion                                                                 | Measurable                                                                                    |
| ---- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| AC-1 | Smoke test module exists at `tests/ops_console/test_dispatch_queue_smoke.py` | File exists on disk                                                                           |
| AC-2 | All tests are tagged `@pytest.mark.smoke`                                 | `pytest -m smoke --collect-only` shows the new tests                                          |
| AC-3 | Enqueue → list → claim → complete lifecycle passes                        | Single test exercises full happy-path lifecycle with mocked DB service                         |
| AC-4 | FIFO ordering verified                                                    | Enqueue A then B; list returns A before B                                                     |
| AC-5 | Duplicate enqueue returns 409                                             | POST same story_id+repo twice → 409 Conflict                                                  |
| AC-6 | Claim non-existent story returns 404                                      | POST claim with unknown story_id → 404                                                        |
| AC-7 | Queue metrics endpoint responds 200                                       | GET `/api/dispatch/metrics` → 200 with expected shape                                         |
| AC-8 | Tests run without PostgreSQL                                              | All tests pass with `DispatchDBService` mocked — no `asyncpg` connection needed               |
| AC-9 | `pytest -m smoke` exit code is 0                                          | Pre-deploy gate `check_smoke_tests.sh` no longer falls back to full suite                     |

## 5. Out of Scope

- E2E / Playwright queue tests (covered by `dispatch-queue-needs-info.spec.ts`).
- Work queue (local file-based `scripts/work_queue.py`) smoke tests — separate story.
- Load/stress testing of the dispatch queue.
- Changes to the dispatch queue implementation itself.

## 6. Technical Notes

- Use `httpx.AsyncClient` with FastAPI's `TestClient` pattern (matching existing `test_routes_dispatch.py` style).
- Mock `DispatchDBService` methods to return canned responses — no DB pool setup.
- Follow existing `conftest.py` patterns in `tests/ops_console/` for auth injection.

## Test Criteria

- 8 smoke tests collected by `pytest -m smoke` — one per acceptance criterion AC-1 through AC-8 plus the full lifecycle test.
- All tests use a mocked `DispatchDBService` (no PostgreSQL, no asyncpg connection).
- Each test is deterministic and completes in < 2 seconds total suite time.
- `pytest -m smoke --collect-only` lists exactly 8 items from `tests/ops_console/test_dispatch_queue_smoke.py`.
- `pytest -m smoke` exits 0 with 8 passed, 0 failed.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/ops_console/test_dispatch_queue_smoke.py -v` | 8/8 tests GREEN |
| 2 | `pytest -m smoke --collect-only` | Collects 8 tests from the smoke module |
| 3 | `pytest -m smoke` | Exit code 0, 8 passed |
| 4 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 5 | After deploy: `bash tests/predeploy/check_smoke_tests.sh` passes without falling back to full suite | Exit code 0 |

## 7. Version

seed-version: 1.0.0
