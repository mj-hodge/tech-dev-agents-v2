# STORY-496: Dashboard Fixes — Feature Spec

## Overview

Fix 6 critical dashboard issues from the STORY-480 review: missing quota visibility, unclear cost labels, wrong presence/status, zeroed fleet counts, missing "In Review" queue status, and meaningless "Busy" label.

---

## 1. Database Changes

### Migration: `sql/002_in_review_status.sql`

```sql
-- STORY-496: Add in_review status for dispatch items
-- Safe to run on live DB — additive only (new constraint value + new column)

-- Step 1: Drop and recreate constraint with in_review
ALTER TABLE dispatch_items
    DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE dispatch_items
    ADD CONSTRAINT valid_status
        CHECK (status IN ('pending', 'claimed', 'in_review', 'completed', 'cancelled', 'failed'));

-- Step 2: Add review_started_at column
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS review_started_at TIMESTAMPTZ;

-- Step 3: Partial index for in_review queries
CREATE INDEX IF NOT EXISTS idx_dispatch_in_review
    ON dispatch_items(status) WHERE status = 'in_review';
```

**Rollback:**
```sql
-- Revert: remove in_review from constraint, drop column
UPDATE dispatch_items SET status = 'claimed' WHERE status = 'in_review';
ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE dispatch_items ADD CONSTRAINT valid_status
    CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled', 'failed'));
ALTER TABLE dispatch_items DROP COLUMN IF EXISTS review_started_at;
DROP INDEX IF EXISTS idx_dispatch_in_review;
```

---

## 2. API Changes

### 2.1 New Endpoint: `GET /api/agents/{name}/quota`

**Purpose:** Return Claude Code token quota status for an agent.

**Request:** `GET /api/agents/{name}/quota`

**Response (200):**
```json
{
  "percent_used": 45.2,
  "reset_in_minutes": 187,
  "block_start": "15:00 UTC",
  "block_end": "20:00 UTC",
  "remaining_tokens": 548000,
  "p90_limit": 1000000,
  "sessions_in_block": 3
}
```

**Response (200, no data):**
```json
{
  "percent_used": null,
  "reset_in_minutes": null,
  "block_start": null,
  "block_end": null,
  "remaining_tokens": null,
  "p90_limit": null,
  "sessions_in_block": null
}
```

**Implementation:** SSH to agent VM, run `sudo -u hermes python3 /opt/agent/quota_check.py`, parse JSON. Cache 5 minutes per agent. Return null fields on SSH failure.

### 2.2 New Endpoint: `POST /api/dispatch/review/{story_id}`

**Purpose:** Transition a claimed dispatch item to in_review after PR creation.

**Request:** `POST /api/dispatch/review/{story_id}`

**Response (200):**
```json
{
  "story_id": "STORY-496",
  "status": "in_review",
  "review_started_at": "2026-04-21T14:30:00Z"
}
```

**Error (409):** Item not in `claimed` state.

### 2.3 Modified Endpoint: `GET /api/dispatch/queue`

**Current response:** `{"pending": [...], "claimed": [...]}`

**New response:**
```json
{
  "pending": [...],
  "in_progress": [...],
  "in_review": [...]
}
```

Where `in_progress` = items with status `claimed`, `in_review` = items with status `in_review`. Rename is frontend-friendly; DB column stays `claimed`.

### 2.4 Modified Endpoint: `GET /api/fleet`

**Changes to `FleetOverviewResponse`:**
- `active_agents`: count where dispatch state is WORKING or IDLE (poller running, not rate-limited)
- `busy_agents`: count where dispatch state is WORKING (SDK actively running)
- `stories_in_progress`: count of `claimed` + `in_review` items in dispatch DB
- `queued_stories`: count of `pending` items in dispatch DB
- Label fields: no backend change needed (frontend labels the values)

**Changes to `FleetAgentSummary`:**
- Add `quota: QuotaInfo | None` — embedded quota per agent
- Add `rate_limited_until: str | None` — ISO timestamp when rate limit resets
- Add `current_story_id: str | None` — from Loki dispatch logs
- Add `work_duration_seconds: int | None` — seconds since SDK started current story

### 2.5 Modified Endpoint: `GET /api/agents` and `GET /api/agents/{name}`

**Changes to `AgentSummary`:**
- Add `quota: QuotaInfo | None`
- Add `rate_limited_until: str | None`
- Add `current_story_id: str | None`
- Add `work_duration_seconds: int | None`

---

## 3. Model Changes

### 3.1 New Model: `QuotaInfo` (responses.py)

```python
class QuotaInfo(BaseModel):
    percent_used: float | None = None      # 0-100, null if no active block
    reset_in_minutes: int | None = None
    block_start: str | None = None         # "15:00 UTC"
    block_end: str | None = None           # "20:00 UTC"
    remaining_tokens: int | None = None
    p90_limit: int | None = None
    sessions_in_block: int | None = None
```

### 3.2 Enum Updates (responses.py)

```python
class AgentStatusEnum(str, Enum):
    ONLINE = "online"           # existing
    IDLE = "idle"               # existing
    STUCK = "stuck"             # existing
    OFFLINE = "offline"         # existing
    UNREACHABLE = "unreachable" # existing
    RATE_LIMITED = "rate_limited"  # NEW
    WORKING = "working"           # NEW

class DispatchStatusEnum(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_REVIEW = "in_review"       # NEW
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
```

### 3.3 Extended Models (responses.py)

Add to `AgentSummary`, `AgentDetailResponse`, `FleetAgentSummary`:
```python
quota: QuotaInfo | None = None
rate_limited_until: str | None = None
current_story_id: str | None = None
work_duration_seconds: int | None = None
```

---

## 4. File-by-File Change Plan

### 4.1 `tech_dev_agents/ops_console/models/responses.py`

| Change | Detail |
|--------|--------|
| Add `QuotaInfo` class | Pydantic model with 7 nullable fields (see 3.1) |
| Add `RATE_LIMITED`, `WORKING` to `AgentStatusEnum` | Two new enum values |
| Add `IN_REVIEW` to `DispatchStatusEnum` | One new enum value |
| Extend `AgentSummary` | +4 optional fields: quota, rate_limited_until, current_story_id, work_duration_seconds |
| Extend `FleetAgentSummary` | Same +4 fields |
| Extend `AgentDetailResponse` | Same +4 fields |

### 4.2 `tech_dev_agents/ops_console/services/loki_client.py`

| Change | Detail |
|--------|--------|
| Add `DispatchState` dataclass | Fields: status, story_id, phase_num, phase_name, started_at, rate_limit_until |
| Add `query_dispatch_state()` method | Query `{job="hermes-gateway"} \|= "[DISPATCH]"` with agent filter. Parse latest log line. Return `DispatchState`. Lookback: 1 hour. |
| Add regex constants | `_DISPATCH_PHASE_RE = r'\[DISPATCH\] Phase (\d+) \(([^)]+)\) for (STORY-\d+)'` |
| | `_DISPATCH_RATE_LIMITED_RE = r'\[DISPATCH\].*RATE LIMITED'` |
| | `_DISPATCH_BUSY_RE = r'\[DISPATCH\].*busy, skipping'` |
| | `_DISPATCH_IDLE_RE = r'\[DISPATCH\].*queue empty'` |
| Add `query_fleet_dispatch_state()` | Single Loki query for all agents, returns `dict[str, DispatchState]`. More efficient than per-agent queries. |

### 4.3 `tech_dev_agents/ops_console/services/agent_service.py`

| Change | Detail |
|--------|--------|
| Update `_poll_agent_health()` | After existing health check, call `loki_client.query_dispatch_state()` to get dispatch status. Merge into `AgentHealthSnapshot`. |
| Add fields to `AgentHealthSnapshot` | `dispatch_status: str \| None`, `current_story_id: str \| None`, `current_phase: str \| None`, `phase_started_at: str \| None`, `rate_limited_until: str \| None` |
| Update `get_all_health()` | Use `query_fleet_dispatch_state()` (batch) instead of per-agent calls. Map results into snapshots. |

### 4.4 `tech_dev_agents/ops_console/routes/_status.py`

| Change | Detail |
|--------|--------|
| Extend `_STATUS_MAP` | Add `"rate_limited": AgentStatusEnum.RATE_LIMITED`, `"working": AgentStatusEnum.WORKING` |

### 4.5 `tech_dev_agents/ops_console/routes/agents.py`

| Change | Detail |
|--------|--------|
| Add `get_agent_quota()` route | `GET /agents/{name}/quota`. SSH to agent VM via `asyncio.create_subprocess_exec("ssh", ...)`. Parse JSON output. Cache 5 min via `functools.lru_cache` or `cachetools.TTLCache`. |
| Update `list_agents()` | Include dispatch state fields in `AgentSummary` (from enriched health snapshot). |
| Update `get_agent_detail()` | Include quota (call quota endpoint internally) and dispatch state in response. |

### 4.6 `tech_dev_agents/ops_console/routes/fleet.py`

| Change | Detail |
|--------|--------|
| Update `_fleet_overview_inner()` | Use `query_fleet_dispatch_state()` for batch status. Use `dispatch_db_service.pending_count()` + count of claimed for accurate story counts. |
| Fix `active_agents` | Count agents where dispatch_state.status in (WORKING, IDLE). |
| Fix `busy_agents` | Count agents where dispatch_state.status == WORKING. |
| Fix `stories_in_progress` | Count dispatch_items where status in ('claimed', 'in_review'). |
| Fix `queued_stories` | Count dispatch_items where status = 'pending'. |
| Add quota to `FleetAgentSummary` | Parallel SSH calls to each agent for quota; 5-min cache. |

### 4.7 `tech_dev_agents/ops_console/services/dispatch_db_service.py`

| Change | Detail |
|--------|--------|
| Add `transition_to_review()` | `UPDATE dispatch_items SET status='in_review', review_started_at=NOW() WHERE story_id=$1 AND status='claimed' RETURNING *`. Raise `InvalidTransitionError` if not claimed. |
| Update `list_queue()` | Return three groups: `pending` (status='pending'), `in_progress` (status='claimed'), `in_review` (status='in_review'). |
| Update `complete()` | Accept `in_review` as valid source state: `WHERE status IN ('claimed', 'pending', 'in_review')`. |
| Add `in_progress_count()` | `SELECT COUNT(*) FROM dispatch_items WHERE status IN ('claimed', 'in_review')`. |

### 4.8 `tech_dev_agents/ops_console/routes/dispatch.py`

| Change | Detail |
|--------|--------|
| Add `review_story()` route | `POST /dispatch/review/{story_id}`. Calls `dispatch_db_service.transition_to_review()`. Pushes presence update (STORY-304 pattern). Returns updated item. |
| Update `list_queue()` route | Return new three-group response format. |

### 4.9 `sql/002_in_review_status.sql`

| Change | Detail |
|--------|--------|
| New file | Migration script (see Section 1). Adds `in_review` to constraint, `review_started_at` column, partial index. |

### 4.10 `frontend/src/types/api.ts`

| Change | Detail |
|--------|--------|
| Add `QuotaInfo` interface | `{ percent_used: number \| null; reset_in_minutes: number \| null; block_start: string \| null; block_end: string \| null; remaining_tokens: number \| null; p90_limit: number \| null; sessions_in_block: number \| null }` |
| Update `AgentSummary` | Add `quota?: QuotaInfo \| null`, `rate_limited_until?: string \| null`, `current_story_id?: string \| null`, `work_duration_seconds?: number \| null`. Extend status union: add `'rate_limited' \| 'working'`. |
| Update `DispatchItem` | Add `'in_review' \| 'failed'` to status union. Add `review_started_at?: string \| null`. |
| Update `DispatchQueueResponse` | Change to `{ pending: DispatchItem[]; in_progress: DispatchItem[]; in_review: DispatchItem[] }`. |

### 4.11 `frontend/src/components/AgentCard.tsx`

| Change | Detail |
|--------|--------|
| Add quota progress bar | Below cost line. Width = `percent_used%`. Color: green (<50%), yellow (50-80%), red (>80%). Text: "{pct}% -- resets in {minutes}m". Hide if quota is null. |
| Replace "Busy" label | If `agent.current_story_id` and `agent.work_duration_seconds`: show "STORY-XXX Phase N -- {duration}". If idle: "Idle -- last completed STORY-YYY ({time} ago)". If rate limited: "Rate Limited -- resets {time}". |
| Label cost | Change from `formatCurrency(agent.today_foundry_usd)` to `"Foundry: " + formatCurrency(agent.today_foundry_usd)`. |
| Remove SDK cost from visible display | SDK cost already hidden in card; verify no regression. |

### 4.12 `frontend/src/components/FleetOverviewBar.tsx`

| Change | Detail |
|--------|--------|
| Label "Total Spend" | Change to "Azure Foundry Spend" in the stat box title. |
| Counts already from API | Verify `data.active_agents`, `data.busy_agents`, `data.stories_in_progress` display correctly (backend fix makes them accurate). |
| Monthly spend label | Change "Monthly Spend" to "Monthly Foundry" or add subtitle "(Foundry only)". |

### 4.13 `frontend/src/components/StatusBadge.tsx`

| Change | Detail |
|--------|--------|
| Add status configs | `rate_limited: { dot: 'bg-amber-500', text: 'text-amber-400' }`, `working: { dot: 'bg-green-500', text: 'text-green-400' }`. |
| Add `detail` prop | Optional string displayed after status label. Used for "resets Apr 23, 7pm UTC". |
| Type the status prop | `status: 'active' \| 'online' \| 'idle' \| 'error' \| 'offline' \| 'rate_limited' \| 'working'` |

### 4.14 `frontend/src/components/DispatchQueue.tsx`

| Change | Detail |
|--------|--------|
| Change tab state | From `'queue' \| 'history'` to `'pending' \| 'in_progress' \| 'in_review' \| 'history'`. |
| Render three queue tabs | Pending: `data.pending`, In Progress: `data.in_progress`, In Review: `data.in_review`. |
| Status badges | pending -> "Waiting" (yellow), claimed/in_progress -> "Working" (blue), in_review -> "In Review" (purple). |
| In Review tab extras | Show PR number/link if available (from DispatchItem.pr_number). |

---

## 5. Data Flow Diagrams

### Quota Flow
```
AgentCard -> useFleet() -> GET /api/fleet
                              |
                              v
                         fleet.py._fleet_overview_inner()
                              |
                              v (parallel per agent)
                         SSH -> agent VM -> quota_check.py
                              |
                              v
                         QuotaInfo in FleetAgentSummary
```

### Status Flow
```
AgentCard -> useFleet() -> GET /api/fleet
                              |
                              v
                         fleet.py._fleet_overview_inner()
                              |
                              v
                         loki_client.query_fleet_dispatch_state()
                              |
                              v
                         Loki: {job="hermes-gateway"} |= "[DISPATCH]"
                              |
                              v
                         DispatchState per agent
                              |
                              v
                         AgentStatusEnum + work detail in FleetAgentSummary
```

### Queue Status Flow
```
DispatchQueue -> useDispatchQueue() -> GET /api/dispatch/queue
                                          |
                                          v
                                     dispatch_db_service.list_queue()
                                          |
                                          v
                                     SELECT ... GROUP BY status
                                          |
                                          v
                                     { pending: [], in_progress: [], in_review: [] }
```

### In-Review Transition Flow
```
SDLC Phase Runner (after PR creation)
    |
    v
POST /api/dispatch/review/{story_id}
    |
    v
dispatch_db_service.transition_to_review()
    |
    v
UPDATE dispatch_items SET status='in_review', review_started_at=NOW()
    |
    v
push_presence() -> Teams (DND -> Available or similar)
```

---

## 6. Caching Strategy

| Data | TTL | Scope | Rationale |
|------|-----|-------|-----------|
| Agent quota | 5 min | Per agent | Quota changes slowly within 5-hour blocks |
| Loki dispatch state | 30 sec | Fleet-wide | Aligned with existing health cache TTL |
| Fleet overview | 10 sec | Global | Existing refetchInterval from frontend |
| Dispatch queue | 5 sec | Global | Existing refetchInterval from frontend |
| Agent health snapshot | 30 sec | Per agent | Existing cache TTL |

---

## 7. Error Handling

| Failure | Behavior | User Impact |
|---------|----------|-------------|
| SSH to agent VM fails | Return `quota: null` | Quota bar hidden for that agent |
| Loki unreachable | Fall back to existing status logic (last_activity timestamp) | Status shows online/offline but not working/rate_limited |
| DB migration not applied | `in_review` INSERT fails with constraint violation | Phase runner logs error; item stays `claimed` |
| Agent VM has no quota_check.py | SSH command returns error | `quota: null`; same as SSH failure |
| Loki returns no dispatch logs | Status defaults to OFFLINE if no recent activity, IDLE if recent non-dispatch activity | Accurate enough as fallback |

---

## 8. Rollback Strategy

### Database
Run the rollback SQL from Section 1. All `in_review` items revert to `claimed`.

### Backend
Revert the backend commits. Old code doesn't know about `in_review` or new fields; nullable fields in responses are backward-compatible. Frontend with old code ignores extra fields.

### Frontend
Revert frontend commits. Old components don't read `quota`, `rate_limited_until`, or `in_review` items — they display as before (with the original 6 issues).

### Feature Flag
Not needed per seed.md — these are fixes to existing features. However, if partial rollback is needed:
- Quota endpoint can be disabled by removing the route (no other code depends on it)
- In-review transition can be skipped by phase runner (items stay `claimed`)
- Status enrichment can be disabled by returning `None` from `query_dispatch_state()`

---

## 9. Testing Boundaries

Tests are designed in `test-design.md` (Phase 7). Key coverage areas:

- **Backend unit:** QuotaInfo model validation, DispatchState parsing (each regex pattern), status enum mapping, transition_to_review happy/error paths, list_queue three-group response
- **Backend integration:** Fleet overview with mock Loki returning various dispatch states, quota endpoint with mock SSH
- **Frontend unit:** AgentCard quota bar rendering (green/yellow/red/null), StatusBadge with all states, DispatchQueue three-tab filtering, FleetOverviewBar count display
- **Frontend integration:** Dashboard renders correctly with mock API returning all new fields

---

## 10. Implementation Order

1. **DB migration** — `sql/002_in_review_status.sql` (independent, run first)
2. **Models** — `responses.py` (QuotaInfo, enum updates, field additions)
3. **Loki client** — `loki_client.py` (DispatchState, query_dispatch_state, regex patterns)
4. **Agent service** — `agent_service.py` (enrich health with dispatch state)
5. **Status helper** — `_status.py` (add rate_limited/working mappings)
6. **Dispatch DB** — `dispatch_db_service.py` (transition_to_review, list_queue groups)
7. **Routes** — `agents.py` (quota endpoint), `fleet.py` (count fixes), `dispatch.py` (review route, queue response)
8. **Frontend types** — `api.ts` (QuotaInfo, status unions, queue response)
9. **Frontend components** — StatusBadge, AgentCard, FleetOverviewBar, DispatchQueue (in dependency order)

Steps 1-2 are independent. Steps 3-5 depend on 2. Steps 6-7 depend on 2+3. Steps 8-9 depend on 7.
