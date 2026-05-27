# Seed: Dashboard Overhaul — Foundry Costs, Quota %, Presence, Work Detail

> Phase 1 — Concept & Seed
> Date: 2026-04-20
> Scope: Medium
> Phase path: 1 → 4 → 6 → [6b,6c,6d] → 7 → 8 → 8b → 11 → Done

---

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Feature Name | Dashboard Overhaul |
| Story ID | STORY-480 |

## Problem Statement

The ops console dashboard (STORY-019, merged) provides a functional fleet overview, but four critical information gaps make it difficult for operators to answer everyday questions without leaving the browser:

1. **Foundry costs lack context.** The FleetOverviewBar shows raw daily and monthly spend figures, but there is no indication of how that spend relates to the budget. Mark cannot tell at a glance whether the fleet is on track to exceed its monthly cost ceiling or has headroom to spare. The AgentCard shows `today_foundry_usd` as a bare dollar figure — without a quota percentage or progress bar, the number is meaningless unless you already know the budget.

2. **Presence is disconnected from the main view.** STORY-426 added a `PresencePanel` with colored dots (working / idle / rate_limited / offline), but it lives as a standalone block above the agent grid. The presence state is not reflected on the AgentCard itself, so the operator must mentally correlate two separate UI regions to answer "is Dan actually working?" The PresencePanel also lacks detail — it shows a label but not *what* the agent is doing (current story, current phase, elapsed time).

3. **Work detail is shallow.** The AgentCard shows `current_story` (truncated to 40 chars) and `current_phase` as plain text, but there is no indication of phase progress, elapsed time on the current phase, or how many phases remain. The WorkHistoryPanel is a flat table with no filtering by agent, no grouping by date, and no cost-per-story rollup. An operator cannot answer "what did Dan accomplish this week and what did it cost?" without querying the API directly.

4. **No cost breakdown in the fleet bar.** The FleetOverviewBar shows `total_daily_spend_usd` and `total_monthly_spend_usd` but does not break these down by cost source (Foundry vs. SDK vs. OpenAI). Since Foundry is the pay-per-token cost driver and SDK is flat-rate, lumping them together obscures the actionable metric. EPIC-004 (Foundry Cost Observability) established the cost-source taxonomy; the dashboard should surface it.

### What Exists Today

| Component | Lines | Current Capability | Gap |
|-----------|-------|-------------------|-----|
| FleetOverviewBar | 81 | 7 KPI cards (daily spend, monthly spend, active agents, busy agents, stories, queued, health) | No quota %, no cost-source breakdown, no budget progress bar |
| AgentCard | 92 | Status badge, busy flag, story name, phase, foundry cost, last-checked timestamp | No presence dot, no phase progress, no elapsed time |
| PresencePanel | 66 | Grid of colored bubbles with state label | No detail (story/phase), disconnected from AgentCard, no elapsed time |
| WorkHistoryPanel | 113 | Flat table of completed stories with PR badges | No agent filter, no date grouping, no cost-per-story, no search |
| AgentDetailView | 87 | Cost chart (single series), activity timeline, restart/pause buttons | No cost-source breakdown in chart, no current-work summary card |
| CostChart | 40 | Single AreaChart of 30-day cost history | Single `cost` field — no Foundry/SDK/OpenAI breakdown |

## Target User / Use Case

- **Mark (operator)** — opens the dashboard and needs to answer within 5 seconds: "Are we on budget? Is anyone stuck? What shipped today?" Currently requires cross-referencing multiple panels and mental math.
- **Product managers** — need a single-page summary of fleet productivity: stories completed, costs incurred, agents utilized — without clicking into individual agent views.

### User Stories

1. As an operator, I want to see daily Foundry spend as a percentage of my daily budget so I know whether to throttle agents before the ceiling is hit.
2. As an operator, I want each AgentCard to show the agent's live presence state so I do not need to look at a separate panel.
3. As an operator, I want the work history panel to support filtering by agent and date range so I can answer "what did Dan do this week?"
4. As an operator, I want the fleet bar to break costs down by source (Foundry / SDK / OpenAI) so I can see which cost lever matters.
5. As an operator, I want each AgentCard to show phase progress (e.g., "Phase 7 of 11") and elapsed time so I can spot slow phases early.

## Acceptance Criteria

- [ ] AC-1: FleetOverviewBar shows a "Budget Used" KPI card displaying `(total_daily_spend_usd / daily_budget_usd) * 100` as a percentage with a color-coded progress bar (green < 70%, yellow 70-90%, red > 90%). The daily budget value is provided by a new backend field `daily_budget_usd` on the `/api/fleet` response (sourced from config, default $50).
- [ ] AC-2: FleetOverviewBar shows cost breakdown by source — Foundry, SDK, and OpenAI — as either a secondary line under the Daily Spend card or a separate mini-bar chart. Data sourced from new aggregate fields on `/api/fleet`.
- [ ] AC-3: AgentCard displays an inline presence indicator (colored dot + label) sourced from the existing `/api/agents/presence` endpoint, eliminating the need to cross-reference PresencePanel. The standalone PresencePanel is retained but becomes the "expanded" view.
- [ ] AC-4: AgentCard shows phase progress as "Phase N of M" (derived from scope-to-phase-path mapping) and elapsed time on the current phase (derived from `phase_started_at` timestamp, new backend field).
- [ ] AC-5: WorkHistoryPanel supports filtering by agent name (dropdown) and date range (last 7d / 30d / all). Filter state is preserved in URL query params.
- [ ] AC-6: WorkHistoryPanel shows a cost-per-story column (sum of Foundry + SDK + OpenAI for that story's duration). Data sourced from a new `total_cost_usd` field on the work-history response.
- [ ] AC-7: AgentDetailView cost chart shows stacked area series for Foundry, SDK, and OpenAI instead of a single `cost` line. Requires `CostHistoryEntry` to include per-source fields.
- [ ] AC-8: All new and modified components have corresponding Vitest + React Testing Library tests.
- [ ] AC-9: No regressions — all existing 59+ frontend tests continue to pass.
- [ ] AC-10: Responsive layout works at 1024px+ (existing breakpoint), with graceful degradation of the budget bar and cost breakdown at smaller widths.

## Technical Notes

### Frontend Changes

| Component | Change | Estimated Lines |
|-----------|--------|----------------|
| FleetOverviewBar | Add "Budget Used" card with progress bar; add cost-source breakdown | +40 |
| AgentCard | Merge presence dot inline; add phase progress + elapsed time | +25 |
| PresencePanel | Keep as expanded view; add story/phase detail text per agent | +15 |
| WorkHistoryPanel | Add agent filter dropdown, date range selector, cost column, URL param sync | +60 |
| CostChart | Refactor to stacked AreaChart with 3 series (foundry, sdk, openai) | +30 |
| AgentDetailView | Add "Current Work" summary card above chart | +20 |
| types/api.ts | Extend FleetOverview, CostHistoryEntry, CompletedStory, AgentSummary interfaces | +25 |

### Backend Changes

| Endpoint | Change |
|----------|--------|
| `GET /api/fleet` | Add `daily_budget_usd` (from config), `daily_foundry_usd`, `daily_sdk_usd`, `daily_openai_usd` aggregate fields |
| `GET /api/agents/{name}` | Extend `cost_history` entries with `foundry_cost_usd`, `sdk_cost_usd`, `openai_cost_usd` breakdown; add `phase_started_at` field |
| `GET /api/work-history` | Add `total_cost_usd` per story; support `?agent=` and `?since=` query params for filtering |
| `AgentSummary` response model | Add `phase_started_at: str | null`, `phase_total: int | null` (total phases in current scope path) |

### Data Sources

- **Daily budget**: Config value (`OPS_DAILY_BUDGET_USD` env var, default 50). No new external API calls needed.
- **Cost breakdown**: Already computed per-source in `CostService` / `AzureCostClient`. The backend already tracks `foundry_cost_usd`, `sdk_cost_usd`, `openai_cost_usd` per agent — this story surfaces the aggregates and per-day breakdowns that are already being computed but not exposed.
- **Phase progress**: The dispatch queue and `.project` file track current phase and scope. The scope-to-phase-path mapping is already defined in the SDLC engine (STORY-006). Expose `phase_index` and `phase_total` on the agent summary.
- **Phase elapsed time**: Add `phase_started_at` to the dispatch record (timestamp of when the current phase began).

### Key Design Decisions

1. **Budget comes from config, not a database.** A simple env var (`OPS_DAILY_BUDGET_USD`) is sufficient for a single-team deployment. No CRUD UI for budget management — that is out of scope.
2. **Presence is merged into AgentCard, not replaced.** The PresencePanel remains as an expanded view for operators who want the full list at a glance. The AgentCard gains an inline dot so the grid view is self-contained.
3. **Work history filters use URL query params** so bookmarkable/shareable links work (e.g., `/work-history?agent=Dan&since=7d`).
4. **CostChart becomes a stacked AreaChart** using Recharts' native stacking support — no new charting library needed.

## Dependencies

| Dependency | Status | Risk |
|------------|--------|------|
| STORY-019 (Ops Console Frontend) | Done, merged | None — base we are extending |
| STORY-426 (Dashboard Presence) | Phase 8 complete, PR #55 remediation in progress | Low — presence API exists; if remediation is not merged, we use the current API contract which is stable |
| EPIC-004 (Foundry Cost Observability) | Children in progress (STORY-224, 225, 226, 227) | Low — we depend only on the existing cost-source taxonomy, not on any specific EPIC-004 child story |
| STORY-022 (Data Pipeline Fix) | Done, merged | None — ensures cost data flows correctly |
| CostService / AzureCostClient | Live in production | None — already computes per-source costs |
| PresenceService | Live (with pending remediation) | Low — API contract is stable |

## Out of Scope

- **Budget CRUD UI** — No admin page to set/edit budgets. Budget is an env var changed by the operator.
- **WebSocket real-time updates** — Polling at 30s intervals (existing pattern) is retained. WebSocket upgrade is a separate story.
- **Mobile-first redesign** — The existing 1024px+ breakpoint is preserved. Sub-1024px improvements are out of scope.
- **CSV/JSON export** — Data export from work history is a separate feature.
- **Cost alerting / threshold notifications** — Already handled by the AlertService. This story surfaces data, not new alert rules.
- **Entra ID SSO** — Tracked by STORY-023 separately.
- **New navigation or routing structure** — Existing routes (`/`, `/agents/:name`, `/alerts`, `/work-history`) are retained.
- **DispatchQueue component refactor** — The 301-line component could benefit from splitting, but that is a separate refactor story.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Backend cost breakdown fields return nulls for agents without Azure cost data | Medium | Low | Frontend renders "N/A" gracefully; Foundry-missing warning (STORY-038 pattern) already exists |
| Phase progress mapping is inaccurate for stories with custom phase paths | Low | Medium | Use the canonical scope-to-path mapping from SDLC engine; fall back to "Phase N" without total if scope is unknown |
| Presence polling adds extra API calls per AgentCard | Low | Low | Reuse the existing `usePresence` hook (single call for all agents, TanStack Query deduplicates); do not make per-card calls |
| Work history cost-per-story is inaccurate for long-running stories that span multiple days | Medium | Low | Show cost as "estimated" with a tooltip explaining the calculation method |
| Stacked AreaChart is harder to read than a single line for small values | Low | Low | Use Recharts tooltips and a legend; allow toggling series visibility |

## Scope Justification: Medium

This story modifies 6-7 existing frontend components (no new pages/routes), extends 3 backend endpoints (no new endpoints), and adds ~215 lines of frontend code plus ~80 lines of backend model/route changes. It does not introduce new infrastructure, new authentication flows, or new data sources. The changes are additive refinements to an already-shipping dashboard, constrained to the existing tech stack (React, TanStack Query, Recharts, FastAPI). Medium scope is appropriate.

## Next Phase

Phase 4 — Analysis (evaluate alternatives for quota display, presence integration pattern, cost chart redesign)
