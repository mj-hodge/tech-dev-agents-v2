# Feature Specification: STORY-576 — Azure Foundry Cost Panel on Dashboard

| Field | Value |
|-------|-------|
| Story | STORY-576 |
| Phase | 6 — Design (Lite) |
| Author | Claude (Phase-6 agent) |
| Date | 2026-04-24 |
| Scope | Medium |
| Approach | New dedicated service + Postgres cache (from analysis.md §5 Option A) |

---

## 1. Overview

Add a fleet-wide Foundry cost panel to the main dashboard showing 7-day daily spend stacked by model deployment (Opus / Sonnet / Haiku). Data is cached in Postgres and refreshed every 2 hours by a standalone cron script. A warning banner fires when today's spend exceeds $200.

---

## 2. Domain Boundaries

| Domain | Responsibility | Key Files |
|--------|----------------|-----------|
| **Foundry Cost Data** | Query Azure Cost Management API, classify ResourceIds into model buckets, persist to Postgres | `foundry_cost_service.py`, `refresh_foundry_cost.py` |
| **API Surface** | Serve cached cost data to frontend, compute cache age | `routes/foundry_cost.py` |
| **Dashboard UI** | Render stacked area chart + warning banner | `FoundryCostPanel.tsx`, `useFoundryCost.ts` |
| **Storage** | Persistent 7-day cache table | `009_foundry_cost_daily.sql` |

**Interactions:**
- Cron script → `foundry_cost_service.refresh_from_azure()` → Postgres (write)
- API route → `foundry_cost_service.get_daily_by_model()` → Postgres (read)
- Frontend hook → API route → renders panel

No interaction with existing `CostService` or `AzureCostClient` — this is a separate concern (fleet-wide model-level breakdown vs per-agent resource-group breakdown).

---

## 3. Database Schema

### Migration: `scripts/migrations/009_foundry_cost_daily.sql`

```sql
-- STORY-576: Fleet-wide Foundry cost cache, bucketed by model deployment.
-- Populated by refresh_foundry_cost.py cron (every 2h).
-- Read by GET /api/fleet/foundry-cost.

CREATE TABLE IF NOT EXISTS foundry_cost_daily (
  usage_date DATE        PRIMARY KEY,
  opus_usd   NUMERIC(10,2) NOT NULL DEFAULT 0,
  sonnet_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
  haiku_usd  NUMERIC(10,2) NOT NULL DEFAULT 0,
  other_usd  NUMERIC(10,2) NOT NULL DEFAULT 0,
  fetched_at TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Index on fetched_at for cache-age queries (single row lookup by max).
CREATE INDEX IF NOT EXISTS idx_foundry_cost_daily_fetched
  ON foundry_cost_daily (fetched_at DESC);
```

**Design rationale:**
- `usage_date` as PK — one row per day, UPSERT-safe (idempotent refresh).
- `NUMERIC(10,2)` — matches existing `DailyCost` precision.
- `fetched_at` — updated on each refresh; used to compute `cache_age_seconds` in the API response.
- No FK dependencies — table is fully isolated; safe additive migration.

---

## 4. Backend Service

### File: `tech_dev_agents/ops_console/services/foundry_cost_service.py`

```python
"""Fleet-wide Foundry cost by model deployment (STORY-576)."""

from __future__ import annotations
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# --- Response Models ---

class DailyFoundryCostByModel(BaseModel):
    """Single day's Foundry cost split by model."""
    date: str               # YYYY-MM-DD
    opus_usd: float
    sonnet_usd: float
    haiku_usd: float
    other_usd: float
    total_usd: float        # computed: opus + sonnet + haiku + other


class FoundryCostResponse(BaseModel):
    """Response for GET /api/fleet/foundry-cost."""
    daily: list[DailyFoundryCostByModel]
    fetched_at: str | None   # ISO 8601 timestamp of last refresh
    cache_age_seconds: int   # seconds since last refresh


# --- Model Classification ---

# Patterns match Azure ResourceId segments like:
#   /providers/Microsoft.MachineLearningServices/.../claude-opus-4-6-...
#   /providers/Microsoft.MachineLearningServices/.../claude-sonnet-4-5-...
#   /providers/Microsoft.MachineLearningServices/.../claude-haiku-3-5-...
_MODEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"claude-opus", re.IGNORECASE), "opus"),
    (re.compile(r"claude-sonnet", re.IGNORECASE), "sonnet"),
    (re.compile(r"claude-haiku", re.IGNORECASE), "haiku"),
]


def classify_resource_id(resource_id: str) -> str:
    """Classify an Azure ResourceId into a model bucket.

    Returns one of: "opus", "sonnet", "haiku", "other".
    """
    for pattern, bucket in _MODEL_PATTERNS:
        if pattern.search(resource_id):
            return bucket
    return "other"


# --- Service Class ---

class FoundryCostService:
    """Read fleet-wide Foundry cost from Postgres cache.

    Write path (refresh_from_azure) is called by the standalone cron script.
    Read path (get_daily_by_model) is called by the API route handler.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self._pool = db_pool

    async def get_daily_by_model(self, days: int = 7) -> FoundryCostResponse:
        """Read cached daily cost from Postgres.

        Returns up to `days` rows ordered by date ascending.
        """
        now = datetime.now(timezone.utc)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT usage_date, opus_usd, sonnet_usd, haiku_usd, other_usd, fetched_at
                FROM foundry_cost_daily
                WHERE usage_date >= CURRENT_DATE - $1::int
                ORDER BY usage_date ASC
                """,
                days,
            )

        if not rows:
            return FoundryCostResponse(
                daily=[],
                fetched_at=None,
                cache_age_seconds=0,
            )

        latest_fetched = max(r["fetched_at"] for r in rows)
        cache_age = int((now - latest_fetched).total_seconds())

        daily = [
            DailyFoundryCostByModel(
                date=r["usage_date"].isoformat(),
                opus_usd=float(r["opus_usd"]),
                sonnet_usd=float(r["sonnet_usd"]),
                haiku_usd=float(r["haiku_usd"]),
                other_usd=float(r["other_usd"]),
                total_usd=float(
                    r["opus_usd"] + r["sonnet_usd"] + r["haiku_usd"] + r["other_usd"]
                ),
            )
            for r in rows
        ]

        return FoundryCostResponse(
            daily=daily,
            fetched_at=latest_fetched.isoformat(),
            cache_age_seconds=max(0, cache_age),
        )

    @staticmethod
    async def upsert_daily_costs(
        conn: asyncpg.Connection,
        rows: list[dict[str, Any]],
    ) -> int:
        """UPSERT classified cost rows into foundry_cost_daily.

        Each dict in `rows` must have keys:
          usage_date (date), opus_usd (float), sonnet_usd, haiku_usd, other_usd

        Returns the number of rows upserted.
        """
        if not rows:
            return 0

        count = 0
        for row in rows:
            await conn.execute(
                """
                INSERT INTO foundry_cost_daily (usage_date, opus_usd, sonnet_usd, haiku_usd, other_usd, fetched_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (usage_date) DO UPDATE SET
                    opus_usd   = EXCLUDED.opus_usd,
                    sonnet_usd = EXCLUDED.sonnet_usd,
                    haiku_usd  = EXCLUDED.haiku_usd,
                    other_usd  = EXCLUDED.other_usd,
                    fetched_at = EXCLUDED.fetched_at
                """,
                row["usage_date"],
                row["opus_usd"],
                row["sonnet_usd"],
                row["haiku_usd"],
                row["other_usd"],
            )
            count += 1
        return count
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Separate service file (not extending `cost_service.py`) | Different concern: fleet-wide model bucketing vs per-agent resource-group bucketing |
| `classify_resource_id()` as a module-level function | Pure function, easy to unit test in isolation |
| Case-insensitive regex matching | Future-proofs against Azure naming changes |
| `@staticmethod` for `upsert_daily_costs` | Called by both the service and the standalone cron script with different connections |
| UPSERT semantics | Idempotent — safe to re-run cron at any time |

---

## 5. API Route

### File: `tech_dev_agents/ops_console/routes/foundry_cost.py`

**Endpoint:** `GET /api/fleet/foundry-cost`

| Aspect | Detail |
|--------|--------|
| Method | GET |
| Path | `/fleet/foundry-cost` |
| Query params | `days` (int, optional, default=7, max=30) |
| Auth | `X-API-Key` header (existing `verify_api_key` dependency) |
| Idempotent | Yes (read-only) |
| Missing resource | Returns `{ daily: [], fetched_at: null, cache_age_seconds: 0 }` — no 404 (cache simply hasn't been populated yet) |
| Unknown fields | N/A (GET, no request body) |

### Response Schema

```json
{
  "daily": [
    {
      "date": "2026-04-18",
      "opus_usd": 1486.23,
      "sonnet_usd": 0.00,
      "haiku_usd": 0.00,
      "other_usd": 12.50,
      "total_usd": 1498.73
    },
    {
      "date": "2026-04-19",
      "opus_usd": 1102.00,
      "sonnet_usd": 45.20,
      "haiku_usd": 8.10,
      "other_usd": 5.00,
      "total_usd": 1160.30
    }
  ],
  "fetched_at": "2026-04-24T14:00:01Z",
  "cache_age_seconds": 3601
}
```

### Error Responses

| Condition | Status | Behavior |
|-----------|--------|----------|
| DB pool unavailable | 503 | `{ "type": "/problems/service-unavailable", "title": "Service Unavailable", "status": 503, "detail": "Cost data storage unavailable" }` |
| Invalid `days` param | 422 | FastAPI validation (automatic) |
| Empty cache | 200 | `{ "daily": [], "fetched_at": null, "cache_age_seconds": 0 }` — fail-open, not an error |

### Route Registration

In `main.py`:
```python
from tech_dev_agents.ops_console.routes import foundry_cost
# ...
app.include_router(foundry_cost.router, prefix="/api")
```

---

## 6. Cron Refresh Script

### File: `deployment/ops-console/scripts/refresh_foundry_cost.py`

**Schedule:** `0 */2 * * *` (every 2 hours)

**Behavior:**
1. Load `DefaultAzureCredential` (same as `main.py` pattern)
2. POST to Cost Management Query API with `ResourceId` grouping for last 8 days
3. Parse rows: `[cost, YYYYMMDD, resourceId, currency]`
4. Classify each ResourceId via `classify_resource_id()`
5. Aggregate by date → `{date: {opus, sonnet, haiku, other}}`
6. Connect to Postgres via `DATABASE_URL` env var
7. UPSERT aggregated rows via `FoundryCostService.upsert_daily_costs()`
8. Log: timestamp, rows upserted, total cost

**Azure Cost Management Query body:**
```json
{
  "type": "ActualCost",
  "timeframe": "Custom",
  "timePeriod": {
    "from": "<8 days ago>T00:00:00Z",
    "to": "<today>T23:59:59Z"
  },
  "dataset": {
    "granularity": "Daily",
    "aggregation": { "totalCost": { "name": "Cost", "function": "Sum" } },
    "grouping": [{ "type": "Dimension", "name": "ResourceId" }]
  }
}
```

**API Rate Limits:**
- Cost Management Query API: 30 requests per 5 minutes per subscription
- This script makes 1 request per 2 hours = well within limits
- On 429 response: log error, exit non-zero (cron retries next cycle)

**Failure mode:** If Azure API is unreachable or returns an error, the script logs the error and exits non-zero. Existing cached data remains valid in Postgres — the API route serves stale data with an increased `cache_age_seconds`. No fail-open risk.

---

## 7. Frontend Component

### File: `frontend/src/components/FoundryCostPanel.tsx`

**Props:**
```typescript
interface FoundryCostPanelProps {
  // No props — self-contained, fetches its own data via useFoundryCost hook
}
```

**Behavior:**
- Renders a Recharts stacked `AreaChart` with 3 series:
  - **Opus**: `#ef4444` (red) — matches severity/cost concern
  - **Sonnet**: `#eab308` (yellow)
  - **Haiku**: `#22c55e` (green)
- X-axis: date (last 7 days)
- Y-axis: USD ($)
- Stacked areas (same `stackId` pattern as `CostChart.tsx`)
- Header: "Foundry Cost (7 Day)" with total spend across the period
- Subtext: "Last updated: X minutes ago" from `cache_age_seconds`
- Fixed chart dimensions (same as `CostChart.tsx`: `width=700, height=300`) for JSDOM test compat

**Warning Banner:**
- Renders inline above the chart when today's `total_usd > 200`
- Style: red bg (`bg-red-900/50 border border-red-700`), warning icon, text: "Today's Foundry spend exceeds $200"
- Threshold: hardcoded at $200 (matches AGENT_PROVISIONING_LESSONS §14; future story can make configurable)

**Loading/Error States:**
- Loading: skeleton placeholder matching chart dimensions
- Error: "Failed to load Foundry cost data" message
- Empty data: "No cost data available" (same pattern as `CostChart.tsx`)

**Test IDs:**
- `data-testid="foundry-cost-panel"` — outer container
- `data-testid="foundry-cost-chart"` — chart area
- `data-testid="foundry-cost-warning"` — warning banner (when visible)
- `data-testid="foundry-cost-total"` — total spend display

### File: `frontend/src/hooks/useFoundryCost.ts`

```typescript
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export interface DailyFoundryCost {
  date: string;
  opus_usd: number;
  sonnet_usd: number;
  haiku_usd: number;
  other_usd: number;
  total_usd: number;
}

export interface FoundryCostData {
  daily: DailyFoundryCost[];
  fetched_at: string | null;
  cache_age_seconds: number;
}

export function useFoundryCost(days: number = 7) {
  return useQuery({
    queryKey: ['foundry-cost', days],
    queryFn: () => api.get<FoundryCostData>(`/api/fleet/foundry-cost?days=${days}`),
    staleTime: 300_000,       // 5 minutes — server data only updates every 2h
    refetchInterval: 300_000, // re-check every 5 minutes
  });
}
```

### File: `frontend/src/types/api.ts` (additions)

```typescript
// STORY-576: Fleet-wide Foundry cost by model deployment
export interface DailyFoundryCost {
  date: string;
  opus_usd: number;
  sonnet_usd: number;
  haiku_usd: number;
  other_usd: number;
  total_usd: number;
}

export interface FoundryCostResponse {
  daily: DailyFoundryCost[];
  fetched_at: string | null;
  cache_age_seconds: number;
}
```

### Dashboard Integration: `frontend/src/components/DashboardLayout.tsx`

Mount `FoundryCostPanel` below `BudgetGauge`:

```tsx
import { FoundryCostPanel } from './FoundryCostPanel';

// In the layout, after BudgetGauge:
<div className="flex gap-4 px-4 pt-4">
  <div className="flex-1 min-w-0">
    <AlertStatusPanel />
  </div>
  <div className="w-64 flex-shrink-0">
    <BudgetGauge />
  </div>
</div>
<FoundryCostPanel />  {/* STORY-576: Fleet-wide Foundry cost chart */}
```

---

## 8. Error Handling Design

| Layer | Error Source | Behavior | Logged | Exposed to Caller |
|-------|-------------|----------|--------|-------------------|
| API route | DB pool `None` | 503 JSON (RFC 7807) | Yes: `logger.error("DB pool unavailable")` | Generic "service unavailable" |
| API route | DB query fails | 503 JSON (RFC 7807) | Yes: full exception | Generic "cost data unavailable" |
| Cron script | Azure API 4xx/5xx | Exit non-zero, log error | Yes: status code + body | N/A (cron) |
| Cron script | Azure API timeout | Exit non-zero, log error | Yes: timeout details | N/A (cron) |
| Cron script | DB connect fails | Exit non-zero, log error | Yes: connection error | N/A (cron) |
| Frontend | API returns error | Show "Failed to load" message | Console warn | User sees fallback UI |
| Frontend | API returns empty `daily` | Show "No cost data available" | No | Informational message |

**What is NOT exposed:** Stack traces, database connection strings, Azure subscription IDs, internal paths.

**Fallback behavior:** Fail-closed for writes (cron exits non-zero on any error). Fail-open for reads (API serves stale cache; frontend shows data age warning).

---

## 9. Failure Modes

| Dependency | Unavailable Behavior | Rationale |
|------------|---------------------|-----------|
| **Postgres** (read path) | API returns 503 | Fail-closed — no data to serve |
| **Postgres** (write path) | Cron exits non-zero | Fail-closed — stale cache preserved |
| **Azure Cost Management API** | Cron exits non-zero; API serves stale cache | Fail-closed on write, fail-open on read (stale data better than no data) |
| **Azure credentials** | Cron logs auth error, exits non-zero | Fail-closed — no unauthorized fallback |

---

## 10. Async Hazards

| SDK/Library | Sync or Async? | Wrapping Strategy |
|-------------|----------------|-------------------|
| `asyncpg` | Async native | Direct `await` — no wrapping needed |
| `DefaultAzureCredential.get_token()` | **Sync** | Wrap in `asyncio.to_thread()` in cron script (matches `azure_cost_client.py` pattern) |
| `httpx.AsyncClient.post()` | Async native | Direct `await` |

---

## 11. Operational Readiness

| Concern | Decision | Acceptance Criteria |
|---------|---------|---------------------|
| **Health** | Existing `/api/health` covers DB pool status | No new endpoint needed |
| **Observability** | Cron script logs: timestamp, rows upserted, total USD, errors | Logs are visible in systemd journal |
| **Cache staleness** | `cache_age_seconds` in API response; frontend shows "last updated" | Frontend displays warning if age > 4h |
| **Alerting** | Warning banner in UI at $200/day; no server-side alert (out of scope) | Banner renders when threshold exceeded |
| **Deployment** | Migration runs on deploy (`scripts/migrations/`); cron added to crontab | Migration is idempotent (`IF NOT EXISTS`) |

---

## 12. Implementation Sequence (Phase 8 Build Order)

| Step | File | Depends On |
|------|------|-----------|
| 1 | `scripts/migrations/009_foundry_cost_daily.sql` | — |
| 2 | `tech_dev_agents/ops_console/services/foundry_cost_service.py` | Step 1 (table schema) |
| 3 | `tech_dev_agents/ops_console/routes/foundry_cost.py` | Step 2 (service) |
| 4 | `tech_dev_agents/ops_console/main.py` (wire route + service) | Steps 2, 3 |
| 5 | `deployment/ops-console/scripts/refresh_foundry_cost.py` | Step 2 (service + classify fn) |
| 6 | `frontend/src/types/api.ts` (add types) | — |
| 7 | `frontend/src/hooks/useFoundryCost.ts` | Step 6 |
| 8 | `frontend/src/components/FoundryCostPanel.tsx` | Steps 6, 7 |
| 9 | `frontend/src/components/DashboardLayout.tsx` (mount panel) | Step 8 |
| 10 | `frontend/src/__tests__/FoundryCostPanel.test.tsx` | Step 8 |
| 11 | `tests/ops_console/test_foundry_cost_service.py` | Step 2 |
| 12 | `e2e/dashboard-foundry-cost.spec.ts` | Steps 8, 9 |

---

## 13. Simplicity Checklist

- [x] No interfaces with single implementers
- [x] No abstract classes with single concrete classes
- [x] No factories for single types
- [x] No wrapper classes that just delegate
- [x] Maximum 3 layers: Route → Service → DB (2 layers for reads)
- [x] Endpoints do one thing (read cached cost data)
- [x] Request/response objects are flat
- [x] Clear, obvious naming
- [x] Can a new developer understand this in their first week? Yes — read from a table, show a chart

---

## 14. External API Rate Limits

| API | Rate Limit | Our Usage | Strategy |
|-----|-----------|-----------|----------|
| Azure Cost Management Query | 30 req / 5 min / subscription | 1 req / 2h | Well within limits; no throttling needed |
| On 429 | — | — | Log error, exit non-zero; cron retries next 2h cycle |

---

## 15. Configuration Additions

### `tech_dev_agents/ops_console/config.py` (Settings)

No new settings required. The cron script uses:
- `DATABASE_URL` — already exists
- `OPS_AZURE_SUBSCRIPTION_ID` — already exists
- `OPS_AZURE_MANAGED_IDENTITY_CLIENT_ID` — already exists

The $200 warning threshold is hardcoded in `FoundryCostPanel.tsx` (matches seed spec). A future story can extract it to settings.

---

## 16. Files Created / Modified

| Action | File |
|--------|------|
| **Create** | `scripts/migrations/009_foundry_cost_daily.sql` |
| **Create** | `tech_dev_agents/ops_console/services/foundry_cost_service.py` |
| **Create** | `tech_dev_agents/ops_console/routes/foundry_cost.py` |
| **Create** | `deployment/ops-console/scripts/refresh_foundry_cost.py` |
| **Create** | `frontend/src/components/FoundryCostPanel.tsx` |
| **Create** | `frontend/src/hooks/useFoundryCost.ts` |
| **Create** | `frontend/src/__tests__/FoundryCostPanel.test.tsx` |
| **Create** | `tests/ops_console/test_foundry_cost_service.py` |
| **Create** | `e2e/dashboard-foundry-cost.spec.ts` |
| **Modify** | `tech_dev_agents/ops_console/main.py` (wire service + route) |
| **Modify** | `frontend/src/components/DashboardLayout.tsx` (mount panel) |
| **Modify** | `frontend/src/types/api.ts` (add response types) |

---

Ready for Phase 7 (Test Design).
