# Phase 6: Feature Specification — Dispatch Queue Database Persistence & History

**Story:** STORY-028
**Phase:** 6 (Design)
**Date:** 2026-04-12
**Scope:** Medium
**Approach:** A — Direct asyncpg with Raw SQL

---

## 1. Overview

Replace the JSON-file dispatch queue (`dispatch-queue.json` + `fcntl` locking) with PostgreSQL 16 via `asyncpg`. Add lifecycle tracking (`completed`/`cancelled` terminal states), a paginated history endpoint, a completion endpoint, and agent auto-registration on first contact.

**Non-breaking:** All existing API response contracts preserved. Existing route tests pass unchanged after the migration.

---

## 2. Database Schema

### 2.1 Migration Script: `scripts/migrations/001_dispatch_queue.sql`

```sql
-- STORY-028: Dispatch Queue Database Persistence
-- Idempotent: safe to run multiple times

CREATE TABLE IF NOT EXISTS dispatch_items (
    id            BIGSERIAL PRIMARY KEY,
    story_id      TEXT NOT NULL,
    repo          TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'small'
        CHECK (scope IN ('small', 'medium', 'large')),
    prompt        TEXT NOT NULL,
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    enqueued_by   TEXT NOT NULL DEFAULT 'mark',
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled')),
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    cancelled_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial unique index: only one active dispatch per story_id
CREATE UNIQUE INDEX IF NOT EXISTS uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed');

-- Query indexes
CREATE INDEX IF NOT EXISTS idx_dispatch_status ON dispatch_items (status);
CREATE INDEX IF NOT EXISTS idx_dispatch_enqueued_at ON dispatch_items (enqueued_at);
CREATE INDEX IF NOT EXISTS idx_dispatch_claimed_by ON dispatch_items (claimed_by);

-- Agent registry table
CREATE TABLE IF NOT EXISTS agents (
    name           TEXT PRIMARY KEY,
    email          TEXT,
    vm             TEXT,
    ip             TEXT,
    ssh_port       INTEGER DEFAULT 443,
    status         TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'draining')),
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_via TEXT DEFAULT 'auto'
        CHECK (registered_via IN ('manual', 'auto'))
);
```

### 2.2 updated_at Trigger (Optional Enhancement)

The service layer sets `updated_at` explicitly on each write. No trigger needed.

---

## 3. Backend Service: `DispatchDBService`

**File:** `tech_dev_agents/ops_console/services/dispatch_db_service.py`

### 3.1 Constructor

```python
class DispatchDBService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
```

### 3.2 Methods

| Method | Signature | SQL | Returns |
|--------|-----------|-----|---------|
| `enqueue` | `async def enqueue(self, *, story_id, repo, scope, prompt, enqueued_by) -> dict` | `INSERT INTO dispatch_items (...) VALUES (...) RETURNING *` | Row dict or raises `DuplicateDispatchError` on unique violation |
| `list_queue` | `async def list_queue(self) -> dict` | `SELECT * FROM dispatch_items WHERE status IN ('pending', 'claimed') ORDER BY enqueued_at` | `{"pending": [...], "claimed": [...]}` |
| `next_pending` | `async def next_pending(self) -> dict \| None` | `SELECT * FROM dispatch_items WHERE status = 'pending' ORDER BY enqueued_at LIMIT 1` | Single row dict or `None` |
| `claim` | `async def claim(self, story_id: str, agent_name: str) -> dict` | `UPDATE dispatch_items SET status='claimed', claimed_by=$1, claimed_at=now(), updated_at=now() WHERE story_id=$2 AND status='pending' RETURNING *` | Row dict or raises `NotFoundError` / `AlreadyClaimedError` |
| `cancel` | `async def cancel(self, story_id: str) -> dict` | `UPDATE dispatch_items SET status='cancelled', cancelled_at=now(), updated_at=now() WHERE story_id=$1 AND status='pending' RETURNING *` | Row dict or raises `NotFoundError` / `AlreadyClaimedError` |
| `complete` | `async def complete(self, story_id: str) -> dict` | `UPDATE dispatch_items SET status='completed', completed_at=now(), updated_at=now() WHERE story_id=$1 AND status='claimed' RETURNING *` | Row dict or raises `NotFoundError` / `InvalidTransitionError` |
| `history` | `async def history(self, *, limit=50, offset=0, status_filter=None) -> dict` | `SELECT * FROM dispatch_items WHERE status IN ('completed', 'cancelled') ORDER BY updated_at DESC LIMIT $1 OFFSET $2` | `{"items": [...], "total": int}` |
| `recover_stale_claims` | `async def recover_stale_claims(self, timeout_seconds: int = 300) -> list[str]` | `UPDATE dispatch_items SET status='pending', claimed_by=NULL, claimed_at=NULL, updated_at=now() WHERE status='claimed' AND claimed_at < now() - make_interval(secs => $1) RETURNING story_id` | List of recovered story_ids |
| `pending_count` | `async def pending_count(self) -> int` | `SELECT COUNT(*) FROM dispatch_items WHERE status = 'pending'` | int |
| `register_agent` | `async def register_agent(self, name: str) -> bool` | `INSERT INTO agents (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET last_seen = now() RETURNING (xmax = 0) AS is_new` | `True` if new agent, `False` if existing |

### 3.3 Error Classes

```python
class DispatchDBError(Exception): ...
class DuplicateDispatchError(DispatchDBError): ...
class NotFoundError(DispatchDBError): ...
class AlreadyClaimedError(DispatchDBError): ...
class InvalidTransitionError(DispatchDBError): ...
```

### 3.4 Row-to-Dict Conversion

asyncpg returns `asyncpg.Record` objects. Each method converts to plain dict using `dict(row)` and serializes `TIMESTAMPTZ` fields to ISO 8601 strings for backward compatibility with existing Pydantic models.

```python
def _row_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    for key in ("enqueued_at", "claimed_at", "completed_at", "cancelled_at", "updated_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    return d
```

---

## 4. Model Updates

**File:** `tech_dev_agents/ops_console/models/responses.py`

### 4.1 Updated Enum

```python
class DispatchStatusEnum(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
```

### 4.2 Updated DispatchItem

```python
class DispatchItem(BaseModel):
    story_id: str
    repo: str
    scope: str
    prompt: str
    enqueued_at: str
    enqueued_by: str
    status: DispatchStatusEnum = DispatchStatusEnum.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None
    completed_at: str | None = None   # NEW
    cancelled_at: str | None = None   # NEW
```

### 4.3 New Response Models

```python
class CompleteRequest(BaseModel):
    """Optional metadata on completion."""
    pass  # Extensible — no required fields for v1

class CompleteResponse(BaseModel):
    story_id: str
    completed: bool
    completed_at: str
    item: DispatchItem

class DispatchHistoryResponse(BaseModel):
    items: list[DispatchItem]
    total: int
    limit: int
    offset: int
    fetched_at: str

class AgentRegisterRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    email: str | None = None
    vm: str | None = None
    ip: str | None = None

class AgentRegisterResponse(BaseModel):
    name: str
    registered: bool
    is_new: bool
    message: str
```

---

## 5. Route Changes

**File:** `tech_dev_agents/ops_console/routes/dispatch.py`

### 5.1 Service Access Pattern

All routes change from:
```python
svc = request.app.state.dispatch_service  # DispatchQueueService (JSON)
```
to:
```python
db_svc = request.app.state.dispatch_db_service  # DispatchDBService (PostgreSQL)
```

### 5.2 Modified Endpoints (Behavior Preserved)

**POST /api/dispatch (201)**
- Call `db_svc.pending_count()` to check queue cap (50)
- Call `db_svc.enqueue(...)` — catch `DuplicateDispatchError` → 409
- Return `DispatchItemResponse` (same shape)

**GET /api/dispatch/queue (200)**
- Call `db_svc.list_queue()` → returns `{"pending": [...], "claimed": [...]}`
- Map to `DispatchItem` models with correct status enum
- Return `DispatchQueueResponse` (same shape)

**GET /api/dispatch/next (200/204)**
- Extract `X-Agent-Name` header (optional) for auto-registration
- If header present, call `db_svc.register_agent(agent_name)` — if new, refresh Teams client
- Call `db_svc.next_pending()` → `None` returns 204
- Return `DispatchItemResponse` (same shape)

**POST /api/dispatch/claim/{story_id} (200/409/404)**
- Call `db_svc.register_agent(body.agent_name)` — auto-register on claim
- Call `db_svc.claim(story_id, body.agent_name)` — catch errors → 409/404
- Return `ClaimResponse` (same shape)

**DELETE /api/dispatch/queue/{story_id} (200/409/404)**
- Call `db_svc.cancel(story_id)` — catch errors → 409/404
- Return `CancelResponse` (same shape)

### 5.3 New Endpoints

**POST /api/dispatch/complete/{story_id} (200/404/409)**
```python
@router.post("/dispatch/complete/{story_id}", response_model=CompleteResponse)
async def complete_story(story_id: str, request: Request):
    db_svc = request.app.state.dispatch_db_service
    try:
        row = await db_svc.complete(story_id)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found in claimed queue")
    except InvalidTransitionError:
        raise HTTPException(409, f"{story_id} is not in claimed status")
    item = DispatchItem(**row)
    return CompleteResponse(
        story_id=story_id, completed=True, completed_at=row["completed_at"], item=item
    )
```

**GET /api/dispatch/history (200)**
```python
@router.get("/dispatch/history", response_model=DispatchHistoryResponse)
async def dispatch_history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, pattern=r"^(completed|cancelled)$"),
):
    db_svc = request.app.state.dispatch_db_service
    result = await db_svc.history(limit=limit, offset=offset, status_filter=status)
    items = [DispatchItem(**row) for row in result["items"]]
    return DispatchHistoryResponse(
        items=items, total=result["total"],
        limit=limit, offset=offset,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
```

**POST /api/agents/register (200)**
```python
@router.post("/agents/register", response_model=AgentRegisterResponse)
async def register_agent(body: AgentRegisterRequest, request: Request):
    db_svc = request.app.state.dispatch_db_service
    is_new = await db_svc.register_agent(body.name)
    if is_new:
        # Refresh Teams client agent list
        teams = getattr(request.app.state, "teams_client", None)
        if teams:
            teams.add_agent(body.name)
    return AgentRegisterResponse(
        name=body.name, registered=True, is_new=is_new,
        message="Registered" if is_new else "Updated last_seen",
    )
```

---

## 6. Configuration & Lifespan

### 6.1 Config Changes

**File:** `tech_dev_agents/ops_console/config.py`

```python
# Add to Settings class:
database_url: str = "postgresql://ops_console@localhost/ops_console"
```

### 6.2 Lifespan Changes

**File:** `tech_dev_agents/ops_console/main.py`

```python
import asyncpg

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing service creation ...

    # Database pool (STORY-028)
    db_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
    )

    # Dispatch DB service replaces JSON service
    dispatch_db_service = DispatchDBService(db_pool)
    app.state.dispatch_db_service = dispatch_db_service

    # Stale recovery loop (updated for async DB service)
    recovery_task = asyncio.create_task(
        _stale_claim_recovery_loop(dispatch_db_service),
        name="stale-claim-recovery",
    )

    yield

    # Shutdown
    recovery_task.cancel()
    try:
        await recovery_task
    except asyncio.CancelledError:
        pass
    await db_pool.close()
    await http_client.aclose()
```

### 6.3 Stale Recovery Loop Update

The loop becomes async since `DispatchDBService.recover_stale_claims()` is async:

```python
async def _stale_claim_recovery_loop(service: DispatchDBService) -> None:
    while True:
        try:
            recovered = await service.recover_stale_claims()
            if recovered:
                logger.info("Stale claim recovery: %s", recovered)
        except Exception:
            logger.error("Stale claim recovery failed", exc_info=True)
        await asyncio.sleep(60)
```

---

## 7. Agent-Side Changes

**File:** `deployment/hermes/dispatch_poller.py`

### 7.1 Add X-Agent-Name Header

In `poll_once()`, add `X-Agent-Name` to the `/next` request:

```python
headers = {"X-API-Key": api_key, "X-Agent-Name": agent_name}
```

### 7.2 Completion Callback

After `start_story()` launches SDK, the agent needs to call `/complete` when done. This is handled by adding a completion call after the subprocess finishes. Since `start_story()` uses `Popen` (non-blocking), the completion callback is added as a wrapper:

```python
def start_story(*, story_id, repo, scope, prompt, workspace):
    # ... existing local queue + Popen logic ...

    # Launch completion watcher in a thread
    threading.Thread(
        target=_watch_and_complete,
        args=(proc, story_id, base_url, api_key),
        daemon=True,
    ).start()

def _watch_and_complete(proc, story_id, base_url, api_key):
    """Wait for SDK process to finish, then call /complete."""
    proc.wait()
    if proc.returncode == 0:
        try:
            requests.post(
                f"{base_url}/api/dispatch/complete/{story_id}",
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            print(f"[DISPATCH] Completed {story_id}", flush=True)
        except Exception as exc:
            print(f"[DISPATCH] Failed to complete {story_id}: {exc}", flush=True)
```

**Note:** `base_url` and `api_key` need to be passed to `start_story()` or made module-level. Simplest approach: add them as parameters.

---

## 8. Frontend Changes

### 8.1 Type Updates

**File:** `frontend/src/types/api.ts`

```typescript
export interface DispatchItem {
  story_id: string;
  repo: string;
  scope: string;
  prompt: string;
  enqueued_at: string;
  enqueued_by: string;
  status: 'pending' | 'claimed' | 'completed' | 'cancelled';  // UPDATED
  claimed_by: string | null;
  claimed_at: string | null;
  completed_at: string | null;  // NEW
  cancelled_at: string | null;  // NEW
}

export interface DispatchHistoryResponse {
  items: DispatchItem[];
  total: number;
  limit: number;
  offset: number;
  fetched_at: string;
}
```

### 8.2 New Hook

**File:** `frontend/src/hooks/useDispatchQueue.ts`

```typescript
export function useDispatchHistory(page: number = 0, limit: number = 20) {
  return useQuery({
    queryKey: ['dispatch-history', page, limit],
    queryFn: () => api.get<DispatchHistoryResponse>(
      `/api/dispatch/history?limit=${limit}&offset=${page * limit}`
    ),
    staleTime: 30_000,
  });
}
```

### 8.3 Component Updates

**File:** `frontend/src/components/DispatchQueue.tsx`

Add a tab system: **Queue** (existing) | **History** (new)

```
+------------------------------------------+
| Dispatch Queue           [Queue] [History] |
+------------------------------------------+
| Queue tab (existing):                      |
|   Story | Repo | Scope | Agent | Time | Status | Actions |
+------------------------------------------+
| History tab (new):                         |
|   Story | Repo | Scope | Agent | Enqueued | Completed | Duration | Status |
|   STORY-094 | adv-amz | small | dan | 2h ago | 1h ago | 45m | completed |
|   STORY-095 | adv-amz | medium | — | 3h ago | 2h ago | — | cancelled |
|                                             |
|   [< Prev] Page 1 of 3 [Next >]           |
+------------------------------------------+
```

Status badge colors:
- `pending` → yellow
- `claimed` → blue
- `completed` → green
- `cancelled` → red

Duration computed client-side: `completed_at - claimed_at` (or `cancelled_at - enqueued_at`).

---

## 9. Test Strategy

### 9.1 Service Tests (`test_dispatch_db_service.py`)

Tests use a real PostgreSQL test database (`ops_console_test`) with table truncation between tests via a pytest fixture.

| ID | Test | AC |
|----|------|-----|
| T01 | `enqueue` inserts row with status=pending | AC-1 |
| T02 | `enqueue` duplicate active story → `DuplicateDispatchError` | AC-9 |
| T03 | `enqueue` same story after completion → succeeds (partial index) | AC-9 |
| T04 | `list_queue` returns pending + claimed, not terminal | AC-2 |
| T05 | `next_pending` returns oldest pending, None if empty | AC-3 |
| T06 | `claim` transitions pending→claimed with agent_name + timestamp | AC-4 |
| T07 | `claim` non-pending → error | AC-4 |
| T08 | `cancel` transitions pending→cancelled with timestamp | AC-5 |
| T09 | `cancel` claimed → `AlreadyClaimedError` | AC-5 |
| T10 | `complete` transitions claimed→completed with timestamp | AC-6 |
| T11 | `complete` non-claimed → `InvalidTransitionError` | AC-6 |
| T12 | `history` returns terminal items, paginated, ordered by updated_at desc | AC-7 |
| T13 | `history` with status_filter | AC-7 |
| T14 | `recover_stale_claims` moves old claims to pending | AC-8 |
| T15 | `recover_stale_claims` leaves fresh claims alone | AC-8 |
| T16 | `register_agent` creates new row, returns is_new=True | AC-10 |
| T17 | `register_agent` existing agent updates last_seen, returns is_new=False | AC-10 |
| T18 | `pending_count` returns correct count | AC-2 |

### 9.2 Route Tests — Existing (Regression)

All existing tests in `test_routes_dispatch.py` pass unchanged. The fixture changes from `DispatchQueueService(tmp_path)` to a mock `DispatchDBService` or a test DB.

### 9.3 Route Tests — New Endpoints

| ID | Test | AC |
|----|------|-----|
| T20 | POST /dispatch/complete/{story_id} → 200 with CompleteResponse | AC-6 |
| T21 | POST /dispatch/complete not-claimed → 404/409 | AC-6 |
| T22 | GET /dispatch/history → paginated list | AC-7 |
| T23 | GET /dispatch/history?status=completed → filtered | AC-7 |
| T24 | POST /agents/register → 200 with is_new=true | AC-10 |
| T25 | New endpoints require auth | AC-14 |

### 9.4 Migration Test

| ID | Test | AC |
|----|------|-----|
| T30 | Migration script runs twice without error (idempotent) | AC-13 |

### 9.5 Fixture: Test Database

```python
@pytest_asyncio.fixture
async def db_pool():
    pool = await asyncpg.create_pool("postgresql://ops_console@localhost/ops_console_test")
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents CASCADE")
    yield pool
    await pool.close()

@pytest_asyncio.fixture
async def db_service(db_pool):
    return DispatchDBService(db_pool)
```

---

## 10. File Change Summary

| File | Action | Key Changes |
|------|--------|-------------|
| `scripts/migrations/001_dispatch_queue.sql` | **Create** | DDL for dispatch_items + agents tables |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | **Create** | DispatchDBService with all async methods |
| `tech_dev_agents/ops_console/routes/dispatch.py` | **Modify** | Swap service, add 3 new endpoints |
| `tech_dev_agents/ops_console/models/responses.py` | **Modify** | Enum values, new response models |
| `tech_dev_agents/ops_console/config.py` | **Modify** | Add `database_url` setting |
| `tech_dev_agents/ops_console/main.py` | **Modify** | asyncpg pool, new service, async recovery loop |
| `deployment/hermes/dispatch_poller.py` | **Modify** | X-Agent-Name header, completion callback |
| `frontend/src/components/DispatchQueue.tsx` | **Modify** | Tab system, History tab, status badges |
| `frontend/src/hooks/useDispatchQueue.ts` | **Modify** | Add `useDispatchHistory()` hook |
| `frontend/src/types/api.ts` | **Modify** | New status values, history response type |
| `tests/ops_console/test_dispatch_db_service.py` | **Create** | 18 service tests |
| `tests/ops_console/test_routes_dispatch.py` | **Modify** | Adapt fixtures for DB service |
| `pyproject.toml` | **Modify** | Add asyncpg dependency |

---

## 11. Implementation Order

1. **pyproject.toml** — add `asyncpg` dependency
2. **Migration script** — create tables/indexes
3. **Models** — update enum, add new response models
4. **Service** — `DispatchDBService` with all methods
5. **Config** — add `database_url`
6. **Main** — pool lifecycle, wire service
7. **Routes** — swap service, add new endpoints
8. **Tests (service)** — 18 tests for DB service
9. **Tests (routes)** — adapt fixtures, add new endpoint tests
10. **Agent poller** — X-Agent-Name header, completion callback
11. **Frontend types** — update DispatchItem, add history types
12. **Frontend hook** — `useDispatchHistory()`
13. **Frontend component** — History tab with pagination

---

## 12. Rollback Plan

1. Revert `main.py` to use `DispatchQueueService` (JSON) instead of `DispatchDBService`
2. JSON file is preserved (not deleted) during migration
3. No data loss — PostgreSQL data remains; JSON backup available
4. No schema migration needed for rollback (tables remain, unused)
