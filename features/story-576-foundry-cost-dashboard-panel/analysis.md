# Analysis: STORY-576 — Azure Foundry Cost Panel on Dashboard

| Field | Value |
|-------|-------|
| Story | STORY-576 |
| Phase | 4 — Analysis |
| Author | Claude (Phase-4 agent) |
| Date | 2026-04-24 |
| Scope | Medium |

---

## 1. Current State Assessment

### 1.1 What Already Exists

| Component | File | Status |
|-----------|------|--------|
| Azure Cost Client | `services/azure_cost_client.py` | ✅ Queries Cost Management API, groups by **ResourceGroup**, classifies Foundry vs OpenAI |
| Cost Service | `services/cost_service.py` | ✅ Per-agent + fleet daily/monthly spend aggregation |
| In-Memory Cache | `cache.py` | ✅ TTLCache (5 min cost, 1 min fleet) |
| Fleet Route | `routes/fleet.py` | ✅ `GET /fleet` returns `daily_foundry_usd` etc. |
| CostChart Component | `frontend/src/components/CostChart.tsx` | ✅ Recharts AreaChart — but agent-detail only, not fleet-wide |
| BudgetGauge Component | `frontend/src/components/BudgetGauge.tsx` | ✅ Gauge on dashboard — but quota %, not $ breakdown |
| Azure Billing Docs | `docs/azure-foundry-billing.md` | ✅ Validated query shape + classification logic |
| Config Settings | `config.py` | ✅ `azure_subscription_id`, `azure_managed_identity_client_id`, `azure_agent_map` |

### 1.2 What's Missing (Gap Analysis)

| Gap | Detail |
|-----|--------|
| **Model-level breakdown** | `AzureCostClient` groups by ResourceGroup → agent mapping. STORY-576 needs grouping by **ResourceId** to distinguish Opus/Sonnet/Haiku deployments. |
| **Fleet-wide 7-day by model** | No endpoint returns daily cost bucketed by model across the whole fleet. `GET /fleet` returns today's totals only. |
| **Persistent cache** | In-memory TTLCache loses data on restart. Cost Management API has 4–8h lag + rate limits; 2h cron refresh is the right cadence, but results must survive process restarts. |
| **Cron refresh job** | No existing scheduled refresh for cost data. The lifespan pattern is for always-on loops (stale claim recovery), not for periodic external fetches. |
| **Dashboard panel** | No `FoundryCostPanel` component. DashboardLayout has no fleet cost chart slot. |
| **Warning banner** | No threshold-based visual alarm for daily spend on the dashboard. |

---

## 2. Cache Strategy Evaluation

### Option A: Postgres Table (RECOMMENDED)

| Criterion | Assessment |
|-----------|------------|
| **Durability** | ✅ Survives process restarts, deployments, and VM reboots |
| **Multi-instance** | ✅ Single source of truth if ops-console scales to >1 replica |
| **Refresh model** | ✅ Standalone cron script writes rows; API handler reads them — zero coupling |
| **Ops overhead** | Low — one migration file, one table, 7 rows max (one per day) |
| **Latency** | ~1ms for a 7-row `SELECT` — negligible |
| **Existing precedent** | Dispatch queue already uses Postgres (`asyncpg`); pool is initialized in lifespan |

**Schema** (from seed, confirmed adequate):
```sql
CREATE TABLE IF NOT EXISTS foundry_cost_daily (
  usage_date DATE PRIMARY KEY,
  opus_usd   NUMERIC(10,2) NOT NULL DEFAULT 0,
  sonnet_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
  haiku_usd  NUMERIC(10,2) NOT NULL DEFAULT 0,
  other_usd  NUMERIC(10,2) NOT NULL DEFAULT 0,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Option B: In-Memory TTLCache (Existing Pattern)

| Criterion | Assessment |
|-----------|------------|
| **Durability** | ❌ Lost on restart — first request after restart returns empty until next cron tick (up to 2h gap) |
| **Multi-instance** | ❌ Each process has its own copy; requires lifespan-based refresh per instance |
| **Refresh model** | Awkward — either the cron script hits an internal API to populate the cache, or each instance runs its own refresh loop |
| **Ops overhead** | None — no migration |

**Verdict:** Not suitable. The 2h refresh cadence + API lag means losing cache on restart creates a multi-hour data gap.

### Option C: Redis

| Criterion | Assessment |
|-----------|------------|
| **Durability** | ✅ With persistence |
| **Multi-instance** | ✅ Shared |
| **Ops overhead** | ❌ New dependency — no Redis in the current stack |

**Verdict:** Over-engineered. Adds operational complexity for 7 rows of data.

### **Decision: Postgres Table (Option A)**

Best fit: durable, zero new dependencies, aligns with dispatch DB precedent. The standalone refresh script writes directly to the table; the API handler reads via a simple `SELECT`.

---

## 3. Refresh Cadence Analysis

### Cost Management API Constraints

- **Data lag**: 4–8 hours (Microsoft-documented). Today's numbers are always partial.
- **Rate limits**: 30 requests/5 minutes at subscription scope (well within our 1 request/2h).
- **Query cost**: Free (no charges for Cost Management queries).

### Cadence Options

| Cadence | Pros | Cons |
|---------|------|------|
| **Every 2h** (recommended) | Matches API lag — fresher data won't exist. 12 queries/day well under rate limits. Aligns with `morris-fleet-check.sh` cadence. | Worst-case 2h stale on a spike day |
| Every 30 min | Catches partial-day changes faster | 48 queries/day; still within limits but no new data from API most of the time |
| Every 6h | Minimal API usage | Too stale for runaway detection |

### **Decision: 2-hour refresh (`0 */2 * * *`)**

The API data itself only updates every 4–8h, so refreshing more often than 2h yields duplicate data. The refresh script should:
1. Query 8 days of data (7 full + today partial) to handle late-arriving cost rows.
2. UPSERT into `foundry_cost_daily` (idempotent — safe to re-run).
3. Log refresh timestamp and row count for observability.

---

## 4. Azure Authentication Analysis

### Current Auth Chain

`main.py` already initializes `DefaultAzureCredential` with optional `managed_identity_client_id`:

```python
from azure.identity import DefaultAzureCredential
credential_kwargs = {}
if settings.azure_managed_identity_client_id:
    credential_kwargs["managed_identity_client_id"] = settings.azure_managed_identity_client_id
credential = DefaultAzureCredential(**credential_kwargs)
```

This credential chain tries (in order):
1. **Environment variables** (`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID`) — for CI/dev
2. **Managed Identity** — for Azure VMs in prod (user-assigned if `client_id` specified)
3. **Azure CLI** — for local dev (`az login`)

### Role Requirement

The Cost Management Query API requires **Cost Management Reader** role at subscription scope. This is the same role needed by the existing `AzureCostClient`.

**Prerequisite check**: The existing `azure_cost_client.py` already queries Cost Management API successfully (STORY-024/227 delivered this). If that works, STORY-576's refresh script uses the same credential + subscription — no new role grants needed.

**Action item**: The refresh script will use `DefaultAzureCredential` identically. If running as a cron on the same VM (hermes), it inherits the same managed identity. No additional auth configuration required.

### Query API Change: ResourceGroup → ResourceId

The existing client groups by `ResourceGroup` (one RG per agent). STORY-576 needs grouping by `ResourceId` to identify individual model deployments:

```json
{
  "dataset": {
    "grouping": [{"type": "Dimension", "name": "ResourceId"}]
  }
}
```

Resource ID strings contain the deployment name (e.g., `claude-opus-4-6-...`, `claude-sonnet-4-5-...`, `claude-haiku-3-5-...`). Classification regex:
- `/claude-opus/i` → **opus**
- `/claude-sonnet/i` → **sonnet**
- `/claude-haiku/i` → **haiku**
- Everything else → **other**

This is a new query — not a modification to the existing `AzureCostClient`. The refresh script will make its own Cost Management API call.

---

## 5. Architecture Decision: Standalone Service vs. Extend Existing

### Option A: New `foundry_cost_service.py` (RECOMMENDED)

Separate service file with clear responsibility:
- `get_daily_by_model(days=7) -> list[DailyFoundryCost]` — reads from Postgres
- `refresh_from_azure(start_date, end_date)` — writes to Postgres (called by cron script)

**Rationale**: The existing `cost_service.py` handles per-agent costs from Loki + Azure. Fleet-wide model-bucketed costs are a different concern. Separate service keeps both clean.

### Option B: Extend `cost_service.py`

Add methods to existing service. Mixes per-agent Loki-based costs with fleet-wide Azure-only costs.

**Verdict**: Option A — cleaner separation, easier to test.

---

## 6. Frontend Integration Plan

### Panel Placement

`DashboardLayout.tsx` currently renders:
- Header + navigation
- `FleetOverviewBar`
- `BudgetGauge` (in sidebar or header area)
- Main content (agent grid, dispatch queue)

`FoundryCostPanel` mounts **below** `BudgetGauge` — same area, complementary info (gauge = today's quota %, panel = 7-day cost trend by model).

### Chart Library

`CostChart.tsx` already uses Recharts `AreaChart` with gradient fills. `FoundryCostPanel` will use the same library, same pattern — stacked `Area` components:
- **Opus**: red (#ef4444)
- **Sonnet**: yellow (#eab308)
- **Haiku**: green (#22c55e)

### Data Fetching

New custom hook `useFoundryCost(days=7)`:
- React Query with 5-minute stale time (server data only updates every 2h anyway)
- `GET /api/fleet/foundry-cost?days=7`
- Returns `{ daily: [...], fetched_at, cache_age_seconds }`

### Warning Banner

Inline banner within the panel (not a global alert):
- Triggers when `today_total > 200` (configurable threshold)
- Red background, warning icon, text: "⚠ Today's Foundry spend exceeds $200"
- Matches existing `AlertBanner` styling patterns

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Cost Management API returns no data (role missing) | Low (already working for STORY-024) | High — panel shows empty | Graceful fallback: "No data available" message + log warning |
| Cron fails silently | Medium | Medium — stale data shown | `cache_age_seconds` in response; frontend shows "last updated X ago" warning if age > 4h |
| Model classification misses new deployment names | Low | Low — falls into "other" bucket | Log unclassified ResourceIds; regex is case-insensitive and prefix-based |
| DB migration fails | Low | Low — table is isolated, no FK dependencies | Standard `IF NOT EXISTS` guard; migration is additive only |
| $200 threshold too low/high | Medium | Low — cosmetic | Make threshold configurable via settings (`foundry_cost_alert_threshold_usd`) |

---

## 8. Implementation Sequence (Phase 6–8 Roadmap)

### Phase 6: Feature Specification
- API contract for `GET /api/fleet/foundry-cost`
- Pydantic response models (`DailyFoundryCostByModel`, `FoundryCostResponse`)
- Component props and behavior spec
- Configuration additions to `Settings`

### Phase 7: Test Design (RED tests)
- **Backend**: Mock Cost Management API response → verify classification + DB write + read
- **Frontend**: Render with fixture data → verify 3 series + warning banner + loading/error states
- Tests written first, all failing (RED phase)

### Phase 8: Implementation
1. Migration `009_foundry_cost_daily.sql`
2. `foundry_cost_service.py` — read from DB + classify ResourceId
3. `routes/foundry_cost.py` — `GET /api/fleet/foundry-cost`
4. `refresh_foundry_cost.py` — standalone cron script
5. `FoundryCostPanel.tsx` — stacked area chart + warning banner
6. `DashboardLayout.tsx` — mount panel
7. Hook `useFoundryCost.ts`
8. Wire route in `main.py`
9. E2E smoke test

---

## 9. Dependencies & Prerequisites

| Dependency | Status | Owner |
|-----------|--------|-------|
| Postgres DB connection | ✅ Available (dispatch DB) | Ops |
| `DefaultAzureCredential` with Cost Management Reader | ✅ Already working (STORY-024) | Mark (if role grant needed) |
| `azure-identity` package | ✅ In `requirements.txt` | — |
| Recharts | ✅ In `package.json` | — |
| Cron on hermes VM | ✅ Existing pattern (`morris-fleet-check.sh`) | Ops |

**No blockers identified.** All dependencies are satisfied by existing infrastructure.

---

## 10. Conclusion

STORY-576 fills a clear gap: fleet-wide Foundry cost visibility by model, cached durably, with a visual alarm threshold. The architecture reuses existing Azure auth, DB pool, chart library, and cron patterns with minimal new complexity:

- **1 new Postgres table** (7 rows, additive migration)
- **1 new backend service** (read from DB, classify ResourceId)
- **1 new API route** (read-only, no sync Azure calls)
- **1 cron script** (every 2h, standalone, idempotent)
- **1 new React component** (stacked AreaChart + warning banner)

Ready for Phase 6 (Feature Specification).
