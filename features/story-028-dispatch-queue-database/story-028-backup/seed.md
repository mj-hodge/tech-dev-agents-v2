# Seed: Dispatch Queue Database Persistence & History

**Story:** STORY-028
**Date:** 2026-04-12
**Scope:** Medium
**Phase Path:** 1 -> 4 -> 6 -> [6b, 6c, 6d] -> 7 -> 8 -> 8b -> 11 -> Done

---

## Problem Statement

The central dispatch queue (STORY-026) stores all state in a JSON file (`/opt/ops-console/dispatch-queue.json`) with `fcntl` advisory locking. This design was intentionally minimal for a 2-agent fleet, but has three significant limitations:

1. **No history.** When a story is claimed, it moves from `pending` to `claimed`. When the agent finishes or the claim goes stale, the record is either recovered back to pending or simply lost. There is no record of completed or cancelled dispatches. Mark cannot answer "what did the agents work on last week?" or "how long did STORY-094 sit in the queue before pickup?"

2. **No terminal states.** The `DispatchStatusEnum` only has `PENDING` and `CLAIMED`. There is no `COMPLETED` or `CANCELLED` status. When the cancel endpoint removes a pending item, the record vanishes. When an agent finishes work, there is no callback to mark the dispatch as completed -- the claimed record lingers until stale recovery recycles it.

3. **Teams auto-registration gap.** Agents are tracked in a static `agent-registry.json` file. When a new agent VM is provisioned, someone must manually edit this file. There is no mechanism for an agent to register itself by calling the ops console API, nor for the ops console to discover agents that poll the dispatch queue and auto-register them in the Teams client for messaging.

4. **Concurrency ceiling.** File-based locking with `fcntl` is process-local and single-machine only. As the fleet scales beyond 2 agents, the JSON file becomes a bottleneck (full read-parse-write-replace on every operation) and cannot be shared across multiple ops console instances.

PostgreSQL 16 is already running on the ops console VM (`/var/run/postgresql:5432`). Migrating the dispatch queue to a proper database removes the file I/O bottleneck, adds queryable history, and provides ACID transactions with real concurrency control.

## Desired Outcome

Replace the JSON file dispatch queue with PostgreSQL-backed persistence using `asyncpg`. Add lifecycle tracking so every dispatch goes through `pending -> claimed -> completed|cancelled`. Expose a history endpoint for past dispatches. Allow agents to auto-register when they first poll the queue.

After this story:
- Mark can query `/api/dispatch/history` to see completed and cancelled dispatches with timestamps
- Agents that poll the queue are automatically registered in the agent registry (Teams client picks them up)
- The dispatch queue is backed by PostgreSQL with proper indexes, transactions, and concurrent access
- All existing API contracts are preserved (no breaking changes to existing endpoints)

## Users

- **Mark** -- views dispatch history in dashboard, dispatches work as before
- **Agents (Dan, Derrick, future agents)** -- poll queue as before, auto-registered on first contact
- **Ops Console** -- reads/writes PostgreSQL instead of JSON file

## Architecture

### Database Schema

```sql
-- Dispatch items table (replaces dispatch-queue.json)
CREATE TABLE dispatch_items (
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
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_story_active UNIQUE (story_id)
        -- Prevents duplicate active dispatches; history rows excluded via partial index
);

-- Partial unique index: only one active (non-terminal) dispatch per story_id
-- The table-level UNIQUE is replaced by this for correctness:
CREATE UNIQUE INDEX uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed');

-- Query indexes
CREATE INDEX idx_dispatch_status ON dispatch_items (status);
CREATE INDEX idx_dispatch_enqueued_at ON dispatch_items (enqueued_at);
CREATE INDEX idx_dispatch_claimed_by ON dispatch_items (claimed_by);

-- Agent registry table (replaces agent-registry.json for dispatch-aware agents)
CREATE TABLE agents (
    name          TEXT PRIMARY KEY,
    email         TEXT,
    vm            TEXT,
    ip            TEXT,
    ssh_port      INTEGER DEFAULT 443,
    status        TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'draining')),
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_via TEXT DEFAULT 'auto'
        CHECK (registered_via IN ('manual', 'auto'))
);
```

### Connection Management

- Use `asyncpg.create_pool()` in the FastAPI lifespan (min=2, max=10 connections)
- Pool attached to `app.state.db_pool`
- Graceful shutdown: `pool.close()` + `pool.wait_closed()`
- Connection string from `OPS_DATABASE_URL` env var (default: `postgresql://ops_console@localhost/ops_console`)

### New Service: `DispatchDBService`

Replaces `DispatchQueueService` (JSON file). Same public interface so routes need minimal changes:

| Method | SQL | Notes |
|--------|-----|-------|
| `enqueue(item)` | `INSERT INTO dispatch_items ... RETURNING *` | Conflict on active story_id -> 409 |
| `list_queue()` | `SELECT * FROM dispatch_items WHERE status IN ('pending', 'claimed') ORDER BY enqueued_at` | Returns same shape as current `load()` |
| `next_pending()` | `SELECT * FROM dispatch_items WHERE status = 'pending' ORDER BY enqueued_at LIMIT 1` | 204 if none |
| `claim(story_id, agent_name)` | `UPDATE dispatch_items SET status='claimed', claimed_by=$1, claimed_at=now() WHERE story_id=$2 AND status='pending' RETURNING *` | Row-level lock prevents double-claim |
| `cancel(story_id)` | `UPDATE dispatch_items SET status='cancelled', cancelled_at=now() WHERE story_id=$1 AND status='pending' RETURNING *` | Terminal state, not deleted |
| `complete(story_id)` | `UPDATE dispatch_items SET status='completed', completed_at=now() WHERE story_id=$1 AND status='claimed' RETURNING *` | Called by agent on story completion |
| `history(limit, offset, status_filter)` | `SELECT * FROM dispatch_items WHERE status IN ('completed', 'cancelled') ORDER BY updated_at DESC LIMIT $1 OFFSET $2` | Paginated |
| `recover_stale_claims(timeout_seconds)` | `UPDATE dispatch_items SET status='pending', claimed_by=NULL, claimed_at=NULL WHERE status='claimed' AND claimed_at < now() - interval '...' RETURNING story_id` | Same behavior, single atomic query |

### New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dispatch/history` | Paginated history of completed/cancelled dispatches |
| POST | `/api/dispatch/complete/{story_id}` | Mark a claimed dispatch as completed |
| POST | `/api/agents/register` | Agent self-registration (called on first poll) |

### Modified Endpoints (Non-Breaking)

All existing endpoints preserve their request/response contracts. Internal implementation switches from `DispatchQueueService` (JSON) to `DispatchDBService` (PostgreSQL):

| Endpoint | Change |
|----------|--------|
| `POST /api/dispatch` | INSERT instead of JSON append |
| `GET /api/dispatch/queue` | SELECT instead of JSON load |
| `GET /api/dispatch/next` | SELECT with LIMIT 1 instead of `queue["pending"][0]` |
| `POST /api/dispatch/claim/{story_id}` | UPDATE with row lock instead of JSON pop+append |
| `DELETE /api/dispatch/queue/{story_id}` | UPDATE to `cancelled` status instead of JSON pop (soft delete) |

### Teams Auto-Registration

When an agent calls `GET /api/dispatch/next` or `POST /api/dispatch/claim/{story_id}`:
1. Extract agent identity from `X-Agent-Name` header (or `ClaimRequest.agent_name`)
2. Upsert into `agents` table: `INSERT ... ON CONFLICT (name) DO UPDATE SET last_seen = now()`
3. If new agent (INSERT, not UPDATE), refresh the in-memory Teams client agent list
4. The `GET /api/dispatch/next` endpoint adds `X-Agent-Name` as an optional header; existing agents already send `AGENT_NAME` env var

### Agent-Side Changes

The `dispatch_poller.py` needs two small changes:
1. Send `X-Agent-Name` header on `GET /api/dispatch/next` (for auto-registration)
2. Call `POST /api/dispatch/complete/{story_id}` when `claude_sdk_tool.py` finishes successfully

### Frontend Changes

- Add "History" tab to `DispatchQueue.tsx` component
- New `useDispatchHistory()` React Query hook for paginated history
- Status badges: pending (blue), claimed (yellow), completed (green), cancelled (red)
- History table columns: Story, Repo, Scope, Agent, Enqueued, Claimed, Completed, Duration

### Migration Strategy

1. Create database and tables via SQL migration script (`scripts/migrations/001_dispatch_queue.sql`)
2. `DispatchDBService` replaces `DispatchQueueService` in `main.py` lifespan
3. One-time migration script to import existing JSON queue data into PostgreSQL
4. JSON file path config (`dispatch_queue_path`) replaced by `database_url` config
5. Fallback: if database is unreachable, log error and return 503 (no silent fallback to JSON)

## Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| AC-1 | `POST /api/dispatch` inserts a dispatch item into PostgreSQL with status `pending` | Integration test: POST -> SELECT confirms row exists |
| AC-2 | `GET /api/dispatch/queue` returns pending and claimed items from PostgreSQL (same response shape) | Integration test: response matches `DispatchQueueResponse` schema |
| AC-3 | `GET /api/dispatch/next` returns oldest pending item from PostgreSQL (or 204) | Integration test: FIFO ordering verified |
| AC-4 | `POST /api/dispatch/claim/{story_id}` atomically updates status to `claimed` using row-level lock | Integration test: concurrent claims -> exactly one succeeds, other gets 409 |
| AC-5 | `DELETE /api/dispatch/queue/{story_id}` sets status to `cancelled` (soft delete, not row removal) | Integration test: item still queryable via history with `cancelled` status |
| AC-6 | `POST /api/dispatch/complete/{story_id}` transitions claimed item to `completed` with timestamp | Integration test: complete -> status check |
| AC-7 | `GET /api/dispatch/history` returns paginated completed/cancelled dispatches | Integration test: pagination params, correct ordering |
| AC-8 | Stale claim recovery runs as background task, uses single UPDATE query | Integration test: claimed item older than 5min returns to pending |
| AC-9 | Duplicate active dispatch for same story_id returns 409 (partial unique index) | Integration test: double enqueue -> 409 |
| AC-10 | Agents auto-register in `agents` table on first contact with dispatch API | Integration test: new agent name -> row created in agents table |
| AC-11 | Auto-registered agents appear in Teams client for messaging | Integration test: mock Teams client refreshed after new agent |
| AC-12 | `asyncpg` connection pool created in lifespan, closed on shutdown | Unit test: pool lifecycle |
| AC-13 | SQL migration script creates all tables and indexes idempotently (`IF NOT EXISTS`) | Migration test: script runs twice without error |
| AC-14 | All existing API response contracts preserved (no breaking changes) | Existing STORY-026 route tests pass without modification |
| AC-15 | Frontend history tab shows completed/cancelled dispatches with duration | Manual verification (or Playwright if available) |

## Out of Scope (v1)

- Database connection pooling across multiple ops console instances (single instance for now)
- Read replicas or failover (single PostgreSQL instance is sufficient)
- Archiving old dispatch records (no TTL/purge policy yet)
- Agent capability matching or priority queues (still FIFO, any agent)
- Full agent registry migration (only dispatch-discovered agents auto-register; static registry remains for non-dispatch features)
- Alembic or other migration framework (raw SQL scripts are sufficient for this scope)

## Dependencies

- **STORY-026** (Central Dispatch Queue) -- Complete. Provides API endpoints, models, tests, frontend
- **STORY-027** (Dispatch Queue Auto-Pickup) -- Complete. Provides agent polling loop, stale recovery
- **PostgreSQL 16** -- Running on ops console VM at `localhost:5432`
- **asyncpg** -- New dependency (add to pyproject.toml)

## Technical Notes

### asyncpg vs alternatives

| Library | Async | Performance | Connection Pool | Raw SQL |
|---------|-------|-------------|-----------------|---------|
| asyncpg | Yes | Fastest (C ext) | Built-in | Yes |
| psycopg3 | Yes | Fast | External (connpool) | Yes |
| SQLAlchemy async | Yes | Moderate | Via asyncpg/psycopg | ORM optional |

**Choice: asyncpg.** Rationale: fastest async driver, built-in connection pool, no ORM overhead for a simple 2-table schema. The project has no existing ORM patterns to match.

### Database Setup

```bash
# Create database and role (one-time, run as postgres superuser)
sudo -u postgres psql -c "CREATE ROLE ops_console WITH LOGIN;"
sudo -u postgres psql -c "CREATE DATABASE ops_console OWNER ops_console;"
```

### Connection String

```
postgresql://ops_console@localhost/ops_console
```

No password needed for local Unix socket connections with `peer` auth. For TCP connections, add password or use `.pgpass`.

### Estimated File Changes

| File | Action | Est. Lines |
|------|--------|-----------|
| `scripts/migrations/001_dispatch_queue.sql` | **Create** -- schema DDL | ~40 |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | **Create** -- PostgreSQL service | ~200 |
| `tech_dev_agents/ops_console/routes/dispatch.py` | **Modify** -- swap service, add new endpoints | ~80 |
| `tech_dev_agents/ops_console/models/responses.py` | **Modify** -- add COMPLETED/CANCELLED enum, history models | ~30 |
| `tech_dev_agents/ops_console/config.py` | **Modify** -- add `database_url` setting | ~5 |
| `tech_dev_agents/ops_console/main.py` | **Modify** -- pool lifecycle, swap service | ~30 |
| `deployment/hermes/dispatch_poller.py` | **Modify** -- add X-Agent-Name header, completion callback | ~20 |
| `frontend/src/components/DispatchQueue.tsx` | **Modify** -- history tab, new status badges | ~60 |
| `frontend/src/hooks/useDispatchQueue.ts` | **Modify** -- add `useDispatchHistory()` | ~20 |
| `frontend/src/types/api.ts` | **Modify** -- add history types, new statuses | ~15 |
| `tests/ops_console/test_dispatch_db_service.py` | **Create** -- DB service unit tests | ~250 |
| `tests/ops_console/test_routes_dispatch.py` | **Modify** -- adapt fixtures for DB | ~50 |
| `pyproject.toml` | **Modify** -- add asyncpg dependency | ~2 |

**Total estimated new/modified lines:** ~800

## Scope Classification: Medium

- Replaces a core persistence layer (JSON -> PostgreSQL) across service, routes, and config
- Adds new API endpoints (history, complete, agent register)
- Requires database schema design and migration script
- Touches frontend (history tab) and agent-side code (completion callback)
- No new architectural patterns -- follows existing FastAPI service/route structure
- Well-bounded: 2 tables, 3 new endpoints, 1 migration script

Not small because it touches multiple layers (DB, service, routes, frontend, agent poller).
Not large because the domain is narrow (dispatch queue only) and the API surface is small.

## Risks

| Risk | Mitigation |
|------|-----------|
| PostgreSQL connection failure at startup | Fail fast with clear error; health endpoint reports DB status |
| asyncpg pool exhaustion under load | Max 10 connections, 2 agents polling at 60s intervals -- no realistic concern |
| Migration breaks existing dispatch data | One-time import script preserves pending/claimed items |
| Breaking existing API contracts | Existing STORY-026 tests run as-is to verify compatibility |
| Agent auto-register spam | Upsert is idempotent; `last_seen` timestamp updates, no duplicate rows |
| Test isolation (shared DB state) | Use unique DB per test run or TRUNCATE between tests |

## Phase Path

`1 -> 4 -> 6 -> [6b, 6c, 6d] -> 7 -> 8 -> 8b -> 11 -> Done`
