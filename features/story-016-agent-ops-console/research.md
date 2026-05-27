# Research Report: Agent Operations Console

> Phase 2 — Research
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large

---

## Summary

| Metric | Value |
|--------|-------|
| Research areas covered | 7 |
| Existing modules analyzed | 6 |
| Reusable data models identified | 14 |
| Key decisions surfaced | 6 |
| Critical risks flagged | 4 |

---

## Research Area 1: Existing Module Reuse Inventory

### Overview

Six existing Python modules provide the core data models, business logic, and API builders that STORY-016 will wrap in a FastAPI web application. This is the single most important research area because it determines how much new code is needed vs. how much is pure integration.

### Module-by-Module Analysis

#### 1. cost_dashboard.py (STORY-012)

**Reusable artifacts:**
- `CostEvent` (frozen dataclass) — Single cost observation tied to an agent (source, amount, tokens, session_id, timestamp)
- `UsageMetrics` (frozen dataclass) — Aggregated usage per agent over a time period (sessions, turns, tokens, cost breakdown by source)
- `AgentStatus` (frozen dataclass) — Health/activity status with auto-classification (online/idle/stuck/offline)
- `CostAlert` (frozen dataclass) — Threshold breach notification
- `AlertThreshold` — Configurable per-agent or wildcard cost limits
- `CostSource` enum — `claude_sdk` and `azure_foundry`
- `AgentActivityStatus` enum — Used across multiple modules
- `build_cost_event()` — Factory with validation (non-empty name, non-negative amounts)
- `aggregate_usage()` — Filters events by agent/period, aggregates by source
- `evaluate_alerts()` — Daily/weekly threshold evaluation with wildcard support
- `classify_agent_status()` — Time-based classification from last_activity timestamp
- `build_agent_status()` — Factory combining classification with story context

**Console integration:** Direct use for `/api/agents/{name}/cost` endpoint (aggregate_usage), `/api/alerts` endpoint (evaluate_alerts), and fleet overview (aggregate across all agents).

**Gap:** No async variants. All functions are synchronous. For FastAPI, these can be called in sync endpoints or wrapped with `run_in_executor` if needed.

#### 2. agent_dashboard.py (STORY-014)

**Reusable artifacts:**
- `AgentRecord` (frozen dataclass) — Registry entry (name, host, port, role, enabled)
- `AgentHealthSnapshot` (frozen dataclass) — Point-in-time health (status, uptime, active_sessions, error_count, checked_at)
- `SessionInfo` (frozen dataclass) — Active session details (story_id, phase, is_stuck)
- `RestartCommand` / `RestartResult` (frozen dataclasses) — Restart request/response with audit trail
- `DashboardAlert` (frozen dataclass) — Health-based alerts with cooldown_key for dedup
- `collect_dashboard_health()` — Polls all enabled agents via a health_fetcher callable; gracefully handles timeouts/errors as OFFLINE
- `evaluate_health_alerts()` — Evaluates snapshots for offline/stuck/high_errors conditions with cooldown
- `find_stuck_sessions()` — Filters sessions where is_stuck == True
- `validate_restart()` — Validates restart command against registry
- All factory functions: `build_agent_record`, `build_health_snapshot`, `build_session_info`, `build_restart_command`, `build_restart_result`

**Console integration:** This is the backbone of `/api/agents` (collect_dashboard_health), `/api/agents/{name}/restart` (validate_restart + build_restart_response), and the alert system (evaluate_health_alerts).

**Gap:** `collect_dashboard_health` takes a `health_fetcher` callable — the console backend must implement this callable to make HTTP requests to each agent VM's `/health` endpoint.

#### 3. monday_agent.py (STORY-013)

**Reusable artifacts:**
- `AgentIdentity` (frozen dataclass) — Per-agent Monday.com credentials (agent_name, api_token, board_id)
- `PhaseComment` (frozen dataclass) — Structured phase-transition comment with `render()` method
- `StoryStatusTransition` (frozen dataclass) — Story moving between Monday.com groups
- `AgentMondayClient` class — Wraps `MondayClient` with agent identity
  - `post_phase_comment()` — Post structured update
  - `move_story()` — Move between groups (Backlog, In Progress, Done, etc.)
  - `get_story()` — Fetch story details (id, name, description, group, status)
- `PHASE_NAMES` dict — Phase number to display name mapping
- `PHASE_GROUP_MAP` dict — Phase number to Monday.com group mapping
- `format_duration()` — Seconds to human-readable duration string

**Console integration:** `get_story()` feeds the activity feed for each agent. `PHASE_NAMES` provides display text for phase badges. The client requires a per-agent `AgentIdentity`, meaning the console backend needs access to Monday.com API tokens.

**Gap:** `MondayClient` (underlying) uses `requests` (synchronous HTTP). For async FastAPI, either use `httpx` replacement or thread pool.

#### 4. health_api.py (STORY-015)

**Reusable artifacts:**
- `validate_api_key()` — HMAC-based constant-time API key comparison; raises `AuthError`
- `build_health_response()` — Returns JSON-serializable dict from health snapshot
- `build_restart_response()` — Returns JSON-serializable dict from restart attempt; accepts optional `restart_fn` callback

**Console integration:** `validate_api_key` is directly usable as a FastAPI dependency for auth. `build_health_response` and `build_restart_response` return plain dicts — perfect for FastAPI `JSONResponse`.

**Gap:** None. This module was explicitly designed as a framework-agnostic API layer.

#### 5. cost_collector.py (STORY-015)

**Reusable artifacts:**
- `CostSummary` (frozen dataclass) — Daily cost summary per agent (sdk_sessions, sdk_cost, sdk_turns)
- `parse_done_lines()` — Regex extraction of `[DONE] cost=$X turns=Y` from log text
- `collect_costs_from_logs()` — Scans log directory for `session-*.log` files, aggregates
- `format_cost_summary_log()` — Formats `[COST_SUMMARY]` structured log line for Loki

**Console integration:** `parse_done_lines` can be repurposed for real-time Loki query result parsing. `collect_costs_from_logs` is designed for local filesystem — the console will query Loki instead, but can reuse the regex pattern.

**Gap:** The collector is designed for cron-job local execution. The console backend needs a Loki HTTP API client to query the same data remotely.

#### 6. monday_hooks.py (STORY-015)

**Reusable artifacts:**
- `on_story_start()` — Moves story to "In Progress"; returns `StoryStatusTransition`
- `on_story_complete()` — Moves story to "Done" + posts completion comment
- `on_phase_complete()` — Posts structured phase-transition comment

**Console integration:** These hooks are called by the agent runtime, not the console. However, the console could display the results of these hooks (e.g., show recent phase transitions from Monday.com).

**Gap:** Read-only for the console; the console doesn't trigger these hooks directly.

### Reuse Summary

| Category | New Code Needed | Reused From |
|----------|----------------|-------------|
| Cost data models | None | cost_dashboard.py |
| Agent registry models | None | agent_dashboard.py |
| Health snapshot models | None | agent_dashboard.py |
| Restart models | None | agent_dashboard.py |
| Alert evaluation | None | cost_dashboard.py + agent_dashboard.py |
| API key auth | None | health_api.py |
| Health response builders | None | health_api.py |
| Monday.com story fetch | None | monday_agent.py |
| Log cost parsing regex | Adapt | cost_collector.py |
| FastAPI app + routes | **New** | — |
| Loki query client | **New** | — |
| Azure Cost Management client | **New** | — |
| Agent VM health fetcher (HTTP) | **New** | — |
| React frontend | **New** | — |
| Auth middleware | **New** (small) | health_api.py pattern |

**Conclusion:** ~70% of backend data models and business logic already exists. New code is primarily: (1) FastAPI application shell + routes, (2) external service clients (Loki, Azure Cost API), (3) health fetcher HTTP implementation, (4) entire React frontend.

---

## Research Area 2: React + TypeScript + Tailwind Frontend Architecture

### Pattern Match: product-health-dashboard

The seed specifies matching the `product-health-dashboard` pattern (Dan's other project). Standard modern React SPA conventions apply:

**Recommended stack:**
- **React 18+** with functional components and hooks
- **TypeScript** for type safety (matching backend Pydantic models)
- **Tailwind CSS** for utility-first styling (no custom CSS files needed)
- **Vite** as build tool (fast HMR, ESM-native, lightweight vs. Webpack/CRA)
- **Recharts** for cost/usage charts (lightweight, React-native, composable)
- **React Query (TanStack Query)** for server state management (auto-refresh, caching, stale-while-revalidate)

### Component Architecture

```
src/
├── App.tsx                    # Router + layout
├── api/
│   └── client.ts              # Typed fetch wrapper (API key in header)
├── components/
│   ├── FleetOverview.tsx       # Top bar: spend, agents, stories, health
│   ├── AgentCard.tsx           # Per-agent summary card
│   ├── AgentDetail/
│   │   ├── CostChart.tsx       # Recharts daily cost breakdown
│   │   ├── ActivityTimeline.tsx # Phase transitions, commits
│   │   ├── AlertHistory.tsx    # Agent-specific alerts
│   │   └── Controls.tsx        # Restart, pause, enable/disable buttons
│   ├── AlertBanner.tsx         # Top-of-page anomaly warning
│   └── AlertPanel.tsx          # Filterable alert table
├── hooks/
│   ├── useAgents.ts            # React Query hook for /api/agents
│   ├── useFleet.ts             # React Query hook for /api/fleet
│   └── useAlerts.ts            # React Query hook for /api/alerts
├── types/
│   └── api.ts                  # TypeScript interfaces matching backend models
└── utils/
    └── format.ts               # Currency, date, duration formatters
```

### Key Design Decisions

1. **No SSR needed** — This is an internal ops tool, not a public-facing site. Vite SPA is sufficient.
2. **React Query for polling** — `refetchInterval: 30000` (30s) for agent status, `60000` (60s) for fleet overview. No WebSocket needed for MVP.
3. **Tailwind color coding for status** — `online` = green-500, `idle` = yellow-500, `stuck` = orange-500, `offline` = red-500. Matches `AgentActivityStatus` enum values.
4. **TypeScript interfaces mirror Python dataclasses** — Ensures type safety end-to-end. Example: `AgentHealthSnapshot` Python dataclass maps to `AgentHealthSnapshot` TypeScript interface.

### Build & Serve

- **Development:** `vite dev` on port 5173 with proxy to FastAPI backend on port 8000
- **Production:** `vite build` → static files in `dist/` → served by FastAPI `StaticFiles` mount or Nginx
- **Bundle:** The console frontend is ~50KB gzipped (React + Tailwind + Recharts). No heavy dependencies.

---

## Research Area 3: FastAPI Backend Architecture

### Application Structure

```
ops_console/
├── __init__.py
├── main.py                  # FastAPI app, CORS, static files, startup
├── config.py                # Settings (env vars, API keys, Loki URL, etc.)
├── auth.py                  # API key dependency (reuses health_api.validate_api_key)
├── routes/
│   ├── agents.py            # /api/agents, /api/agents/{name}
│   ├── fleet.py             # /api/fleet
│   ├── alerts.py            # /api/alerts
│   └── health.py            # /api/health (console self-health)
├── services/
│   ├── agent_service.py     # Orchestrates agent_dashboard + health fetcher
│   ├── cost_service.py      # Orchestrates cost_dashboard + Loki + Azure
│   ├── alert_service.py     # Merges cost alerts + health alerts
│   ├── monday_service.py    # Wraps monday_agent for story data
│   └── loki_client.py       # Loki HTTP API query client
├── models/
│   └── responses.py         # Pydantic response models (mirrors dataclasses)
└── tests/
    └── ...
```

### Key Patterns

1. **Service layer** — Routes are thin; business logic lives in service classes that compose existing module functions. Example: `agent_service.get_agent_detail()` calls `build_health_snapshot()`, `aggregate_usage()`, and `monday_client.get_story()` then merges results.

2. **Dependency injection** — FastAPI `Depends()` for:
   - `get_api_key()` — Header-based auth using `validate_api_key()`
   - `get_agent_registry()` — Returns list of `AgentRecord` from registry JSON
   - `get_loki_client()` — Singleton Loki HTTP client
   - `get_monday_client()` — Configured `AgentMondayClient`

3. **Async HTTP** — Use `httpx.AsyncClient` for:
   - Agent VM health polls (fan-out to N VMs concurrently with `asyncio.gather`)
   - Loki query_range API calls
   - Azure Cost Management API calls

4. **Caching** — In-memory cache with TTL:
   - Agent health snapshots: 30s TTL (health changes frequently)
   - Cost data: 15min TTL (expensive to compute, changes slowly)
   - Fleet overview: 60s TTL (aggregate of health + cost)
   - Monday.com story data: 5min TTL (changes infrequently)

5. **Error handling** — Consistent error responses:
   - 401 for invalid/missing API key
   - 404 for unknown agent name
   - 502 for upstream service failure (Loki, Monday.com, agent VM)
   - 503 when console itself is degraded

6. **CORS** — Allow `ops.gorillacommerce.ai` origin (and `localhost:5173` in dev)

### Pydantic Response Models

Map directly from existing frozen dataclasses:

```python
class AgentHealthResponse(BaseModel):
    agent_name: str
    status: str  # "online" | "idle" | "stuck" | "offline"
    last_activity: str | None
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str
    current_story: str | None
    current_phase: str | None
    today_cost_usd: float

class FleetOverviewResponse(BaseModel):
    total_daily_spend_usd: float
    active_agents: int
    total_agents: int
    stories_in_progress: int
    total_tests_passing: int
    fleet_health_score: float  # 0.0-1.0
    agents: list[AgentHealthResponse]
```

---

## Research Area 4: Authentication Approach

### MVP: API Key Auth

The simplest approach, consistent with existing `health_api.py` pattern:

1. **Single static API key** stored in environment variable `OPS_CONSOLE_API_KEY`
2. **Browser sends API key** via `X-API-Key` header on every request
3. **Login page** — User enters API key; stored in `localStorage`; attached to all fetch requests
4. **Backend validates** using `health_api.validate_api_key()` (HMAC constant-time comparison)

**Pros:** Zero external dependencies, fast to implement, consistent with existing pattern.
**Cons:** Single shared key (no per-user audit trail), key in localStorage (XSS risk, mitigated by same-origin policy + CSP headers).

### Future: Azure AD SSO

Post-MVP enhancement:
- Azure AD application registration with OIDC
- Frontend uses MSAL.js for token acquisition
- Backend validates JWT bearer tokens
- Per-user identity and audit trail
- Requires Azure AD tenant admin consent

**Decision: Ship MVP with API key. Plan Azure AD SSO as Phase 9 refinement item.**

---

## Research Area 5: Deployment Target

### Option A: Azure VM (Same as Agent VMs)

- **Approach:** Dedicated VM or co-locate with existing infrastructure VM
- **Proxy:** Nginx reverse proxy with Let's Encrypt SSL for `ops.gorillacommerce.ai`
- **Process:** systemd service running `uvicorn ops_console.main:app`
- **Cost:** ~$30/month for B2s VM (or $0 if co-located)
- **Pros:** Same pattern as agent VMs, full control, simple
- **Cons:** Manual scaling, manual updates, another VM to manage

### Option B: Azure Container App

- **Approach:** Containerized FastAPI app deployed to Azure Container Apps
- **Proxy:** Built-in HTTPS ingress with custom domain
- **Process:** Container runs uvicorn; auto-scaled 0-1 instances
- **Cost:** ~$5-15/month (scale-to-zero when not in use)
- **Pros:** Managed infrastructure, auto-TLS, zero-downtime deploys
- **Cons:** Slightly more complex deployment pipeline, container registry needed

### Recommendation

**Option A (Azure VM) for MVP** — Matches existing deployment patterns. The ops console is a low-traffic internal tool (1 user); managed container infrastructure is overkill. Co-locate on the infrastructure VM that already runs Nginx.

**Migration path:** Containerize in Phase 9 refinement if the VM approach becomes a management burden.

---

## Research Area 6: Loki Integration for Log Queries

### Loki HTTP API

**Endpoint:** `https://grafana.gorillacommerce.ai/loki/api/v1/query_range`

**Authentication:** Grafana Cloud API key (basic auth or bearer token)

**Query patterns needed by the console:**

| Query | LogQL | Purpose |
|-------|-------|---------|
| SDK session costs | `{job="agent-logs"} |= "[DONE]" | pattern "<_> cost=$<cost> <_>"` | Per-agent cost extraction |
| Cost anomalies | `{job="agent-logs"} |= "[COST_ANOMALY]"` | Active anomaly alerts |
| SDK health checks | `{job="agent-logs"} |= "[SDK_HEALTH]"` | SDK process monitoring |
| Terminal guard denials | `{job="agent-logs"} |= "[TERMINAL_GUARD]" |= "DENIED"` | Security events |
| Cost summaries | `{job="agent-logs"} |= "[COST_SUMMARY]"` | Pre-aggregated daily costs |

**Response format:** JSON with `result` array containing `stream` (labels) and `values` (timestamp + log line pairs).

### Client Implementation

```python
class LokiClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def query_range(self, query: str, start: str, end: str, limit: int = 1000) -> list[dict]:
        """Execute LogQL query and return parsed results."""
        params = {"query": query, "start": start, "end": end, "limit": limit}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/loki/api/v1/query_range",
                                     params=params, headers=self.headers)
            resp.raise_for_status()
            return self._parse_streams(resp.json())
```

### Reuse of cost_collector.py Regex

The `parse_done_lines()` regex from cost_collector.py can parse Loki query results:
```python
DONE_PATTERN = r'\[DONE\](?:.*?cost=\$(?P<cost>[\d.]+))?(?:.*?turns=(?P<turns>\d+))?'
```

This eliminates the need for a new parser — just apply the existing regex to Loki response lines.

### Performance Considerations

- **Default time range:** 7 days (avoid scanning months of logs)
- **Caching:** Cache Loki results with 15-min TTL (costs don't change retroactively)
- **Pagination:** Loki supports `limit` parameter; use 1000 for cost queries
- **Pre-aggregated data:** The `[COST_SUMMARY]` lines (written by cost_collector cron) are already daily aggregates — prefer querying these over raw `[DONE]` lines for historical views

---

## Research Area 7: Azure Cost Management API Access Pattern

### API Overview

Azure Cost Management provides REST APIs for querying Azure resource costs.

**Endpoint:** `https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.CostManagement/query`

**Authentication:** Service principal with `Cost Management Reader` role assignment

**Query format:** POST with JSON body specifying:
- Time period (daily, monthly, custom range)
- Granularity (daily, monthly)
- Grouping dimensions (resource group, resource name, meter category)
- Filter (resource group, tags)

### Relevant Query: Per-Agent Azure Foundry Spend

Each agent VM is a separate Azure resource. Azure AI Foundry (f.k.a. Azure OpenAI) costs are tagged or grouped by resource:

```json
{
  "type": "ActualCost",
  "timeframe": "Custom",
  "timePeriod": {"from": "2026-03-01", "to": "2026-04-01"},
  "dataset": {
    "granularity": "Daily",
    "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
    "grouping": [{"type": "Dimension", "name": "ResourceGroupName"}]
  }
}
```

### Authentication Flow

1. **Service principal** with client credentials grant (client_id + client_secret)
2. **Token endpoint:** `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`
3. **Scope:** `https://management.azure.com/.default`
4. **Token caching:** Cache access token until 5 min before expiry (tokens are typically valid for 1 hour)

### Implementation

```python
class AzureCostClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, subscription_id: str):
        self.token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        self.base_url = f"https://management.azure.com/subscriptions/{subscription_id}"
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    async def get_daily_costs(self, start_date: str, end_date: str) -> dict[str, list[dict]]:
        """Returns daily costs grouped by resource group (mapped to agent name)."""
        ...
```

### Rate Limits & Latency

- **Rate limit:** 30 requests per minute per subscription
- **Latency:** 2-5 seconds per query (cost data is pre-aggregated by Azure)
- **Data freshness:** Azure cost data has a 24-48 hour delay for actual costs; estimated costs are available within hours
- **Mitigation:** Cache with 15-min TTL; show "data as of" timestamp; use estimated costs for same-day view

### Mapping Azure Resources to Agents

Agent VMs are in dedicated resource groups or tagged with agent names. The console config maps resource group names to agent names:

```python
AGENT_AZURE_MAP = {
    "rg-agent-dan": "dan",
    "rg-agent-derrick": "derrick",
}
```

---

## Key Decisions Surfaced

| # | Decision | Options | Recommendation | Rationale |
|---|----------|---------|----------------|-----------|
| KD-1 | Frontend framework | React+Vite vs. Next.js vs. Svelte | React+Vite | Matches product-health-dashboard pattern; no SSR needed; team familiarity |
| KD-2 | Data fetching strategy | Polling (React Query) vs. WebSocket vs. SSE | Polling (30s interval) | Simple, sufficient for 1-2 users; WebSocket adds complexity for no gain at this scale |
| KD-3 | Backend async strategy | Full async (httpx) vs. sync (requests) in thread pool | Full async with httpx | FastAPI is async-native; fan-out health polls to N VMs benefit from asyncio.gather |
| KD-4 | Auth for MVP | API key vs. Azure AD | API key | Fast to ship, consistent with health_api.py pattern, one user |
| KD-5 | Deployment target | Azure VM vs. Container App | Azure VM (co-locate) | Matches existing patterns, zero additional cost, simple |
| KD-6 | Cost data source | Loki only vs. Loki + Azure Cost API | Both | Loki for SDK costs (real-time), Azure Cost API for Foundry costs (delayed but authoritative) |

---

## Critical Risks Flagged

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| CR-1 | Azure Cost Management API 24-48h data delay | Users see stale Azure Foundry costs | High | Show "estimated" badge for same-day costs; use Loki `[COST_SUMMARY]` for real-time SDK costs |
| CR-2 | Agent VMs unreachable from console server | Health status shows "unknown" | Medium | 5s timeout per VM; show last-known status with timestamp; existing `collect_dashboard_health` handles this gracefully |
| CR-3 | Loki query performance for 30-day chart | Slow page load for cost history | Medium | Query `[COST_SUMMARY]` pre-aggregated lines instead of raw `[DONE]` lines; cache with 15-min TTL |
| CR-4 | Monday.com API rate limits (60 req/min) | Story data fetch fails under load | Low | Cache story data with 5-min TTL; batch requests where possible; only fetch for currently-viewed agents |

---

## Dependency Health

| Dependency | Version | Status | Notes |
|------------|---------|--------|-------|
| FastAPI | 0.111+ | Stable | Well-maintained, async-first |
| uvicorn | 0.30+ | Stable | ASGI server |
| httpx | 0.27+ | Stable | Async HTTP client (replaces requests for async) |
| React | 18+ | Stable | LTS, hooks-based |
| Vite | 5+ | Stable | Fast build tool |
| Tailwind CSS | 3.4+ | Stable | Utility-first CSS |
| Recharts | 2.12+ | Stable | React charting library |
| TanStack Query | 5+ | Stable | Server state management |
| pydantic | 2+ | Stable | Already in ecosystem (FastAPI requirement) |

---

## Recommendation for Expansion

Three architectural approaches should be explored in Phase 3:

1. **Monolith API + SPA** — Single FastAPI process serves both API endpoints and the built React SPA via `StaticFiles` mount. Simplest deployment, single process to manage.

2. **Split Backend/Frontend** — FastAPI API server and React SPA deployed separately. Nginx routes `/api/*` to backend, `/*` to frontend static files. More flexible but two build artifacts.

3. **Grafana-Embedded Hybrid** — Minimal FastAPI API for agent controls only; embed Grafana panels via iframes for cost charts and log queries. Least new code but less cohesive UX and dependent on Grafana configuration.

---

## Sources Referenced

- Existing codebase: `cost_dashboard.py`, `agent_dashboard.py`, `monday_agent.py`, `health_api.py`, `cost_collector.py`, `monday_hooks.py`
- Seed document: `features/story-016-agent-ops-console/seed.md`
- FastAPI documentation: https://fastapi.tiangolo.com/
- Loki HTTP API: https://grafana.com/docs/loki/latest/reference/loki-http-api/
- Azure Cost Management REST API: https://learn.microsoft.com/en-us/rest/api/cost-management/
- TanStack Query: https://tanstack.com/query/latest
- Recharts: https://recharts.org/
