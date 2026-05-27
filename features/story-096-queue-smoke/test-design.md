# STORY-096: Queue Smoke Tests — Test Design

## Test Strategy

All tests in this story ARE the deliverable — there is no production code change. Each test is marked `@pytest.mark.smoke` and verifies a critical queue subsystem path end-to-end within that subsystem's boundary.

**Test file:** `tests/test_queue_smoke.py`

## Test Cases

### T01: Local WorkQueue Round-Trip (SC-1)

| Field | Value |
|-------|-------|
| ID | T01 |
| Type | Smoke |
| Target | `scripts/work_queue.py` → `WorkQueue` |
| Marker | `@pytest.mark.smoke` |

**Steps:**
1. Create a `WorkQueue` with a `tmp_path` file
2. `enqueue("STORY-SMOKE-1", phase=7, scope="small", source="test")`
3. `set_active("STORY-SMOKE-1")`
4. Assert `get_active()` returns the story
5. `complete("STORY-SMOKE-1")`
6. Assert `get_active()` is None and queue is empty

**Pass criteria:** Full lifecycle completes without error; final state is empty.

### T02: Dispatch API Lifecycle (SC-2)

| Field | Value |
|-------|-------|
| ID | T02 |
| Type | Smoke |
| Target | `tech_dev_agents/ops_console/routes/dispatch.py` |
| Marker | `@pytest.mark.smoke` |

**Steps:**
1. Create a FastAPI TestClient with a mock `DispatchDBService`
2. POST `/api/dispatch` with `{"story_id": "STORY-SMOKE-2", "prompt": "test", "agent": "dan"}`
3. Assert 201 response with `status: "pending"`
4. GET `/api/dispatch/queue` — assert the item appears in `pending`
5. POST `/api/dispatch/claim/STORY-SMOKE-2` — assert 200, status becomes `"claimed"`
6. GET `/api/dispatch/queue` — assert item no longer in `pending`, now in `claimed`

Note: POST `/dispatch/complete` is **out of scope** — it requires a `commit_sha`
proof-of-work via GitHub API (STORY-253) and cannot run in an isolated smoke context.
SC-2 ends at claim.

**Pass criteria:** All three API calls return expected status codes and state transitions.

### T03: Stale Queue Entry Detection (SC-3)

| Field | Value |
|-------|-------|
| ID | T03 |
| Type | Smoke |
| Target | `scripts/work_queue.py` → stale detection |
| Marker | `@pytest.mark.smoke` |

**Steps:**
1. Create a `WorkQueue` with `tmp_path`
2. Enqueue and set_active a story with a fabricated PID (99999999 — guaranteed dead)
3. Call the stale detection / reconciliation logic
4. Assert the stale entry is cleared

**Pass criteria:** Dead-PID entry is detected and removed from active slot.

### T04: Side-Task Exclusion (SC-1, SC-4)

| Field | Value |
|-------|-------|
| ID | T04 |
| Type | Smoke |
| Target | `scripts/work_queue.py` → `is_side_task` / `enqueue` |
| Marker | `@pytest.mark.smoke` |

**Steps:**
1. Create a `WorkQueue` with `tmp_path`
2. Attempt `enqueue("SIDE-001", phase=1, scope="small", source="test")`
3. Assert returns `False` (rejected)
4. Attempt `enqueue("MAINT-001", phase=1, scope="small", source="test")`
5. Assert returns `False`
6. Attempt `enqueue("STORY-001", phase=1, scope="small", source="test")`
7. Assert returns `True` (accepted)

**Pass criteria:** Side-task prefixes are rejected; normal stories are accepted.

## Verification Commands

```bash
# Collect smoke tests — should show 5+ (1 existing + 4 new)
pytest -m smoke --collect-only

# Run smoke tests only
pytest -m smoke -v

# Run just queue smoke tests
pytest tests/test_queue_smoke.py -v
```

## Phase Gate Note

STORY-096 is classified **Medium** (reclassified from Small — see `feature-spec.md`).
The primary deliverable is `tests/test_queue_smoke.py` (5 smoke tests), supported by
minimal production additions to `DispatchFallbackService` required to make those tests
runnable against the JSON-fallback backend (no DB).

**For this story the standard RED → GREEN cycle applies as follows:**
Phase 7 (this file) specifies the test cases; Phase 8 adds the service helpers and
confirms all 5 tests pass (GREEN) before opening the PR.

**Phase 8 outcome:** All 5 smoke tests pass (GREEN). `implementation.md` reports GREEN status
for all 5 tests.
