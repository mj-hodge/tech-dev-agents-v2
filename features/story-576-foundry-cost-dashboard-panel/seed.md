# Seed: STORY-576 — Azure Foundry Cost Panel on Dashboard (Daily / Model Split)

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_add |
| Scope | medium |
| Feature Name | Live Azure Foundry cost panel on the main dashboard — daily $ by deployment (Opus / Sonnet / Haiku) for the last 7 days |
| Active Branch | `story-576/story-576` |
| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Parent Stories | STORY-024 (Foundry cost tracking), STORY-038 (cost breakdown), STORY-480 (dashboard overhaul), EPIC-004 (Foundry cost observability) |

---

## 1. Idea / Trigger

Mark, 2026-04-24: *"What you just ran to validate azure data needs to show up on the dashboard, I think some azure cli commands needed to be written. Figure out what the gap is and get it launched. This needs to be monitored closely — we'll likely need new managers and don't want to keep running into runaway Opus costs ever again."*

The week of 2026-04-17 through 2026-04-22 saw Foundry burn $1,486/day at peak (all Opus). The fix landed 2026-04-23; today we're at ~$193/day projected. Mark wants this **visible in the dashboard** so the next runaway gets caught in minutes not days.

The query that answers it is already validated (`scripts/fleet_azure_spend.py` + the ad-hoc `az rest` calls documented in `docs/azure-foundry-billing.md`). What's missing is:

1. A backend endpoint that runs that query on a cache.
2. A frontend panel that renders the 7-day $-by-model breakdown on the main dashboard page.

## 2. Problem Statement

- **No live Foundry cost on the dashboard.** `CostChart.tsx` exists but only on the per-agent detail view. `DashboardLayout.tsx` has no fleet-wide Foundry cost panel.
- **No auth plumbing for Cost Management API.** `docker-compose.yml` has `OPS_AZURE_SUBSCRIPTION_ID` + `OPS_AZURE_MANAGED_IDENTITY_CLIENT_ID` env vars but no evidence they're actually used by the ops-console backend. Need to verify the managed identity has `Cost Management Reader` role on the subscription.
- **Cost Management API has a 4–8h lag** + rate limits. Must cache and refresh on a schedule, not hit on every dashboard load.
- **Only Mark sees the numbers today** (via `az rest` from his laptop). When Mark is offline, nobody is watching. Runaway costs need a visual alarm that Morris's heartbeat (text-based) doesn't provide.

## 3. Scope Classification

**Medium.** Touches:
- Python backend: new `cost_service.get_foundry_daily_by_model()` method + route `/api/fleet/foundry-cost`.
- Azure identity: verify + possibly grant Cost Management Reader role on the managed identity (`OPS_AZURE_MANAGED_IDENTITY_CLIENT_ID`).
- Caching: Postgres table or in-memory cache with 2h TTL. Prefer a dedicated table `foundry_cost_daily` that a scheduled job populates every 2h.
- Scheduler: add a cron or async task that refreshes the cache. Existing patterns: `curator-cron`, `morris-fleet-check.sh`.
- Frontend: new `FoundryCostPanel.tsx` component, mounted in `DashboardLayout.tsx` below `BudgetGauge`. Renders a stacked AreaChart (same library as `CostChart.tsx`) with series per model.

## 4. Phase Path

```
1 (Seed)           — this file
4 (Analysis)       — analysis.md: evaluate cache strategy (Postgres table vs Redis vs in-memory), refresh cadence trade-off (2h lag OK?), and managed-identity vs service-principal auth.
6 (Design)         — feature-spec.md: API contract for /api/fleet/foundry-cost, panel component props, refresh schedule.
7 (Test Design)    — test-design.md + RED pytest for backend (mock Cost Management API, verify cache population) + RED vitest for panel render.
8 (Implementation) — wire backend + Azure auth + frontend panel; add Playwright @smoke spec for the panel.
Done
```

No Phase 2/3/5 — Medium scope skips Research/Expansion/Selection per CLAUDE.md.

## 5. Acceptance Criteria

Must-contain tokens:

- `tech_dev_agents/ops_console/services/foundry_cost_service.py` — new service with `get_daily_by_model(start_date, end_date) -> list[DailyFoundryCost]` that hits the Cost Management Query API (via `azure.identity` + `azure.mgmt.costmanagement` or raw `az rest` equivalent) and classifies ResourceId strings into `opus`/`sonnet`/`haiku`/`other`. Auth via `DefaultAzureCredential` (picks up managed identity in prod, env creds in dev).
- `tech_dev_agents/ops_console/routes/foundry_cost.py` (or add to existing `fleet.py`) — new route `GET /api/fleet/foundry-cost?days=7` returning `{daily: [{date, opus_usd, sonnet_usd, haiku_usd, total_usd}, ...], fetched_at, cache_age_seconds}`. Reads from the cache table; refuses to hit Cost Management API synchronously from a request handler (too slow).
- `scripts/migrations/<N>_foundry_cost_daily.sql` — new table:
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
- `deployment/ops-console/scripts/refresh_foundry_cost.py` — standalone script that refreshes the cache. Cron entry: `0 */2 * * *` (every 2h).
- `frontend/src/components/FoundryCostPanel.tsx` — new component. Stacked AreaChart with Opus (red), Sonnet (yellow), Haiku (green). Shows today's partial + last 6 days, total across the top. Warning banner when today's total > $200 (alert threshold from AGENT_PROVISIONING_LESSONS § 14).
- `frontend/src/components/DashboardLayout.tsx` — mount `FoundryCostPanel` below `BudgetGauge`.
- `frontend/src/__tests__/FoundryCostPanel.test.tsx` — 4+ vitest cases: renders all 3 model series, warning banner fires over threshold, handles null/missing data gracefully, updates on refetch.
- `e2e/dashboard-foundry-cost.spec.ts` — Playwright @smoke spec asserting panel renders on dashboard load.
- `tests/ops_console/test_foundry_cost_service.py` — Python tests: classification of ResourceId strings (claude-opus-4-6-xxx → opus), cache read/write, graceful degrade when Azure API unreachable.

## 6. Out of Scope

- **Realtime cost** — Cost Management API's 4–8h lag means today's number is always partial. Don't try to combine Cost Management with Azure Monitor token metrics (those undercount by 6×); pick one source.
- **Per-agent attribution on this panel** — agent cards already show per-agent `today_foundry_usd`; this panel is fleet-wide.
- **Alerting beyond the warning banner** — a separate story can wire it to Teams/Slack alerts. Out of scope here.
- **Non-Foundry costs** — SDK + OpenAI breakdown is already in `CostChart` on the agent detail view. This panel is Foundry-specific.

## 7. Design Hint (Phase 4 + 6)

Cost Management Query API shape (reference — already validated 2026-04-23 in `docs/azure-foundry-billing.md`):

```bash
az rest --method POST \
  --uri "https://management.azure.com/subscriptions/<SUB>/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
  --body @query-body.json
```

Where `query-body.json`:
```json
{
  "type": "ActualCost",
  "timeframe": "Custom",
  "timePeriod": {"from": "2026-04-17T00:00:00Z", "to": "2026-04-24T23:59:59Z"},
  "dataset": {
    "granularity": "Daily",
    "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
    "grouping": [{"type": "Dimension", "name": "ResourceId"}]
  }
}
```

Return rows have `[cost, YYYYMMDD, resourceId, currency]`. Classify `claude-opus-*` / `claude-sonnet-*` / `claude-haiku-*` into model buckets.

**Azure auth:** Prefer managed identity in prod (`DefaultAzureCredential` picks it up automatically in Azure). For dev/CI, fall back to `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` env vars. Required role: `Cost Management Reader` at subscription scope. If the managed identity doesn't have it yet, the Phase 4 analysis should flag the grant as a prerequisite and Mark will apply it from Azure portal (agents don't have `az role assignment` perms).

## 8. Target Branch

`main` (tech-dev-agents).

## 9. Dependencies

- Requires Azure managed identity or service principal with `Cost Management Reader` role. Phase 4 must verify or flag for Mark.
- No dispatch-queue or phase-runner changes.
- Independent of STORY-575 (quota display) — they ship in parallel.

## 10. Follow-up (separate story, not in scope)

Once this lands, a follow-up should extend it to:
- Daily spend by agent (already in per-agent cards but not aggregated across time on the fleet view).
- Alerting when `today_total > $500` — route to Teams via the STORY-505 Alertmanager integration that just landed.
- Week-over-week trend indicator (↑ / ↓ / flat vs same day last week).

## Test Criteria

1. New backend service (e.g. `foundry_cost_service`) calls Azure Cost Management's `query` endpoint with the documented JSON body and parses `[cost, YYYYMMDD, resourceId, currency]` rows. Unit test mocks the HTTP layer and asserts the request body matches the contract shape (timeframe, granularity, aggregation, grouping).
2. Resource-id classifier maps `claude-opus-*` / `claude-sonnet-*` / `claude-haiku-*` to model buckets `opus|sonnet|haiku`; unrecognized → `other`. Pin the regex with table-driven tests.
3. Endpoint (e.g. `GET /api/foundry-cost?days=7`) returns `{today_total_usd, week_total_usd, by_model: {opus, sonnet, haiku, other}, by_day: [{date, cost_usd}, …]}`. Schema-validated response model.
4. Auth fallback: in CI/dev, `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` env vars succeed; in prod, `DefaultAzureCredential` resolves the managed identity. Missing both → response surfaces a structured `source=no_credentials` so the UI can render an empty state instead of crashing.
5. Frontend panel (Playwright @smoke) renders today + week totals + per-model breakdown when the endpoint returns data, and renders the empty state when `source=no_credentials` or `by_day=[]`.
6. Regression: existing dashboard panels unaffected — pin via Playwright snapshot or contract test.

## Validation

After deploy: open the dashboard, confirm the new Foundry cost panel renders today's and the past week's totals matching the Azure portal Cost Management view (within rounding). Verify the empty state appears when the managed identity hasn't yet been granted `Cost Management Reader` (Mark will grant the role; until then the panel must not crash other widgets). CI: vitest + Playwright @smoke + the new pytest module are green on the PR.
