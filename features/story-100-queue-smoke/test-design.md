# STORY-100 — Test Design: Dispatch Queue Smoke Tests

## 1. Test Strategy

**Goal:** Provide lightweight, fast smoke tests that verify the dispatch queue API contract without requiring a live PostgreSQL database. These tests are tagged `@pytest.mark.smoke` for inclusion in the pre-deploy gate.

**Approach:** Mock `DispatchDBService` at the application layer and exercise the FastAPI routes via `httpx.AsyncClient`. Each test verifies a single critical behavior of the dispatch queue lifecycle.

**File:** `tests/ops_console/test_dispatch_queue_smoke.py`

---

## 2. Fixtures

| Fixture                  | Scope    | Description                                                                                   |
| ------------------------ | -------- | --------------------------------------------------------------------------------------------- |
| `mock_dispatch_db`       | function | `AsyncMock` of `DispatchDBService` with pre-configured return values for common operations     |
| `smoke_client`           | function | `httpx.AsyncClient` bound to the FastAPI app with mocked services and valid API key injected   |

**Shared Constants:**

| Name               | Value                  | Purpose                                  |
| ------------------ | ---------------------- | ---------------------------------------- |
| `SMOKE_STORY_A`    | `"STORY-SMOKE-A"`     | First test story for FIFO ordering       |
| `SMOKE_STORY_B`    | `"STORY-SMOKE-B"`     | Second test story for FIFO ordering      |
| `SMOKE_REPO`       | `"tech-dev-agents"`   | Default repo for all smoke tests         |

---

## 3. Test Cases

### T01 — Enqueue returns 201 with correct shape

**AC:** AC-3, AC-8

**Setup:**
- `mock_dispatch_db.enqueue()` returns a dict with `story_id`, `status="pending"`, `repo`, `enqueued_at`.

**Action:**
- `POST /api/dispatch` with `{"story_id": "STORY-SMOKE-A", "repo": "tech-dev-agents", "scope": "small", "prompt": "smoke test"}`.

**Assertions:**
- Response status is `201`.
- Response body contains `story_id == "STORY-SMOKE-A"` and `status == "pending"`.
- `mock_dispatch_db.enqueue` was called once.

---

### T02 — Queue list returns 200 with status buckets

**AC:** AC-4, AC-8

**Setup:**
- `mock_dispatch_db.get_queue()` returns a dict with `pending: [item_A, item_B]` (A enqueued before B) and empty buckets for other statuses.

**Action:**
- `GET /api/dispatch/queue`.

**Assertions:**
- Response status is `200`.
- `pending` array has 2 items.
- `pending[0].story_id == "STORY-SMOKE-A"` (FIFO: A before B).
- `pending[1].story_id == "STORY-SMOKE-B"`.
- All other status buckets (`claimed`, `in_review`, `completed`, `failed`, `paused`, `needs_info`) are present (may be empty).

---

### T03 — Claim returns 200 and transitions to claimed

**AC:** AC-3, AC-8

**Setup:**
- `mock_dispatch_db.claim()` returns a dict with `story_id`, `status="claimed"`, `claimed_by="test-agent"`, `claimed_at`.

**Action:**
- `POST /api/dispatch/claim/STORY-SMOKE-A` with `{"agent_name": "test-agent"}`.

**Assertions:**
- Response status is `200`.
- Response body contains `status == "claimed"` and `claimed_by == "test-agent"`.

---

### T04 — Complete returns 200 and transitions to completed

**AC:** AC-3, AC-8

**Setup:**
- `mock_dispatch_db.complete()` returns a dict with `story_id`, `status="completed"`, `completed_at`, `commit_sha`.

**Action:**
- `POST /api/dispatch/complete/STORY-SMOKE-A` with `{"commit_sha": "abc123", "pr_number": 42}`.

**Assertions:**
- Response status is `200`.
- Response body contains `status == "completed"`.

---

### T05 — Duplicate enqueue returns 409

**AC:** AC-5, AC-8

**Setup:**
- `mock_dispatch_db.enqueue()` raises `asyncpg.UniqueViolationError` (or the service raises an equivalent application-level error indicating duplicate).

**Action:**
- `POST /api/dispatch` with `{"story_id": "STORY-SMOKE-A", "repo": "tech-dev-agents", "scope": "small", "prompt": "duplicate"}`.

**Assertions:**
- Response status is `409`.
- Response body contains an error message referencing duplicate or already-enqueued.

---

### T06 — Claim non-existent story returns 404

**AC:** AC-6, AC-8

**Setup:**
- `mock_dispatch_db.claim()` returns `None` (story not found).

**Action:**
- `POST /api/dispatch/claim/STORY-NONEXISTENT` with `{"agent_name": "test-agent"}`.

**Assertions:**
- Response status is `404`.

---

### T07 — Metrics endpoint returns 200 with expected shape

**AC:** AC-7, AC-8

**Setup:**
- `mock_dispatch_db.get_metrics()` returns a dict with `total_enqueued`, `total_completed`, `total_failed`, `avg_cycle_time_hours`.

**Action:**
- `GET /api/dispatch/metrics`.

**Assertions:**
- Response status is `200`.
- Response body contains keys: `total_enqueued`, `total_completed`, `total_failed`.
- All values are numeric (int or float).

---

### T08 — Full lifecycle: enqueue → list → claim → complete

**AC:** AC-3, AC-8

**Setup:**
- Chain mock returns: `enqueue()` → pending item, `get_queue()` → item in pending bucket, `claim()` → claimed item, `complete()` → completed item.

**Action:**
1. `POST /api/dispatch` with story SMOKE-A → 201.
2. `GET /api/dispatch/queue` → 200, SMOKE-A in pending.
3. `POST /api/dispatch/claim/STORY-SMOKE-A` → 200, status=claimed.
4. `POST /api/dispatch/complete/STORY-SMOKE-A` → 200, status=completed.

**Assertions:**
- Each step returns the expected status code.
- The mock DB service methods are called in the correct order.
- Final state is `completed`.

---

## 4. Pytest Markers

All tests in the module are decorated with:

```python
pytestmark = pytest.mark.smoke
```

This ensures `pytest -m smoke` collects all tests in the file without requiring per-test decoration.

---

## 5. Coverage Matrix

| AC   | Test(s)       | Verified By                                              |
| ---- | ------------- | -------------------------------------------------------- |
| AC-1 | —             | File existence on disk                                   |
| AC-2 | All           | `pytestmark = pytest.mark.smoke` at module level         |
| AC-3 | T01, T03, T04, T08 | Enqueue/claim/complete lifecycle                    |
| AC-4 | T02           | FIFO ordering in queue list                              |
| AC-5 | T05           | Duplicate enqueue → 409                                  |
| AC-6 | T06           | Claim non-existent → 404                                 |
| AC-7 | T07           | Metrics endpoint shape                                   |
| AC-8 | All           | All tests use mocked DB — no PostgreSQL required         |
| AC-9 | All           | `pytest -m smoke` collects and passes these tests        |

---

## 6. Run Instructions

```bash
# Run only smoke tests (what the pre-deploy gate does)
pytest -m smoke -v

# Run only this module
pytest tests/ops_console/test_dispatch_queue_smoke.py -v

# Verify collection
pytest -m smoke --collect-only
```
