# STORY-739: Dashboard Foundation + Agentic Tier — New Tab + Homepage Metrics

**Scope:** Medium  
**Phase path:** 1 → 6 → 7 → 8 → Done  
**Repo:** tech-dev-agents  
**Branch:** story-739/dashboard-agentic-foundation-tier  

## Problem Statement

Two Azure AI service tiers — Foundation (model inference: Claude / GPT-4) and Agentic (Azure AI Studio Agent Service multi-step orchestration) — are now enabled, but the ops dashboard doesn't distinguish between them. Today the homepage rolls everything into a single "Daily Foundry Spend" tile (currently $0 — see STORY-736), and there is no view for active agentic runs, their durations, or per-run costs. Operators can't tell how much Foundation-tier inference is being consumed, can't see which Agentic runs are executing right now, and have no way to drill into agent-run-level metrics.

## User Stories

1. **As an operator,** I want to see Foundation tier (model inference) usage and costs on the homepage — broken down by model (Opus / Sonnet / Haiku), with a 7-day trend — so I can spot inference burn at a glance without leaving the Fleet tab.
2. **As an operator,** I want an Agentic tab to drill into AI agent run metrics — count of currently executing runs, today's completed runs, average duration, today's Agentic-tier cost, and a per-run list (agent, story, start time, status, cost) — so I can investigate which agentic workflows are running, how long they take, and what they cost.
3. **As an operator,** when Azure AI APIs are unavailable, I want both new sections to show a graceful "data unavailable" state rather than blanking, broken numbers, or stale zeros, so I can tell the difference between "no agentic runs" and "telemetry feed down."

## Proposed Solution

### Backend

1. **Split Foundation vs Agentic tier in the cost service.** Today `cost_service.py` aggregates everything under `foundry_cost_usd` (Azure Foundry meter rows). Extend the cost model so Foundation-tier (model API) and Agentic-tier (agent runs) costs are tracked separately, sourced from Azure AI metering rather than estimated from Loki SDK logs. Surface both via the existing fleet overview response and via two new endpoints:
   - **`GET /api/fleet/foundation-usage`** — today's token count and cost, broken down by model (Opus / Sonnet / Haiku) plus a 7-day daily series for the sparkline.
   - **`GET /api/fleet/agentic-runs`** — currently-active agent runs and the last 24h of completed runs, each row carrying agent name, story id, start time, status, duration, and cost.
2. **Graceful degradation.** Both endpoints return a structured `{ available: false, reason: "..."}` payload when Azure AI APIs are unreachable rather than 5xx-ing. Frontend renders a "data unavailable" state.
3. **Source Foundation cost from Azure AI metering.** Replace the Loki-SDK-log estimate currently feeding the homepage tile with the actual Azure AI Foundation meter rows.

### Frontend

4. **"Foundation Tier" metrics card on the Fleet tab homepage.** New card in `FleetOverviewBar.tsx` (or a sibling component placed above the per-agent grid) showing today's Foundation tokens by model, today's Foundation cost (USD), and a 7-day trend sparkline. Sourced from `/api/fleet/foundation-usage`.
5. **"Agentic" tab in the dashboard navigation.** Add a fourth tab to `DashboardLayout.tsx` alongside Fleet / Work History / Alerts. The Agentic tab renders:
   - Headline metrics: active runs, today's completed runs, avg duration, today's Agentic cost.
   - Run-level table: agent name, story, start time, status, cost, duration.
6. **Routing.** New route entry for `/agentic` mounted under `DashboardLayout`'s `<Outlet />`.

## Success Criteria

| ID | Criterion | Test type |
|----|-----------|-----------|
| SC-1 | Fleet tab homepage shows a "Foundation Tier" metrics card with today's token count and cost broken down by model (Opus / Sonnet / Haiku) | unit (frontend component) + e2e (visual) |
| SC-2 | "Agentic" tab appears in the navigation alongside Fleet / Work History / Alerts and routes to `/agentic` | unit (frontend routing) |
| SC-3 | Agentic tab shows a count of currently-active Azure AI agent runs | unit (frontend component) |
| SC-4 | Agentic tab shows a run-level list with columns: agent name, story, start time, status, cost | unit (frontend component) |
| SC-5 | `GET /api/fleet/foundation-usage` returns today's token and cost breakdown by model plus a 7-day daily series | unit (backend route) |
| SC-6 | `GET /api/fleet/agentic-runs` returns active runs and the last 24h of completed runs with agent / story / start / status / cost / duration | unit (backend route) |
| SC-7 | The Foundation cost surfaced on the Fleet overview is sourced from Azure AI Foundation metering rows, not estimated from Loki SDK logs | unit (backend cost service) |
| SC-8 | When Azure AI APIs are unavailable, both `/foundation-usage` and `/agentic-runs` return a structured unavailable payload, and the frontend renders a "data unavailable" state instead of zeros or errors | unit (backend + frontend) |

## Open Questions

- **Azure AI Agentic tier API access.** Does Azure AI Studio Agent Service expose a list/describe-runs API today, or only metering aggregates? If only metering, what's the lowest-granularity row available (per-run vs per-hour-per-agent)?
- **Agent-run identity model.** How does an Azure AI agent run map to our internal concepts? Is there a 1:1 between an agent run and an SDK session, or do multiple runs nest inside a single dispatch? The seed assumes runs are independent units identified by `agent_name + story_id + start_time`, but this needs to be confirmed against the Agent Service schema.
- **Active-runs polling cadence.** How fresh does "active runs" need to be? 30s polling matches the existing fleet overview refresh, but if Azure AI rate-limits hard we may need to poll the Agent Service less aggressively and serve a cached snapshot.
- **Cost attribution.** When an Agentic run chains multiple Foundation calls, do those Foundation tokens show up in *both* `/foundation-usage` (as model inference) *and* `/agentic-runs` (as part of the run's cost)? Need to decide whether the homepage card double-counts or whether Foundation usage is netted of agent-driven calls. Default assumption: don't net — show raw inference total on the homepage and full run cost (which includes those Foundation calls) on the Agentic tab. Note this clearly in the UI.
- **Metering lag.** Azure metering is typically delayed by minutes to hours. The "today's cost" tile needs a clear timestamp / freshness indicator so users don't mistake metering lag for an inactivity period.
- **Historical backfill.** Do we need any historical Agentic-run data, or is "last 24h + active" sufficient for v1? Seed assumes 24h is enough; deeper history can land in a follow-up.

## Key Files

**Frontend**
- `frontend/src/components/DashboardLayout.tsx` — add "Agentic" `NavLink` and route registration.
- `frontend/src/components/FleetOverviewBar.tsx` — host or sit beside the new Foundation Tier card.
- `frontend/src/components/FoundryCostPanel.tsx` — read-only reference; existing 7-day stacked area chart pattern that the Foundation card's sparkline can mirror.
- `frontend/src/components/AgenticTab.tsx` (new) — top-level tab content: headline metrics + run-level table.
- `frontend/src/components/FoundationTierCard.tsx` (new) — homepage card with token/cost-by-model + sparkline.
- `frontend/src/hooks/useFoundationUsage.ts` (new) and `frontend/src/hooks/useAgenticRuns.ts` (new) — data hooks for the two new endpoints.
- `frontend/src/types/api.ts` — add `FoundationUsageResponse`, `AgenticRunsResponse`, `AgenticRun` types.

**Backend**
- `tech_dev_agents/ops_console/routes/fleet.py` — add `GET /foundation-usage` and `GET /agentic-runs` route handlers.
- `tech_dev_agents/ops_console/services/cost_service.py` — split Foundation vs Agentic tier accounting; add Foundation token/cost aggregation by model; add Agentic run listing.
- `tech_dev_agents/ops_console/azure_ai_client.py` (new or extension of existing Azure client) — wraps Azure AI Foundation metering + Agent Service run-list APIs with graceful-unavailable behavior.
- `tech_dev_agents/cost_collector.py` — read-only reference for the existing Azure Foundry meter ingestion path; Foundation tier metering may extend this.

**Tests**
- `tests/ops_console/test_fleet_foundation_usage.py` (new) — covers SC-5, SC-7, SC-8 (unavailable state).
- `tests/ops_console/test_fleet_agentic_runs.py` (new) — covers SC-6, SC-8 (unavailable state).
- `frontend/src/components/__tests__/FoundationTierCard.test.tsx` (new) — covers SC-1.
- `frontend/src/components/__tests__/AgenticTab.test.tsx` (new) — covers SC-3, SC-4.
- `frontend/src/components/__tests__/DashboardLayout.test.tsx` — covers SC-2 (Agentic nav link + route).

## Out of Scope

- Historical Agentic-run analytics beyond the last 24h (follow-up story).
- Cost-anomaly alerting on Foundation or Agentic tiers (separate alerts story).
- Per-user / per-team cost attribution within the Agentic tier.
- STORY-736's underlying fix for `daily_foundry_usd = $0` — STORY-739 consumes whatever Foundation metering is available and surfaces an unavailable state if metering is empty; it does not fix the upstream meter ingestion.

## Implementation Notes

- **Build order.** Backend first (routes + service split + tests GREEN), then frontend hooks + types, then components, then layout integration. Keeps the frontend never blocked on a missing endpoint.
- **Reuse `FoundryCostPanel` patterns.** The existing 7-day stacked area chart by model is the visual template for the Foundation card sparkline — copy the data-shape and recharts config rather than inventing a new pattern.
- **Unavailable payload shape.** Return `{ available: false, reason: "azure_ai_unreachable" | "metering_lag" | "not_configured" }` so the frontend can render distinct copy per reason without parsing free-form strings.
- **Don't break the existing homepage.** `FleetOverviewBar`'s "Daily Foundry Spend" tile stays as-is for this story; the Foundation card is additive. Any relabeling / consolidation of the existing tile is a follow-up.
- **No agent-VM deploys.** Both backend and frontend changes ship via the standard ops-console deploy path; no `deployment/vm/push-code.sh` runs needed.

**Frontend:** true

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
