# Feature Spec: Dashboard Overhaul — STORY-480

> Phase 6 — Feature Specification (Medium)
> Date: 2026-04-20
> Scope: Medium
> Story: STORY-480

---

## 1. Technical Design Overview

This story enriches the existing ops console dashboard with four capabilities:

1. **Budget awareness** — Fleet bar shows daily spend as a % of budget with a progress bar
2. **Presence integration** — AgentCard shows live operational state inline
3. **Work detail depth** — Phase progress, elapsed time, cost-per-story, filtering
4. **Cost-source transparency** — Fleet bar and agent detail chart break down Foundry/SDK/OpenAI

All changes are additive modifications to existing components. No new pages, routes, or infrastructure.

---

## 2. API Changes

### 2.1 `GET /api/fleet` — FleetOverviewResponse

**New fields on `FleetOverviewResponse`:**

```python
class FleetOverviewResponse(BaseModel):
    # ... existing fields unchanged ...
    daily_budget_usd: float = 50.0          # NEW: from config (OPS_DAILY_BUDGET_USD)
    daily_foundry_usd: float = 0.0          # NEW: sum of per-agent foundry spend today
    daily_sdk_usd: float = 0.0              # NEW: sum of per-agent SDK spend today
    daily_openai_usd: float = 0.0           # NEW: sum of per-agent OpenAI spend today
```

**New fields on `FleetAgentSummary`:**

```python
class FleetAgentSummary(BaseModel):
    # ... existing fields unchanged ...
    current_phase: str | None = None        # NEW: current SDLC phase
    phase_total: int | None = None          # NEW: total phases in scope path
    phase_started_at: str | None = None     # NEW: ISO timestamp of current phase start
```

**Backend implementation:**
- `daily_budget_usd`: Read from `settings.daily_budget_usd` (new config field).
- `daily_foundry_usd`, `daily_sdk_usd`, `daily_openai_usd`: Summed from existing `FleetAgentSummary.today_foundry_usd` / `today_sdk_usd` / `today_openai_usd` fields in `_fleet_overview_inner()`.
- `current_phase`: Already available from `_resolve_current_work()` — needs to be propagated to `FleetAgentSummary`.
- `phase_total`: Resolved via `SDLCEngine.PHASE_PATHS[scope]` — requires knowing the story's scope, available from the dispatch queue (`DispatchItem.scope`).
- `phase_started_at`: New field on `DispatchItem`. Set when the dispatch poller transitions phases. Initially populated from the dispatch queue's `claimed_at` timestamp as a best-effort approximation.

### 2.2 `GET /api/agents/{name}` — AgentDetailResponse

**Extended `CostHistoryEntry` (frontend type, maps to `DailyCost` backend):**

The backend `DailyCost` model already includes `foundry_cost_usd`, `sdk_cost_usd`, `openai_cost_usd`. The agent detail route already returns `CostBreakdownResponse` with `daily: list[DailyCost]`. The change is on the **frontend** — extend `CostHistoryEntry` to consume these existing fields.

**New fields on `AgentDetailResponse`:**

```python
class AgentDetailResponse(BaseModel):
    # ... existing fields unchanged ...
    current_phase: str | None = None        # already exists
    phase_started_at: str | None = None     # NEW
    phase_total: int | None = None          # NEW
```

### 2.3 `GET /api/work-history` — WorkHistoryResponse

**New query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `agent` | `str \| None` | `None` | Filter PRs by agent name (case-insensitive) |
| `since` | `str \| None` | `None` | Filter by date: `7d`, `30d`, or ISO date. `None` = all |

**New fields on `CompletedStory`:**

```python
class CompletedStory(BaseModel):
    # ... existing fields unchanged ...
    total_cost_usd: float | None = None     # NEW: estimated total cost for this story
```

**Backend implementation:**
- `agent` param: Filter the extracted PRs by agent name before building the response.
- `since` param: Convert to a cutoff date; filter PRs by `completed_at >= cutoff`.
- `total_cost_usd`: For each story, look up the agent's daily costs for the date range the PR was active (created_at → merged_at/updated_at). Sum foundry + sdk + openai costs. Divide by concurrent story count for that agent on each day. Return null if cost data unavailable.

### 2.4 Frontend TypeScript Type Changes (`types/api.ts`)

```typescript
// Extend FleetOverview
export interface FleetOverview {
  // ... existing fields ...
  daily_budget_usd: number;       // NEW
  daily_foundry_usd: number;      // NEW
  daily_sdk_usd: number;          // NEW
  daily_openai_usd: number;       // NEW
}

// Extend AgentSummary
export interface AgentSummary {
  // ... existing fields ...
  phase_total: number | null;          // NEW
  phase_started_at: string | null;     // NEW
}

// Extend CostHistoryEntry
export interface CostHistoryEntry {
  date: string;
  cost: number;
  foundry_cost_usd?: number;    // NEW (optional for backward compat)
  sdk_cost_usd?: number;        // NEW
  openai_cost_usd?: number;     // NEW
}

// Extend CompletedStory
export interface CompletedStory {
  // ... existing fields ...
  total_cost_usd: number | null;   // NEW
}
```

---

## 3. Database Changes

**None.** All data sources already exist:
- Budget: config/env var
- Cost breakdown: Azure Cost Management API (via existing CostService)
- Phase progress: SDLC engine phase paths + dispatch queue
- Work history cost: derived from existing cost data

---

## 4. File-by-File Change Plan

### 4.1 Backend Changes

#### `tech_dev_agents/ops_console/config.py`

**Add:**
```python
# Budget
daily_budget_usd: float = 50.0  # OPS_DAILY_BUDGET_USD
```

**Location:** After the `fleet_cache_ttl` field (line ~51). Single line addition.

---

#### `tech_dev_agents/ops_console/models/responses.py`

**Modify `FleetOverviewResponse`** (line 213):
```python
class FleetOverviewResponse(BaseModel):
    # ... existing fields ...
    daily_budget_usd: float = 50.0
    daily_foundry_usd: float = 0.0
    daily_sdk_usd: float = 0.0
    daily_openai_usd: float = 0.0
```

**Modify `FleetAgentSummary`** (line 201):
```python
class FleetAgentSummary(BaseModel):
    # ... existing fields ...
    current_phase: str | None = None
    phase_total: int | None = None
    phase_started_at: str | None = None
```

**Modify `CompletedStory`** (line 233):
```python
class CompletedStory(BaseModel):
    # ... existing fields ...
    total_cost_usd: float | None = None
```

**Modify `AgentDetailResponse`** (line 96):
```python
class AgentDetailResponse(BaseModel):
    # ... existing fields ...
    phase_started_at: str | None = None
    phase_total: int | None = None
```

---

#### `tech_dev_agents/ops_console/routes/fleet.py`

**Modify `_fetch_agent_data()`** (line 60):
- Add `current_phase` resolution (reuse `_resolve_current_work` pattern or pass from caller)
- Add `phase_total` by looking up scope from dispatch queue and resolving via `SDLCEngine.PHASE_PATHS`
- Add `phase_started_at` from dispatch queue item's `claimed_at` (initial approximation)

**Modify `_fleet_overview_inner()`** (line 159):
- After computing `agent_summaries`, sum per-source costs:
  ```python
  daily_foundry = sum(a.today_foundry_usd for a in agent_summaries)
  daily_sdk = sum(a.today_sdk_usd for a in agent_summaries)
  daily_openai = sum(a.today_openai_usd for a in agent_summaries)
  ```
- Read `settings.daily_budget_usd` and pass to response.

**Estimated diff:** +30 lines

---

#### `tech_dev_agents/ops_console/routes/work_history.py`

**Modify `work_history()` endpoint** (line 91):
- Add `agent: str | None = Query(None)` and `since: str | None = Query(None)` parameters.
- After extracting PRs, filter by `agent` (case-insensitive match on `story.agent`).
- Parse `since` into a cutoff datetime: `7d` → 7 days ago, `30d` → 30 days ago, ISO string → parsed date.
- Filter stories by `completed_at >= cutoff`.
- For `total_cost_usd`: attempt to look up cost from `cost_service.get_cost_breakdown(agent_name, days)` for the story's active period. Divide by concurrent stories. Set to `None` on failure.

**Estimated diff:** +40 lines

---

#### `tech_dev_agents/ops_console/routes/agents.py`

**Modify agent detail endpoint** (~line 88+):
- Include `phase_started_at` and `phase_total` on the detail response.
- Source `phase_total` from `SDLCEngine.PHASE_PATHS` using the story's scope from dispatch queue.
- Source `phase_started_at` from dispatch queue item's `claimed_at`.

**Estimated diff:** +15 lines

---

### 4.2 Frontend Changes

#### `frontend/src/types/api.ts`

**Extend interfaces** as described in section 2.4. Approximately +10 lines net.

---

#### `frontend/src/components/FleetOverviewBar.tsx`

**Add 8th KPI card: "Budget Used":**
```tsx
<div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
  <p className="text-gray-400 text-xs uppercase tracking-wide">Budget Used</p>
  <p className={`text-xl font-bold ${budgetColor(budgetPercent)}`}>
    {budgetPercent}%
  </p>
  {/* Progress bar */}
  <div className="mt-1 h-1.5 bg-gray-600 rounded-full overflow-hidden">
    <div
      className={`h-full rounded-full ${budgetBarColor(budgetPercent)}`}
      style={{ width: `${Math.min(budgetPercent, 100)}%` }}
    />
  </div>
</div>
```

**Add cost-source breakdown under Daily Spend card:**
```tsx
<p className="text-gray-500 text-xs mt-1">
  Foundry {formatCurrency(data.daily_foundry_usd)} ·
  SDK {formatCurrency(data.daily_sdk_usd)} ·
  OpenAI {formatCurrency(data.daily_openai_usd)}
</p>
```

**New helper functions:**
- `budgetColor(percent: number)`: green < 70, yellow 70-90, red > 90
- `budgetBarColor(percent: number)`: bg-green-400 / bg-yellow-400 / bg-red-400

**Estimated diff:** +40 lines

---

#### `frontend/src/components/AgentCard.tsx`

**Add presence dot inline** (between name and status badge):
```tsx
<span className={`h-2.5 w-2.5 rounded-full ${presenceDotColor(presence?.state)}`} />
<span className={`text-xs ${presenceTextColor(presence?.state)}`}>
  {presenceLabel(presence?.state)}
</span>
```

**Add phase progress** (below current_phase):
```tsx
{agent.current_phase && (
  <div className="flex items-center gap-2 text-sm text-gray-500 mb-2">
    <span>
      {agent.current_phase}
      {agent.phase_total ? ` of ${agent.phase_total}` : ''}
    </span>
    {agent.phase_started_at && (
      <span className="text-xs text-gray-600">
        ({elapsedTime(agent.phase_started_at)})
      </span>
    )}
  </div>
)}
```

**New helper functions:**
- `presenceDotColor(state)`, `presenceTextColor(state)`, `presenceLabel(state)`: Reuse same color scheme as PresencePanel's `STATE_CONFIG`
- `elapsedTime(isoString)`: Compute hours/minutes since timestamp

**Props change:** AgentCard receives an optional `presence?: AgentPresenceItem` prop. The parent component (`Dashboard` or agent grid) fetches presence data once and distributes.

**Estimated diff:** +25 lines

---

#### `frontend/src/components/PresencePanel.tsx`

**Enhance `PresenceBubble`** — add story and phase detail text:
```tsx
function PresenceBubble({ agent }: { agent: AgentPresenceItem }) {
  const config = STATE_CONFIG[agent.state] ?? STATE_CONFIG.offline;
  return (
    <div className="flex items-center gap-2 p-2 rounded bg-gray-700/50" title={agent.detail ?? ""}>
      <span className={`h-3 w-3 rounded-full ${config.dot}`} />
      <div>
        <div className="text-sm text-gray-100">{agent.name}</div>
        <div className={`text-xs ${config.text}`}>{config.label}</div>
        {agent.detail && (
          <div className="text-xs text-gray-500 truncate max-w-[120px]">{agent.detail}</div>
        )}
      </div>
    </div>
  );
}
```

**Estimated diff:** +5 lines (the `detail` field already exists on `AgentPresenceItem`, just not rendered).

---

#### `frontend/src/components/WorkHistoryPanel.tsx`

**Add filter controls** above the table:
```tsx
<div className="flex gap-3 mb-4 flex-wrap">
  {/* Agent dropdown */}
  <select
    value={agentFilter}
    onChange={(e) => setAgentFilter(e.target.value)}
    className="bg-gray-700 text-gray-200 text-sm rounded px-3 py-1.5 border border-gray-600"
  >
    <option value="">All Agents</option>
    {uniqueAgents.map(a => <option key={a} value={a}>{a}</option>)}
  </select>

  {/* Date range */}
  <div className="flex gap-1">
    {['7d', '30d', 'all'].map(range => (
      <button
        key={range}
        onClick={() => setSinceFilter(range)}
        className={`text-xs px-3 py-1.5 rounded ${
          sinceFilter === range ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400'
        }`}
      >
        {range === 'all' ? 'All' : `Last ${range}`}
      </button>
    ))}
  </div>
</div>
```

**Add Cost column to table:**
```tsx
<th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Cost</th>
// In StoryRow:
<td className="px-3 py-2 text-sm text-gray-400">
  {story.total_cost_usd != null ? formatCurrency(story.total_cost_usd) : 'N/A'}
</td>
```

**URL param sync:**
```tsx
const [searchParams, setSearchParams] = useSearchParams();
const agentFilter = searchParams.get('agent') || '';
const sinceFilter = searchParams.get('since') || 'all';

const setAgentFilter = (agent: string) => {
  const params = new URLSearchParams(searchParams);
  agent ? params.set('agent', agent) : params.delete('agent');
  setSearchParams(params);
};
```

**Update query to pass params:**
```tsx
queryFn: () => api.get(`/api/work-history${agentFilter || sinceFilter !== 'all' ? '?' : ''}${agentFilter ? `agent=${agentFilter}` : ''}${sinceFilter !== 'all' ? `&since=${sinceFilter}` : ''}`),
queryKey: ['work-history', agentFilter, sinceFilter],
```

**Estimated diff:** +60 lines

---

#### `frontend/src/components/CostChart.tsx`

**Refactor to stacked AreaChart:**
```tsx
export function CostChart({ data }: { data: CostHistoryEntry[] | undefined | null }) {
  if (!data || data.length === 0) {
    return <div data-testid="cost-chart" className="...">No cost data available</div>;
  }

  const hasBreakdown = data.some(d => d.foundry_cost_usd !== undefined);

  if (!hasBreakdown) {
    // Fallback: single series (backward compat)
    return <SingleAreaChart data={data} />;
  }

  return (
    <div data-testid="cost-chart" className="w-full">
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={data} stackOffset="none">
          <defs>
            <linearGradient id="foundryGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="sdkGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="openaiGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="date" stroke="#9ca3af" />
          <YAxis stroke="#9ca3af" tickFormatter={(v) => `$${v}`} />
          <Tooltip formatter={(v, name) => [`$${v}`, name]} />
          <Legend />
          <Area type="monotone" dataKey="foundry_cost_usd" name="Foundry"
                stackId="1" stroke="#3b82f6" fill="url(#foundryGradient)" />
          <Area type="monotone" dataKey="sdk_cost_usd" name="SDK"
                stackId="1" stroke="#10b981" fill="url(#sdkGradient)" />
          <Area type="monotone" dataKey="openai_cost_usd" name="OpenAI"
                stackId="1" stroke="#f59e0b" fill="url(#openaiGradient)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
```

**Backward compatibility:** If `foundry_cost_usd` is undefined on the data entries, fall back to the original single-series chart. This ensures the component works during partial rollout.

**Estimated diff:** +30 lines (net, after removing old single-series code and adding stacked version + fallback).

---

#### `frontend/src/components/AgentDetailView.tsx`

**Add "Current Work" summary card** above the cost chart:
```tsx
{agent.current_story && (
  <div className="bg-gray-700 rounded-lg p-4">
    <h2 className="text-sm font-medium text-gray-400 uppercase mb-2">Current Work</h2>
    <p className="text-gray-100 text-lg font-semibold">{agent.current_story}</p>
    <div className="flex gap-4 mt-2 text-sm text-gray-400">
      {agent.current_phase && (
        <span>{agent.current_phase}{agent.phase_total ? ` of ${agent.phase_total}` : ''}</span>
      )}
      {agent.phase_started_at && (
        <span>{elapsedTime(agent.phase_started_at)} on this phase</span>
      )}
    </div>
  </div>
)}
```

**Estimated diff:** +20 lines

---

## 5. Configuration Changes

### New Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OPS_DAILY_BUDGET_USD` | float | `50.0` | Daily fleet spend budget in USD. Used for the Budget Used KPI calculation. |

### No New Secrets

The budget value is a non-sensitive configuration number. No new API keys, tokens, or credentials are introduced.

---

## 6. Rollback Strategy

### Frontend Rollback
All frontend changes are additive to existing components. Rolling back requires reverting the frontend build to the previous version. Since the dashboard is served as a static SPA, this is a simple deployment rollback:
```bash
# Revert to previous frontend build
git revert <merge-commit-sha>
npm run build && deploy
```

### Backend Rollback
All new response fields have defaults (`0.0`, `None`, `50.0`). Removing them is backward-compatible — the frontend renders gracefully when fields are absent (optional types, null checks, fallback values).

**No database migrations to roll back.**

### Partial Rollback
Individual features can be disabled by reverting specific component changes:
- Budget card: revert FleetOverviewBar only
- Presence on AgentCard: revert AgentCard only (PresencePanel still works standalone)
- Work history filters: revert WorkHistoryPanel only (backend params are optional, ignored when absent)
- Stacked chart: CostChart falls back to single series when breakdown fields are undefined

### Feature Flag Alternative
No feature flags are needed for this story. The changes are small enough that a git revert is faster and simpler than implementing conditional rendering. If the team later adopts feature flags, these could be retrofitted.

---

## 7. Performance Considerations

| Concern | Assessment | Mitigation |
|---------|-----------|------------|
| Extra API call for presence on AgentCard | None — reuses existing `usePresence` hook (single call, 30s cache) | Pass data from parent, not per-card |
| Fleet bar computes per-source aggregates | Negligible — sums 6 numbers per agent, already fetched | Already in-memory summation |
| Work history filtering | Reduces data transfer when `since` is set; no extra API calls | Server-side filtering before response |
| Cost-per-story calculation | New computation on `/api/work-history` — calls cost_service per agent | Cache cost data (already 5-min TTL); batch lookups by agent |
| Stacked chart rendering | Same Recharts instance, 3 series instead of 1 | No measurable difference for 7-30 data points |

---

## 8. Acceptance Criteria Traceability

| AC | Implementation | File(s) |
|----|---------------|---------|
| AC-1 | Budget Used KPI card with progress bar | FleetOverviewBar.tsx, fleet.py, config.py, responses.py |
| AC-2 | Cost-source breakdown text under Daily Spend | FleetOverviewBar.tsx, fleet.py, responses.py |
| AC-3 | Inline presence dot + label on AgentCard | AgentCard.tsx, types/api.ts (no backend change — reuses usePresence) |
| AC-4 | Phase progress "N of M" + elapsed time | AgentCard.tsx, fleet.py, agents.py, responses.py |
| AC-5 | Agent + date range filters with URL params | WorkHistoryPanel.tsx, work_history.py |
| AC-6 | Cost-per-story column | WorkHistoryPanel.tsx, work_history.py, responses.py |
| AC-7 | Stacked AreaChart with Foundry/SDK/OpenAI | CostChart.tsx, types/api.ts |
| AC-8 | Tests for all new/modified components | FleetOverviewBar.test.tsx, AgentCard.test.tsx, WorkHistoryPanel.test.tsx (new), CostChart.test.tsx (new), AgentDetailView.test.tsx |
| AC-9 | No regressions — 59+ existing tests pass | CI validation |
| AC-10 | Responsive at 1024px+ | flex-wrap on FleetOverviewBar (existing), min-w adjustments |

---

## 9. Implementation Order

Recommended implementation sequence for Phase 8:

1. **Backend config + models** — Add `daily_budget_usd` to config, extend response models (responses.py)
2. **Backend fleet route** — Add budget, per-source aggregates, phase progress fields
3. **Backend work-history route** — Add query params, cost-per-story field
4. **Backend agents route** — Add phase_started_at, phase_total to detail response
5. **Frontend types** — Extend api.ts interfaces
6. **FleetOverviewBar** — Budget card + cost breakdown
7. **AgentCard** — Presence dot + phase progress + elapsed time
8. **PresencePanel** — Detail text enhancement
9. **WorkHistoryPanel** — Filters + cost column + URL params
10. **CostChart** — Stacked AreaChart refactor
11. **AgentDetailView** — Current Work summary card
12. **Tests** — One test file per component change

Each step is a single commit. Tests follow each component change or are batched at the end per AC-8.
