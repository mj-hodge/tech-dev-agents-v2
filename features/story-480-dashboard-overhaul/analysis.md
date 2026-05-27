# Analysis: Dashboard Overhaul — STORY-480

> Phase 4 — Analysis
> Date: 2026-04-20
> Scope: Medium
> Story: STORY-480

---

## 1. Affected Files

### Frontend (React/TypeScript)

| File | Lines | Impact | Change Type |
|------|-------|--------|-------------|
| `frontend/src/components/FleetOverviewBar.tsx` | 81 | High | Modify — add Budget Used card + cost-source breakdown |
| `frontend/src/components/AgentCard.tsx` | 92 | High | Modify — inline presence dot, phase progress, elapsed time |
| `frontend/src/components/PresencePanel.tsx` | 66 | Low | Modify — add story/phase detail text per agent bubble |
| `frontend/src/components/WorkHistoryPanel.tsx` | 113 | High | Modify — add agent filter, date range, cost column, URL params |
| `frontend/src/components/CostChart.tsx` | 40 | Medium | Modify — refactor to stacked AreaChart (3 series) |
| `frontend/src/components/AgentDetailView.tsx` | 87 | Medium | Modify — add Current Work summary card |
| `frontend/src/types/api.ts` | 173 | Medium | Modify — extend FleetOverview, CostHistoryEntry, CompletedStory, AgentSummary |
| `frontend/src/hooks/useFleet.ts` | ~20 | None | No change — already fetches FleetOverview |
| `frontend/src/hooks/usePresence.ts` | ~20 | None | No change — already returns AgentPresenceItem[] |

### Backend (Python/FastAPI)

| File | Lines | Impact | Change Type |
|------|-------|--------|-------------|
| `tech_dev_agents/ops_console/routes/fleet.py` | 298 | Medium | Modify — add daily_budget_usd, per-source aggregate fields |
| `tech_dev_agents/ops_console/routes/agents.py` | 300 | Medium | Modify — extend cost_history entries with per-source fields; add phase_started_at |
| `tech_dev_agents/ops_console/routes/work_history.py` | 128 | Medium | Modify — add `?agent=` and `?since=` query params; add total_cost_usd per story |
| `tech_dev_agents/ops_console/models/responses.py` | 443 | Medium | Modify — extend FleetOverviewResponse, FleetAgentSummary, CompletedStory; add CostHistoryDetailEntry |
| `tech_dev_agents/ops_console/config.py` | 95 | Low | Modify — add daily_budget_usd config field |
| `tech_dev_agents/ops_console/services/cost_service.py` | 281 | Low | No structural change — per-source data already computed; may add a helper for per-story cost rollup |

### Test Files

| File | Lines | Impact |
|------|-------|--------|
| `frontend/src/__tests__/FleetOverviewBar.test.tsx` | 159 | Modify — add tests for Budget Used card, cost breakdown |
| `frontend/src/__tests__/AgentCard.test.tsx` | 191 | Modify — add tests for presence dot, phase progress, elapsed time |
| `frontend/src/__tests__/WorkHistoryPanel.test.tsx` | (new) | Create — tests for filtering, cost column |
| `frontend/src/__tests__/CostChart.test.tsx` | (new) | Create — tests for stacked chart |
| `frontend/src/__tests__/AgentDetailView.test.tsx` | 230 | Modify — add test for Current Work card |

---

## 2. Current Behavior

### FleetOverviewBar
- Renders 7 KPI cards: Daily Spend, Monthly Spend, Active Agents, Busy Agents, Active Stories, Queued Stories, Health Score.
- Spend values are raw dollar amounts from `total_daily_spend_usd` / `total_monthly_spend_usd` — Azure-only (SDK excluded from headline).
- No indication of budget target. No cost-source breakdown (Foundry vs SDK vs OpenAI are lumped).

### AgentCard
- Shows agent name, role badge, status badge, busy flag, truncated story name, phase (plain text), queued count, Foundry cost with tooltip, and relative timestamp.
- Presence state is NOT shown — operator must look at the separate PresencePanel.
- Phase is a bare string (e.g., "Phase 7") with no progress indicator and no elapsed time.

### PresencePanel
- Standalone panel above the agent grid. Shows colored dots (green/gray/yellow/red) and state label per agent.
- No story or phase context — just the agent name and state label.
- Uses `usePresence` hook (single API call for all agents, TanStack Query deduplication).

### WorkHistoryPanel
- Flat table of completed stories with columns: Story ID, Repo, Agent, Title, PR badge, Date.
- No filtering by agent or date range. No cost-per-story column.
- Queries `/api/work-history` which scans 7 GitHub repos for PRs.

### CostChart (AgentDetailView)
- Single blue AreaChart with `date` and `cost` fields.
- No per-source breakdown — just a single aggregate cost line.
- Used on the agent detail page with 7 days of history.

### AgentDetailView
- Shows agent name, action buttons (Restart/Pause), context panel, CostChart, ActivityTimeline.
- No "Current Work" summary card. No phase progress. No cost-source breakdown in chart.

### Backend `/api/fleet`
- Computes per-agent summaries with `today_foundry_usd`, `today_sdk_usd`, `today_openai_usd` already present on `FleetAgentSummary`.
- Fleet-wide aggregates only expose `total_daily_spend_usd` and `total_monthly_spend_usd` (Azure Foundry only).
- No budget field. No per-source aggregate fields on the fleet response.

### Backend `/api/work-history`
- Scans 7 repos for PRs. No filtering query params. No cost data per story.

### Backend `/api/agents/{name}`
- Returns `AgentDetailResponse` with `cost_today` (has per-source breakdown) but `cost_history` uses `CostBreakdownResponse` with `DailyCost` entries that DO have `foundry_cost_usd` / `sdk_cost_usd` / `openai_cost_usd` — but the frontend `CostHistoryEntry` type only has `date` and `cost`.

---

## 3. Proposed Approach

### 3.1 Budget Display (AC-1)

**Approach:** Add `daily_budget_usd` config field to `Settings` (env var `OPS_DAILY_BUDGET_USD`, default 50.0). Expose on `FleetOverviewResponse`. Frontend adds an 8th KPI card "Budget Used" with a progress bar showing `(total_daily_spend_usd / daily_budget_usd) * 100`. Color-coded: green < 70%, yellow 70-90%, red > 90%.

**Why this approach:** Budget from config is the simplest viable solution — matches the single-team deployment model. No database, no CRUD UI. The env var can be changed via deployment config.

### 3.2 Cost-Source Breakdown in Fleet Bar (AC-2)

**Approach:** Add `daily_foundry_usd`, `daily_sdk_usd`, `daily_openai_usd` aggregate fields to `FleetOverviewResponse`. These are summed from per-agent summaries that already carry per-source breakdowns. Frontend shows a secondary line under the Daily Spend card with "Foundry $X · SDK $Y · OpenAI $Z" text.

**Why not a mini-bar chart:** Text breakdown is simpler, fits the existing KPI card layout, and avoids adding a Recharts dependency to the fleet bar. A mini-bar chart can be added in a follow-up if needed.

### 3.3 Presence on AgentCard (AC-3)

**Approach:** Import `usePresence` in the parent grid component (not per-card — avoids N API calls). Pass the presence state for each agent down as a prop to `AgentCard`. Render a colored dot + short label (e.g., "Working", "Idle") inline next to the status badge.

**Why not per-card fetch:** The `usePresence` hook returns all agents in one call. TanStack Query deduplicates, but it is cleaner to pass data down than to have each card call the hook.

### 3.4 Phase Progress + Elapsed Time (AC-4)

**Approach:** Backend adds `phase_started_at` (ISO timestamp) and `phase_total` (total phases in scope path) to `FleetAgentSummary` and `AgentSummary`. The phase total is derived from `SDLCEngine.PHASE_PATHS` using the story's scope. Frontend computes phase index from `current_phase` relative to the path and renders "Phase N of M" with an elapsed time (computed client-side from `phase_started_at`).

**Why phase_total from backend:** The SDLC engine's phase path mapping is Python-only. Duplicating it in TypeScript would create drift risk. Backend resolves scope → total phases and sends the integer.

### 3.5 Work History Filtering (AC-5)

**Approach:** Add `?agent=` and `?since=` query params to `GET /api/work-history`. Frontend adds a dropdown (populated from agent names in the response) and a date range selector (7d / 30d / all). Filter state is synced to URL search params via `useSearchParams`.

**Why server-side filtering for `since` but client-side for `agent`:** The `since` filter reduces GitHub API calls (can use `since` param on the GitHub PRs endpoint). Agent filtering is cheap client-side since the data set is small (<100 PRs).

**Revised:** Both filters will be implemented server-side for consistency and correctness. The `agent` param filters which PRs to include, the `since` param limits the date range. URL params are synced bidirectionally.

### 3.6 Cost-Per-Story (AC-6)

**Approach:** Add `total_cost_usd` field to `CompletedStory`. For each PR, the backend looks up cost data from Azure cost records for the story's date range. Since exact per-story cost attribution is complex (stories overlap in time), the initial implementation estimates cost by dividing the agent's daily cost by the number of concurrent stories that day, then summing across the story's active days.

**Fallback:** If cost data is unavailable, `total_cost_usd` returns `null` and the frontend shows "N/A".

### 3.7 Stacked CostChart (AC-7)

**Approach:** Extend frontend `CostHistoryEntry` to include `foundry_cost_usd`, `sdk_cost_usd`, `openai_cost_usd`. The backend already returns `DailyCost` with these fields — the agent detail route just needs to map them into the response. Refactor `CostChart` to use Recharts' `stackOffset="none"` with 3 `<Area>` elements.

**Colors:** Foundry = blue (#3b82f6), SDK = emerald (#10b981), OpenAI = amber (#f59e0b). Legend and series toggle via click.

---

## 4. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Backend cost breakdown fields return null for agents without Azure credentials configured | Medium | Low | Frontend renders "N/A" when null; warning icon pattern from STORY-038 already exists |
| R2 | Phase progress inaccurate for custom/epic phase paths | Low | Medium | Fall back to "Phase N" (no total) when scope is unknown or epic; only show "of M" for trivial/small/medium/large |
| R3 | Cost-per-story estimation inaccurate for overlapping stories | Medium | Low | Label as "Est." with tooltip explaining methodology; accept as known limitation |
| R4 | Work history `since` filter doesn't reduce GitHub API calls for older PRs (GitHub API returns most recent anyway) | Low | Low | `since` param on GitHub API filters by updated_at; combine with client-side date filtering on `completed_at` |
| R5 | Adding presence data to AgentCard increases the data dependency chain (fleet + presence) | Low | Low | Presence data is already cached (30s TTL) and loaded via existing `usePresence` hook; no new API calls |
| R6 | FleetOverviewBar wrapping at 1024px with 8+ cards | Medium | Low | Use `flex-wrap` (already present) and reduce `min-w` for the new Budget card; test at 1024px breakpoint |
| R7 | Stacked AreaChart harder to read for small values | Low | Low | Recharts tooltips show exact values; legend allows toggling visibility; consider switching to grouped bars if feedback is negative |

---

## 5. Dependencies

| Dependency | Status | Impact on This Story |
|------------|--------|---------------------|
| STORY-019 (Ops Console Frontend) | Merged | Base components we are modifying — no risk |
| STORY-426 (Dashboard Presence) | Phase 8 complete, PR #55 remediation in progress | Presence API and `usePresence` hook exist; if remediation changes the API shape, we adapt — low risk since the contract (`AgentPresenceItem`) is stable |
| STORY-038 (Foundry Cost as Primary) | Merged | Cost breakdown fields (`today_foundry_usd`, `today_sdk_usd`, `today_openai_usd`) on `AgentSummary` and `FleetAgentSummary` — already shipping |
| EPIC-004 (Foundry Cost Observability) | In progress | We depend only on the existing cost-source taxonomy, not on any specific child story — no risk |
| STORY-006 (SDLC Engine) | Merged | `SDLCEngine.PHASE_PATHS` provides scope → phase path mapping for phase progress — stable |
| CostService / AzureCostClient | Live | Already computes per-source costs per agent per day — no new external API calls needed |
| Recharts | Installed | Stacked AreaChart uses native Recharts stacking — no new library |
| TanStack React Query | Installed | All data hooks already use TanStack Query — no new patterns |

---

## 6. Alternatives Considered

### Budget Display

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **A: Progress bar in KPI card** | Visual, immediate, fits existing layout | Needs color thresholds, slightly larger card | **Selected** — best balance of information density and visual clarity |
| B: Percentage text only | Simplest | Easy to miss, no visual urgency cue | Rejected |
| C: Circular gauge widget | Visually striking | Requires new component, doesn't match KPI card pattern | Rejected — overengineered |

### Presence Integration

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **A: Colored dot inline on AgentCard, PresencePanel retained** | Self-contained cards, backward compatible | Slight visual noise on small cards | **Selected** — matches operator's mental model |
| B: Replace PresencePanel entirely with AgentCard dots | Removes duplication | Loses the "at-a-glance all agents" view operators may prefer | Rejected |
| C: Tooltip-only presence on AgentCard | Minimal visual change | Requires hover — defeats "5-second answer" goal | Rejected |

### Cost Chart Redesign

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **A: Stacked AreaChart (3 series)** | Shows composition and trend, native Recharts support | Harder to read individual series for small values | **Selected** — tooltips mitigate readability |
| B: Grouped bar chart | Easy to compare series | Loses trend continuity | Rejected for primary view |
| C: Three separate sparklines | Clear per-series trends | Takes 3x vertical space | Rejected — too much space |

---

## 7. Scope Confirmation

This analysis confirms Medium scope:
- **6 existing components modified** (no new pages, no new routes)
- **3 backend endpoints extended** (no new endpoints)
- **~215 lines frontend + ~80 lines backend** estimated net additions
- **No new infrastructure, auth flows, or data sources**
- **No database schema changes** (cost data already exists in Azure Cost Management; phase path data in SDLC engine)
- Phase path: 1 → 4 → 6 → [6b,6c,6d] → 7 → 8 → 8b → 11 → Done
