# Seed: Dispatch Queue — Database Persistence & History

**Story:** STORY-028
**Date:** 2026-04-11
**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done

---

## Problem Statement

The central dispatch queue (STORY-026/027) works end-to-end — stories flow through pending → claimed, agents auto-pickup via polling, and the fleet command shows queue status. But the queue uses a JSON file with fcntl locking and only tracks two states (pending, claimed). When an agent finishes a story, the record disappears — there's no "completed" state and no history.

Mark can't answer basic questions about agent work:
- What stories did Dan complete this week?
- How long was STORY-094 in the queue before it was claimed?
- Which agent handles the most medium-scope stories?
- What's the average time from enqueue to completion?

The JSON file also can't support filtering, pagination, or concurrent writes beyond advisory locking. As the fleet scales, this becomes a bottleneck.

## Desired Outcome

Replace JSON file persistence with PostgreSQL. Add a `completed` status so stories flow through `pending → claimed → completed`. Persist all records permanently so Mark can:

1. **View history** — all dispatched stories with timestamps, agent, duration, cost
2. **Filter & search** — by agent, date range, story ID, scope, status
3. **Track metrics** — time-in-queue, time-to-complete, agent throughput
4. **Inform decisions** — use historical data to adjust skills, workloads, and dispatch strategy

## Users

- **Mark** — views dispatch history in dashboard, filters by agent/date, uses insights to update skills and agent assignments
- **Agents (Dan, Derrick)** — unchanged polling/claim workflow, now call a "complete" endpoint when done
- **Ops Console** — reads/writes PostgreSQL instead of JSON file

## Success Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| AC-1 | `dispatch_items` table in PostgreSQL with columns: id, story_id, repo, scope, prompt, status (pending/claimed/completed), enqueued_at, enqueued_by, claimed_by, claimed_at, completed_at, cost_usd, turns, duration_seconds | Migration runs, table exists |
| AC-2 | `POST /api/dispatch` writes to DB instead of JSON | API test: enqueue → row in DB with status=pending |
| AC-3 | `GET /api/dispatch/queue` reads from DB | API test: returns pending + claimed items |
| AC-4 | `GET /api/dispatch/next` reads oldest pending from DB | API test: returns FIFO order |
| AC-5 | `POST /api/dispatch/claim/{story_id}` updates status to claimed in DB | API test: status transitions |
| AC-6 | `POST /api/dispatch/complete/{story_id}` sets status=completed with cost/turns/duration | New endpoint, API test |
| AC-7 | `GET /api/dispatch/history` returns completed stories with filtering | New endpoint: ?agent=dan&since=2026-04-01&limit=50 |
| AC-8 | `DELETE /api/dispatch/queue/{story_id}` sets status=cancelled (soft delete) | API test: no hard deletes |
| AC-9 | Stale claim recovery reads/writes DB instead of JSON | Unit test: recover moves claimed→pending in DB |
| AC-10 | Dispatch poller works unchanged (same API contract) | Integration test: poller claims from DB-backed queue |
| AC-11 | Frontend shows completed tab with history table | React component with date filter, agent filter, pagination |
| AC-12 | Dashboard loads without 401 errors | Entra ID SSO works (separate from this story but prerequisite) |
| AC-13 | JSON file fallback removed — single source of truth is PostgreSQL | No references to dispatch-queue.json remain |
| AC-14 | Agent calls complete endpoint after SDK session ends | dispatch_poller.py updated to POST /complete on [DONE] |
| AC-15 | Teams-dispatched work auto-registers in dispatch queue | Hermes gateway POSTs to /api/dispatch when starting any SDK session from a Teams message, so all agent work is visible in the queue regardless of source |
| AC-16 | `source` column on dispatch_items tracks origin (queue, teams, ssh) | DB column + API field, filterable in history |

## Architecture

### Database

PostgreSQL is already running on Dan's VM (`postgresql@16-main`). Add a new database `ops_console` (or use existing if one exists).

**Table: `dispatch_items`**

```sql
CREATE TABLE dispatch_items (
    id              SERIAL PRIMARY KEY,
    story_id        VARCHAR(20) NOT NULL,
    repo            VARCHAR(200) NOT NULL,
    scope           VARCHAR(10) NOT NULL DEFAULT 'small',
    prompt          TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enqueued_by     VARCHAR(50) NOT NULL,
    claimed_by      VARCHAR(50),
    claimed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    cost_usd        NUMERIC(10, 4),
    turns           INTEGER,
    duration_seconds INTEGER,
    source          VARCHAR(20) NOT NULL DEFAULT 'queue',
    error           BOOLEAN DEFAULT FALSE,
    error_message   TEXT,
    CONSTRAINT valid_status CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled')),
    CONSTRAINT valid_source CHECK (source IN ('queue', 'teams', 'ssh', 'manual'))
);

CREATE INDEX idx_dispatch_status ON dispatch_items(status);
CREATE INDEX idx_dispatch_agent ON dispatch_items(claimed_by);
CREATE INDEX idx_dispatch_story ON dispatch_items(story_id);
CREATE INDEX idx_dispatch_enqueued ON dispatch_items(enqueued_at);
```

### Service Layer

Replace `DispatchQueueService` (JSON + fcntl) with `DispatchDBService` using `asyncpg` for async PostgreSQL access. Same method signatures so routes change minimally.

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/dispatch/complete/{story_id}` | Mark story as completed with cost/turns/duration |
| `GET` | `/api/dispatch/history` | Query completed/cancelled stories with filters |

### History Query Parameters

| Param | Type | Description |
|-------|------|-------------|
| `agent` | string | Filter by claimed_by agent name |
| `since` | ISO datetime | Stories completed after this date |
| `until` | ISO datetime | Stories completed before this date |
| `status` | string | Filter by status (completed, cancelled, all) |
| `story_id` | string | Search by story ID (partial match) |
| `limit` | int | Max results (default 50, max 200) |
| `offset` | int | Pagination offset |

### Teams Auto-Register Flow

When hermes receives a Teams message that triggers an SDK session, it should self-register in the dispatch queue so ALL agent work is visible regardless of source:

```
1. Hermes receives Teams message with story assignment
2. Before starting SDK: POST /api/dispatch with source="teams"
   (If story already in queue from a prior enqueue, skip — just claim it)
3. POST /api/dispatch/claim/{story_id} with agent name
4. Run SDK session as normal
5. On [DONE]: POST /api/dispatch/complete/{story_id} with cost/turns/duration
```

This means `/fleet` and the dashboard always show the complete picture — queue-dispatched AND Teams-dispatched work in one view.

### Agent Complete Flow

After `claude_sdk_tool.py` emits `[DONE]`, the dispatch poller calls:

```
POST /api/dispatch/complete/{story_id}
Body: { "cost_usd": 1.42, "turns": 42, "duration_seconds": 229, "error": false }
```

### Frontend Changes

- Add "History" tab to DispatchQueue component
- History table: story_id, agent, repo, scope, duration, cost, completed_at
- Filters: agent dropdown, date range picker, status toggle
- Pagination: 50 per page
- Existing pending/claimed views stay the same, read from same API

## Files to Create/Modify

| File | Action | Est. Lines |
|------|--------|-----------|
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | **Create** — async PostgreSQL service | ~200 |
| `tech_dev_agents/ops_console/services/dispatch_service.py` | **Remove** — replaced by DB service | -143 |
| `tech_dev_agents/ops_console/routes/dispatch.py` | **Modify** — add complete + history endpoints, use DB service | ~80 |
| `tech_dev_agents/ops_console/models/responses.py` | **Modify** — add COMPLETED/CANCELLED status, history models | ~40 |
| `tech_dev_agents/ops_console/config.py` | **Modify** — add database_url setting | ~5 |
| `tech_dev_agents/ops_console/main.py` | **Modify** — init DB pool in lifespan, update recovery task | ~30 |
| `sql/001_dispatch_items.sql` | **Create** — migration script | ~30 |
| `deployment/hermes/dispatch_poller.py` | **Modify** — call /complete endpoint on [DONE] | ~30 |
| `frontend/src/types/api.ts` | **Modify** — add completed status, history types | ~20 |
| `frontend/src/components/DispatchQueue.tsx` | **Modify** — add history tab | ~100 |
| `frontend/src/hooks/useDispatchQueue.ts` | **Modify** — add history query hook | ~20 |
| `tests/ops_console/test_dispatch_db_service.py` | **Create** — DB service tests | ~250 |
| `tests/ops_console/test_routes_dispatch.py` | **Modify** — update for DB-backed endpoints | ~100 |

**Total estimated new/modified lines:** ~905

## Dependencies

- **PostgreSQL 16** — already running on Dan's VM (confirmed via `systemctl`)
- **asyncpg** — async PostgreSQL driver (add to requirements)
- **STORY-026** (Central Dispatch Queue) — Complete, all API endpoints exist
- **STORY-027** (Auto-Pickup) — Complete, pollers deployed and running

## Risks

| Risk | Mitigation |
|------|-----------|
| PostgreSQL on Dan's VM only — ops console on separate VM | Use PostgreSQL connection string over network, or migrate DB to ops console VM |
| Migration breaks existing queue state | One-time migration script imports any pending/claimed items from JSON before cutover |
| asyncpg dependency conflicts | Pin version, test in ops console venv |
| Poller /complete call fails (network) | Best-effort with retry; stale recovery catches stuck claims |
| Dashboard SSO still broken (AC-12) | Separate issue — dashboard history works via API key for now |

## Open Questions

1. **DB location** — Should PostgreSQL run on the ops console VM (137.116.63.176) or continue using Dan's VM? Network latency vs. separation of concerns.
2. **Retention** — How long to keep completed records? Forever, or prune after N months?
3. **Cost data source** — Should the poller extract cost from Loki `[DONE]` logs, or does the agent explicitly report it?
