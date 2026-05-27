# STORY-532 Phase 7: Test Design

**Feature:** Dispatch `needs_info` state — human gate for ambiguous stories
**Scope:** Medium
**Phase:** 7 (Test Design)
**Date:** 2026-04-22
**Status:** RED — 9 FAIL (deployment), 17 SKIP (ops_console, PG unavailable in CI)

---

## Test Files

| File | Location | PG required? | Count |
|------|----------|-------------|-------|
| `test_needs_info_state.py` | `tests/ops_console/` | Yes (skip guard) | 17 tests |
| `test_phase_runner_needs_info.py` | `tests/deployment/` | No | 9 tests |

**Total: 26 tests — 0 pass, 9 fail, 17 skip in RED state.**

---

## Acceptance Criteria Mapping

| AC (from dispatch) | Test(s) |
|--------------------|---------|
| AC-1: `next_pending()` excludes `needs_info` | A-05, C-08 |
| AC-2: `list_queue()` returns needs_info bucket | A-06, C-07 |
| AC-3: POST `/resume` moves needs_info → pending | A-07, C-05 |
| AC-4: Phase runner POSTs `/needs-info` when QUESTION.md; no auto-retry | B-01, B-02 |
| AC-5: Agents polling `/next` do NOT claim needs_info stories | A-05, C-08 |
| AC-6: Dashboard shows needs_info rows (violet badge) | Frontend (e2e — deferred) |

---

## Test Group A — Service Layer (17 tests, PG-skip)

**File:** `tests/ops_console/test_needs_info_state.py`

### Group A — `DispatchDBService` methods

| ID | Test | Why RED |
|----|------|---------|
| A-01 | `needs_info()` transitions claimed → needs_info; stores path | Method does not exist |
| A-02 | `needs_info()` raises InvalidTransitionError on pending | Method does not exist |
| A-03 | `needs_info()` raises InvalidTransitionError on already-needs_info | Method does not exist |
| A-04 | `needs_info()` raises NotFoundError on unknown story | Method does not exist |
| A-05 | `next_pending()` returns None when only needs_info exists | Method does not exist / exclusion not in SQL |
| A-06 | `list_queue()` returns `'needs_info'` bucket with correct items | Bucket not returned by existing implementation |
| A-07 | `resume_from_needs_info()` transitions needs_info → pending; clears path | Method does not exist |
| A-08 | `resume_from_needs_info()` raises on non-needs_info story | Method does not exist |

### Group B — Unique index

| ID | Test | Why RED |
|----|------|---------|
| B-01 | `enqueue()` raises DuplicateDispatchError when story is needs_info | `needs_info` not in `uq_story_active_idx` (migration 007 not run) |

### Group C — Routes

| ID | Test | Why RED |
|----|------|---------|
| C-01 | POST `/api/dispatch/needs-info/{id}` returns 200 on claimed story | Route does not exist |
| C-02 | Response schema: status='needs_info', needs_info_path | Route does not exist |
| C-03 | 404 on unknown story_id | Route does not exist (any 404 is ambiguous until route exists) |
| C-04 | 409 on pending story (not claimed) | Route does not exist |
| C-05 | POST `/api/dispatch/resume/{id}` returns 200; story → pending | Route does not exist |
| C-06 | 404 or 409 on non-needs_info story | Route does not exist |
| C-07 | GET `/api/dispatch/queue` includes needs_info bucket | `DispatchQueueResponse.needs_info` field not added yet |
| C-08 | GET `/api/dispatch/next` returns 204 when only needs_info exists | `next_pending()` exclusion not implemented |

**Infrastructure note:** Tests use real asyncpg pool against `ops_console_test`.
Run migration `007_needs_info_state.sql` against the test DB before executing.
PG skip guard in `pytestmark` — all 17 tests skip if PG is unreachable.

---

## Test Group B — Phase Runner (9 tests, no PG needed)

**File:** `tests/deployment/test_phase_runner_needs_info.py`

### Group A — `_post_needs_info` helper

| ID | Test | Why RED |
|----|------|---------|
| A-01 | Returns True on HTTP 200 | `_post_needs_info` function does not exist |
| A-02 | Returns False on HTTP 500 | `_post_needs_info` function does not exist |
| A-03 | Returns False on URLError | `_post_needs_info` function does not exist |
| A-04 | POSTs to correct URL with correct body | `_post_needs_info` function does not exist |

### Group B — Integration with `run_sdlc_phases`

| ID | Test | Why RED |
|----|------|---------|
| B-01 | QUESTION.md present → `_post_needs_info` called | Branch not in `run_sdlc_phases` |
| B-02 | POST success → returns `(False, None)`, no retry | Branch not in `run_sdlc_phases` |
| B-03 | POST failure → `_notify_teams` fallback, still `(False, None)` | Branch not in `run_sdlc_phases` |

### Group C — Negative path

| ID | Test | Why RED / GREEN |
|----|------|-----------------|
| C-01 | No QUESTION.md → `_post_needs_info` never called | RED: function doesn't exist; GREEN once implemented (no call when no file) |

### Group D — Ordering

| ID | Test | Why RED |
|----|------|---------|
| D-01 | QUESTION.md + rc=-429 → needs_info path taken (not rate-limit path) | Ordering not implemented |

---

## Test Infra

### ops_console tests — asyncpg + FastAPI TestClient

```
Pattern: same as test_507_paused_routes.py
- Local db_pool fixture (truncates between tests)
- Local dispatch_db_service fixture (wraps real asyncpg pool)
- inject_mock_services() from conftest.py to inject service into app.state
- httpx.AsyncClient with X-API-Key header for route tests
- PG skip guard: pytestmark = pytest.mark.skipif(not _PG_AVAILABLE, ...)
```

To run against real PG:
```bash
# Start test DB (if not running)
docker run -d --name ops_test_pg \
  -e POSTGRES_USER=ops_console \
  -e POSTGRES_PASSWORD=ops_console \
  -e POSTGRES_DB=ops_console_test \
  -p 5432:5432 postgres:15

# Apply migrations
for f in scripts/migrations/*.sql; do
    psql postgresql://ops_console:ops_console@localhost/ops_console_test < "$f"
done

# Run tests
python3 -m pytest tests/ops_console/test_needs_info_state.py -v
```

### phase runner tests — unittest.mock

```
Pattern: same as test_phase_runner_project_file.py
- importlib.util to load sdlc_phase_runner.py from disk
- patch.object for internal functions (_run_phase_sdk, _post_needs_info, etc.)
- patch("urllib.request.urlopen") for HTTP calls
- tempfile / tmp_path for workdir fixture
- No external services required
```

```bash
python3 -m pytest tests/deployment/test_phase_runner_needs_info.py -v
```

---

## Feature Flag Handling

**ops_console tests:** The `test_settings` fixture in `conftest.py` will need
`dispatch_needs_info_enabled=True` added once the `Settings` model gains the
field. Until then, route tests fail because the endpoint returns 404 (feature
gate absent → disabled). This is an acceptable RED-state failure that becomes
GREEN when Phase 8 adds both the `Settings` field AND the route registration.

**Phase 8 implementation must add to `conftest.py`:**
```python
dispatch_needs_info_enabled=True,   # STORY-532
```

---

## Regression Protection

| Existing test | Relationship |
|---------------|-------------|
| `test_507_paused_routes.py` | Must remain GREEN — `paused` behavior unchanged |
| `test_routes_dispatch.py` | Must remain GREEN — existing dispatch routes unchanged |
| `test_dispatch_db_service.py` | Must remain GREEN — existing service methods unchanged |

**Run regression check:**
```bash
python3 -m pytest tests/ops_console/test_507_paused_routes.py \
                  tests/ops_console/test_routes_dispatch.py \
                  tests/ops_console/test_dispatch_db_service.py \
                  -v --no-header -q
```

---

## Frontend Testing (deferred to e2e)

The dashboard `needs_info` violet badge and group rendering (§7.2 of
feature-spec.md) are not covered by backend unit tests. Coverage options:

1. **Playwright e2e** (existing `e2e/` directory): add a test that loads the
   queue page and asserts a `needs_info` item renders with the `text-violet-400`
   class. Deferred — frontend unit tests are not part of this SDLC's standard
   phase-7 scope for Medium stories.
2. **Manual verification** during rollout (see feature-spec.md §11 Rollout Plan).

---

## RED State Verification

```
python3 -m pytest tests/ops_console/test_needs_info_state.py \
                  tests/deployment/test_phase_runner_needs_info.py \
                  -v --no-header -q
```

**Expected output:**
```
FAILED tests/deployment/...::TestPostNeedsInfoHelper::test_returns_true_on_200
FAILED tests/deployment/...::TestPostNeedsInfoHelper::test_returns_false_on_http_error
FAILED tests/deployment/...::TestPostNeedsInfoHelper::test_returns_false_on_network_exception
FAILED tests/deployment/...::TestPostNeedsInfoHelper::test_posts_to_correct_url_with_correct_body
FAILED tests/deployment/...::TestQuestionMdIntegration::test_question_md_detected_posts_needs_info
FAILED tests/deployment/...::TestQuestionMdIntegration::test_successful_post_returns_false_none_no_retry
FAILED tests/deployment/...::TestQuestionMdIntegration::test_failed_post_falls_back_to_notify_teams
FAILED tests/deployment/...::TestNoQuestionMd::test_no_question_md_no_post
FAILED tests/deployment/...::TestQuestionMdOrdering::test_question_md_wins_over_rate_limit
9 failed, 17 skipped
```

All 9 failures are **for the right reason** (`_post_needs_info not found in
sdlc_phase_runner.py` / method does not exist on DispatchDBService). No
errors (import failures, syntax errors). The 17 skips are PG-gated and will
activate once the test database is available.

---

## Next Phase

→ **Phase 8 (Implementation)** — turn all 26 tests GREEN by implementing:
1. `scripts/migrations/007_needs_info_state.sql`
2. `DispatchDBService.needs_info()` + `resume_from_needs_info()` + updated
   `next_pending()` + `list_queue()`
3. `POST /api/dispatch/needs-info/{id}` + `POST /api/dispatch/resume/{id}`
4. Updated `DispatchQueueResponse`, `DispatchItem`, `DispatchStatusEnum`
5. `_post_needs_info()` helper in `sdlc_phase_runner.py` + branch in
   `run_sdlc_phases`
6. Frontend: `statusBadge('needs_info')` (violet) + `needs_info` group in
   `DispatchQueue.tsx`; TypeScript union update in `api.ts`
7. `Settings.dispatch_needs_info_enabled` field + conftest addition
