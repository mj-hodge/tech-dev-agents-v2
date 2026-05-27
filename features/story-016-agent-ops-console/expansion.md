# Expansion: Agent Operations Console — Architectural Approaches

> Phase 3 — Expansion
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large

---

## Summary

This document presents three distinct architectural approaches for implementing the Agent Operations Console. Each approach is evaluated across six dimensions: implementation complexity, testability, reuse leverage, deployment simplicity, UX cohesion, and extensibility. A recommendation follows the comparative analysis.

The three approaches are:
- **Approach A: Monolith (FastAPI + Embedded SPA)** — Single process, maximum simplicity
- **Approach B: Split Services (FastAPI API + Separate React SPA)** — Clean separation, Nginx-routed
- **Approach C: Grafana-Embedded Hybrid** — Minimal new code, Grafana iframe panels for charts/logs

---

## Approach A: Monolith (FastAPI + Embedded SPA)

### Overview

A single FastAPI application serves both the REST API endpoints and the production-built React SPA. The React app is built at deploy time (`vite build`) and the resulting static files are mounted via FastAPI's `StaticFiles`. One systemd service, one process, one port.

### Architecture

```
ops-console/
├── backend/
│   ├── ops_console/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app + StaticFiles mount
│   │   ├── config.py            # Pydantic Settings (env vars)
│   │   ├── auth.py              # API key dependency
│   │   ├── routes/
│   │   │   ├── agents.py        # /api/agents, /api/agents/{name}
│   │   │   ├── fleet.py         # /api/fleet
│   │   │   ├── alerts.py        # /api/alerts
│   │   │   └── health.py        # /api/health
│   │   ├── services/
│   │   │   ├── agent_service.py # Health poll + registry
│   │   │   ├── cost_service.py  # Loki + Azure Cost API
│   │   │   ├── alert_service.py # Merge cost + health alerts
│   │   │   ├── monday_service.py# Story data
│   │   │   └── loki_client.py   # Loki HTTP query client
│   │   └── models/
│   │       └── responses.py     # Pydantic response models
│   └── tests/
│       ├── test_routes.py
│       ├── test_services.py
│       └── test_loki_client.py
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/client.ts
│   │   ├── components/
│   │   │   ├── FleetOverview.tsx
│   │   │   ├── AgentCard.tsx
│   │   │   ├── AgentDetail/
│   │   │   │   ├── CostChart.tsx
│   │   │   │   ├── ActivityTimeline.tsx
│   │   │   │   ├── AlertHistory.tsx
│   │   │   │   └── Controls.tsx
│   │   │   ├── AlertBanner.tsx
│   │   │   └── AlertPanel.tsx
│   │   ├── hooks/
│   │   │   ├── useAgents.ts
│   │   │   ├── useFleet.ts
│   │   │   └── useAlerts.ts
│   │   ├── types/api.ts
│   │   └── utils/format.ts
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   └── package.json
├── Makefile                      # build, dev, deploy targets
└── Dockerfile                    # Multi-stage: node build → python runtime
```

### Data Flow

```
Browser (React SPA)
    │
    │ GET /api/agents  (X-API-Key header)
    │
    ▼
FastAPI (uvicorn, port 8000)
    │
    ├─ auth.py validates API key (reuses health_api.validate_api_key)
    │
    ├─ routes/agents.py → services/agent_service.py
    │       │
    │       ├─ Load AgentRecord list from agent-registry.json
    │       ├─ Fan-out httpx.AsyncClient GET to each VM's /health endpoint
    │       │   └─ asyncio.gather with 5s timeout per VM
    │       ├─ Build AgentHealthSnapshot per VM (reuses agent_dashboard.build_health_snapshot)
    │       ├─ Fetch current story from Monday.com (reuses monday_agent.get_story)
    │       └─ Return merged AgentHealthResponse list
    │
    ├─ routes/fleet.py → services/cost_service.py + agent_service.py
    │       │
    │       ├─ Query Loki for [COST_SUMMARY] lines (today)
    │       ├─ Query Azure Cost Management API (daily granularity)
    │       ├─ Aggregate across all agents (reuses cost_dashboard.aggregate_usage)
    │       └─ Return FleetOverviewResponse
    │
    └─ routes/alerts.py → services/alert_service.py
            │
            ├─ evaluate_alerts() from cost_dashboard (cost thresholds)
            ├─ evaluate_health_alerts() from agent_dashboard (health alerts)
            ├─ Query Loki for [COST_ANOMALY] and [SDK_HEALTH] lines
            └─ Merge + sort by timestamp, return AlertResponse list
```

### Service Layer Design

Each service wraps existing module functions and adds HTTP/caching:

```python
# services/agent_service.py
class AgentService:
    def __init__(self, registry_path: str, http_client: httpx.AsyncClient):
        self._registry = self._load_registry(registry_path)
        self._http = http_client
        self._cache: dict[str, tuple[AgentHealthSnapshot, float]] = {}
        self._cache_ttl = 30  # seconds

    async def get_all_agents(self) -> list[AgentHealthResponse]:
        """Fan-out health check to all registered agents."""
        tasks = [self._fetch_health(agent) for agent in self._registry if agent.enabled]
        snapshots = await asyncio.gather(*tasks, return_exceptions=True)
        return [self._to_response(s) for s in snapshots if isinstance(s, AgentHealthSnapshot)]

    async def _fetch_health(self, agent: AgentRecord) -> AgentHealthSnapshot:
        """Fetch health from a single agent VM with caching."""
        cache_key = agent.name
        if cache_key in self._cache:
            snapshot, cached_at = self._cache[cache_key]
            if time.time() - cached_at < self._cache_ttl:
                return snapshot
        try:
            resp = await self._http.get(
                f"http://{agent.host}:{agent.port}/health", timeout=5.0
            )
            data = resp.json()
            snapshot = build_health_snapshot(
                agent_name=agent.name,
                last_activity=data.get("last_activity"),
                uptime_seconds=data.get("uptime_seconds", 0),
                active_sessions=data.get("active_sessions", 0),
                error_count=data.get("error_count", 0),
            )
        except (httpx.HTTPError, OSError):
            snapshot = build_health_snapshot(
                agent_name=agent.name, last_activity=None,
                uptime_seconds=0, active_sessions=0, error_count=0,
            )
        self._cache[cache_key] = (snapshot, time.time())
        return snapshot
```

```python
# services/cost_service.py
class CostService:
    def __init__(self, loki: LokiClient, azure: AzureCostClient | None):
        self._loki = loki
        self._azure = azure
        self._cost_cache: dict[str, tuple[Any, float]] = {}

    async def get_agent_costs(self, agent_name: str, days: int = 30) -> CostBreakdown:
        """Combine SDK costs (Loki) and Azure Foundry costs (Azure API)."""
        sdk_task = self._get_sdk_costs(agent_name, days)
        azure_task = self._get_azure_costs(agent_name, days) if self._azure else asyncio.sleep(0)
        sdk_costs, azure_costs = await asyncio.gather(sdk_task, azure_task)
        return CostBreakdown(
            agent_name=agent_name,
            daily_sdk=sdk_costs,
            daily_azure=azure_costs or [],
            total_sdk=sum(d.cost for d in sdk_costs),
            total_azure=sum(d.cost for d in azure_costs) if azure_costs else 0,
        )
```

### Frontend Key Components

#### FleetOverview.tsx
```tsx
function FleetOverview() {
  const { data: fleet } = useQuery({
    queryKey: ['fleet'],
    queryFn: () => api.getFleet(),
    refetchInterval: 60_000,
  });

  return (
    <div className="grid grid-cols-4 gap-4 p-4 bg-gray-900 text-white">
      <StatCard label="Daily Spend" value={`$${fleet?.total_daily_spend_usd.toFixed(2)}`} />
      <StatCard label="Active Agents" value={`${fleet?.active_agents}/${fleet?.total_agents}`} />
      <StatCard label="Stories In Progress" value={fleet?.stories_in_progress} />
      <StatCard label="Fleet Health" value={`${(fleet?.fleet_health_score * 100).toFixed(0)}%`} />
    </div>
  );
}
```

#### AgentCard.tsx
```tsx
const STATUS_COLORS = {
  online: 'bg-green-500',
  idle: 'bg-yellow-500',
  stuck: 'bg-orange-500',
  offline: 'bg-red-500',
};

function AgentCard({ agent }: { agent: AgentHealthResponse }) {
  return (
    <div className="rounded-lg border border-gray-700 p-4 hover:border-blue-500 cursor-pointer">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">{agent.agent_name}</h3>
        <span className={`px-2 py-1 rounded text-xs ${STATUS_COLORS[agent.status]}`}>
          {agent.status}
        </span>
      </div>
      <p className="text-sm text-gray-400 mt-1">{agent.current_story || 'No active story'}</p>
      <p className="text-sm text-gray-400">Phase: {agent.current_phase || '—'}</p>
      <p className="text-sm mt-2">Today: ${agent.today_cost_usd.toFixed(2)}</p>
    </div>
  );
}
```

### Deployment

```
Nginx (ops.gorillacommerce.ai:443)
  ├── / → proxy_pass http://127.0.0.1:8000  (FastAPI serves SPA + API)
  └── TLS: Let's Encrypt certbot
```

Single systemd service:
```ini
[Service]
ExecStart=/usr/bin/uvicorn ops_console.main:app --host 127.0.0.1 --port 8000
Environment=OPS_CONSOLE_API_KEY=...
Environment=LOKI_URL=https://grafana.gorillacommerce.ai
Environment=LOKI_API_KEY=...
```

### Strengths
1. **Simplest deployment** — One process, one port, one systemd service
2. **No CORS issues** — SPA and API on same origin
3. **Maximum reuse** — Imports existing modules directly; no serialization boundary
4. **Easy development** — `vite dev` proxies to `uvicorn` in dev mode

### Weaknesses
1. **Coupled deploys** — Frontend change requires backend redeploy (mitigated: `vite build` is fast)
2. **Single process** — If backend crashes, frontend is also unavailable
3. **Static file overhead** — FastAPI serving static files is slower than Nginx (mitigated: Nginx is the actual reverse proxy)

### Complexity Score: 4/10
### Testability Score: 9/10
### Implementation Effort: ~3 weeks (1 week backend, 1 week frontend, 1 week integration/polish)

---

## Approach B: Split Services (FastAPI API + Separate React SPA)

### Overview

The FastAPI backend and React frontend are deployed as separate artifacts. Nginx routes `/api/*` to the backend (port 8000) and `/*` to the frontend static files (served directly by Nginx). The two can be developed, built, and deployed independently.

### Architecture

```
ops-console-api/               # Backend repository/directory
├── ops_console/
│   ├── main.py                # FastAPI app (API only, no static files)
│   ├── config.py
│   ├── auth.py
│   ├── routes/
│   ├── services/
│   └── models/
└── tests/

ops-console-ui/                # Frontend repository/directory
├── src/
│   ├── App.tsx
│   ├── api/client.ts
│   ├── components/
│   ├── hooks/
│   └── types/
├── vite.config.ts
└── package.json
```

### Deployment

```
Nginx (ops.gorillacommerce.ai:443)
  ├── /api/*   → proxy_pass http://127.0.0.1:8000  (FastAPI)
  ├── /*       → /var/www/ops-console/dist/          (React static files)
  └── TLS: Let's Encrypt certbot
```

### Nginx Configuration

```nginx
server {
    listen 443 ssl;
    server_name ops.gorillacommerce.ai;

    ssl_certificate /etc/letsencrypt/live/ops.gorillacommerce.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ops.gorillacommerce.ai/privkey.pem;

    # API routes → FastAPI backend
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Frontend static files
    location / {
        root /var/www/ops-console/dist;
        try_files $uri $uri/ /index.html;  # SPA fallback
    }
}
```

### Data Flow

Same as Approach A internally, but with an explicit network boundary between frontend and backend. CORS must be configured:

```python
# main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ops.gorillacommerce.ai"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key"],
)
```

### Strengths
1. **Independent deployment** — Update frontend without touching backend (and vice versa)
2. **Nginx serves static files** — Faster than FastAPI's StaticFiles for production
3. **Clean separation** — API contract is explicit; easier to add other clients later (CLI, mobile)
4. **Standard pattern** — Most production React+API deployments use this model

### Weaknesses
1. **Two build artifacts** — Must coordinate deployments when API contract changes
2. **CORS configuration** — Extra middleware, header management
3. **More Nginx config** — Split routing adds operational complexity
4. **Dev experience** — Need to run both `vite dev` and `uvicorn` simultaneously

### Complexity Score: 5/10
### Testability Score: 9/10
### Implementation Effort: ~3.5 weeks (1 week backend, 1 week frontend, 1 week integration, 0.5 week deploy config)

---

## Approach C: Grafana-Embedded Hybrid

### Overview

Minimize new frontend code by embedding existing Grafana panels via iframes for cost charts and log queries. Build only a thin React shell for agent cards, controls, and alert banners. The FastAPI backend handles agent health/controls only; Grafana provides all cost/log visualization directly.

### Architecture

```
ops-console/
├── backend/
│   ├── ops_console/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── agents.py     # Health + status only
│   │   │   ├── fleet.py      # Agent list + basic stats
│   │   │   └── controls.py   # Restart, pause
│   │   └── services/
│   │       └── agent_service.py
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── FleetOverview.tsx     # Agent cards + status
│   │   │   ├── AgentDetail.tsx       # Controls + Grafana embeds
│   │   │   ├── GrafanaPanel.tsx      # iframe wrapper with auth
│   │   │   └── AlertBanner.tsx
│   │   └── config/
│   │       └── grafana-panels.ts     # Panel URLs by agent/metric
│   └── package.json
└── grafana/
    └── dashboards/
        ├── agent-cost-daily.json     # Grafana dashboard definition
        ├── agent-activity.json
        └── alert-history.json
```

### Grafana Panel Embedding

```tsx
function GrafanaPanel({ dashboardUid, panelId, vars }: GrafanaPanelProps) {
  const params = new URLSearchParams({
    orgId: '1',
    panelId: String(panelId),
    ...vars,
  });
  const url = `https://grafana.gorillacommerce.ai/d-solo/${dashboardUid}?${params}`;

  return (
    <iframe
      src={url}
      className="w-full h-64 border-0 rounded"
      title="Grafana Panel"
    />
  );
}
```

### Grafana Dashboards Needed

| Dashboard | Panels | Data Source |
|-----------|--------|-------------|
| Agent Cost Daily | Daily cost bar chart, cost breakdown pie chart | Loki (`[COST_SUMMARY]` query) |
| Agent Activity | Phase timeline, commit frequency | Loki (agent log queries) |
| Alert History | Alert table with filters | Loki (`[COST_ANOMALY]`, `[SDK_HEALTH]`) |

### Strengths
1. **Least new code** — No custom charting; Grafana handles all visualization
2. **Existing infrastructure** — Grafana Cloud already runs; just add dashboards
3. **Rich visualization** — Grafana has better chart interactivity than Recharts
4. **Log exploration** — Users can click through to full Grafana for deep dives

### Weaknesses
1. **Fragmented UX** — Mix of React components and Grafana iframes feels disjointed
2. **Grafana auth complexity** — Embedded panels require anonymous access or auth proxy; Grafana Cloud anonymous access has security implications
3. **Limited customization** — Agent cards, controls, and fleet overview still need React; can't embed those from Grafana
4. **Dual dependency** — Console now depends on both FastAPI backend AND Grafana Cloud availability
5. **Iframe limitations** — No cross-origin communication; can't style Grafana panels to match the console theme; responsive layout is difficult
6. **Maintenance overhead** — Dashboard JSON must be version-controlled and kept in sync with data model changes

### Complexity Score: 5/10 (less code but more configuration)
### Testability Score: 6/10 (Grafana panels are untestable from the console's test suite)
### Implementation Effort: ~2.5 weeks (0.5 week backend, 1 week frontend shell, 1 week Grafana dashboards)

---

## Comparative Analysis

| Dimension | Approach A (Monolith) | Approach B (Split) | Approach C (Grafana Hybrid) |
|-----------|----------------------|-------------------|-----------------------------|
| **Implementation Complexity** | 4/10 | 5/10 | 5/10 |
| **Testability** | 9/10 | 9/10 | 6/10 |
| **Reuse Leverage** | High (direct imports) | High (direct imports) | Medium (no cost service) |
| **Deployment Simplicity** | 10/10 (one process) | 7/10 (two artifacts) | 6/10 (three systems) |
| **UX Cohesion** | 9/10 (unified) | 9/10 (unified) | 5/10 (iframe seams) |
| **Extensibility** | 8/10 | 9/10 | 6/10 |
| **Dev Experience** | 9/10 (vite proxy) | 7/10 (two terminals) | 7/10 (Grafana config) |
| **Effort** | ~3 weeks | ~3.5 weeks | ~2.5 weeks |
| **Weighted Score** | **8.3** | **7.7** | **5.9** |

### Scoring Methodology

Weights: Implementation Complexity (15%), Testability (20%), Reuse Leverage (15%), Deployment Simplicity (15%), UX Cohesion (15%), Extensibility (10%), Dev Experience (10%).

---

## Recommendation

**Approach A (Monolith) for v1**, with a migration path to Approach B if the project grows beyond a single-user internal tool.

### Rationale

1. **Simplest possible deployment** — One systemd service, one Nginx proxy_pass rule. Mark (sole user) benefits from reliability over architecture elegance.

2. **Maximum reuse of existing modules** — Direct Python imports from `cost_dashboard`, `agent_dashboard`, `monday_agent`, `health_api`, `cost_collector`. No serialization boundary between the console backend and existing libraries.

3. **No CORS complexity** — SPA and API served from the same origin. Eliminates an entire class of bugs.

4. **Testability is identical** — Both A and B score 9/10. The monolith's service layer is just as testable as a split service.

5. **Fastest path to value** — 3 weeks vs. 3.5 weeks for split services. The extra 0.5 weeks buys nothing for a single-user tool.

6. **Approach C rejected** — The Grafana iframe approach saves ~0.5 weeks of implementation but introduces permanent UX debt (disjointed interface), auth complexity (Grafana anonymous access), and a dual-system dependency. The custom Recharts charts in Approach A are more maintainable and testable.

### Migration Path to Approach B

If in Phase 9 refinement we determine that independent frontend/backend deployments are needed:
1. Remove `StaticFiles` mount from `main.py`
2. Add CORS middleware
3. Update Nginx config to serve frontend static files directly
4. No code changes needed in routes, services, or frontend components

This migration is ~2 hours of work, so there is no risk in starting with Approach A.

---

## Implementation Roadmap (Approach A)

### Week 1: Backend Foundation
1. FastAPI app scaffold (`main.py`, `config.py`, `auth.py`)
2. Agent registry loader (parse `agent-registry.json` → `AgentRecord` list)
3. Agent service: health poll fan-out via `httpx.AsyncClient`
4. `/api/agents` and `/api/agents/{name}` routes
5. `/api/health` self-health endpoint
6. Loki client: `query_range` with `[COST_SUMMARY]` parsing
7. Cost service: SDK costs from Loki + optional Azure Cost API
8. `/api/agents/{name}/cost` and `/api/fleet` routes
9. Alert service: merge cost alerts + health alerts
10. `/api/alerts` route
11. Restart/pause endpoints: `/api/agents/{name}/restart`, `/api/agents/{name}/pause`

### Week 2: Frontend
1. Vite + React + TypeScript + Tailwind scaffold
2. API client with typed fetch wrapper and API key header injection
3. Login page (API key entry → localStorage)
4. Fleet Overview bar with TanStack Query polling
5. Agent Card grid with status badges
6. Agent Detail view with cost chart (Recharts), activity timeline, controls
7. Alert Banner (top-of-page anomaly warning)
8. Alert Panel (filterable table)

### Week 3: Integration & Polish
1. End-to-end testing (backend → Loki → frontend)
2. Error states (agent unreachable, Loki down, stale data indicators)
3. Responsive layout (desktop-first, basic tablet support)
4. Deployment: Nginx config, systemd service, Let's Encrypt SSL
5. Smoke test on `ops.gorillacommerce.ai`

### Module Dependency Graph

```
ops_console.main
├── ops_console.auth              (wraps health_api.validate_api_key)
├── ops_console.routes.agents     (→ services.agent_service)
│   ├── agent_dashboard.*         (build_health_snapshot, collect_dashboard_health, etc.)
│   └── monday_agent.*            (AgentMondayClient.get_story)
├── ops_console.routes.fleet      (→ services.cost_service + agent_service)
│   ├── cost_dashboard.*          (aggregate_usage, evaluate_alerts)
│   ├── services.loki_client      (NEW: Loki HTTP API)
│   └── services.azure_cost       (NEW: Azure Cost Management API, optional)
├── ops_console.routes.alerts     (→ services.alert_service)
│   ├── cost_dashboard.*          (evaluate_alerts)
│   ├── agent_dashboard.*         (evaluate_health_alerts)
│   └── services.loki_client      (anomaly/SDK health queries)
└── ops_console.routes.health     (→ self-health check)
```

### New Code Inventory

| Component | Type | Estimated Lines | Depends On |
|-----------|------|----------------|------------|
| `ops_console/main.py` | FastAPI app | ~80 | FastAPI, uvicorn |
| `ops_console/config.py` | Settings | ~40 | pydantic-settings |
| `ops_console/auth.py` | Auth dependency | ~20 | health_api |
| `ops_console/routes/*.py` (4 files) | Route handlers | ~200 | services |
| `ops_console/services/agent_service.py` | Agent health | ~120 | agent_dashboard, httpx |
| `ops_console/services/cost_service.py` | Cost aggregation | ~100 | cost_dashboard, loki_client |
| `ops_console/services/alert_service.py` | Alert merge | ~80 | cost_dashboard, agent_dashboard |
| `ops_console/services/monday_service.py` | Story data | ~60 | monday_agent |
| `ops_console/services/loki_client.py` | Loki HTTP | ~100 | httpx |
| `ops_console/services/azure_cost.py` | Azure Cost API | ~120 | httpx |
| `ops_console/models/responses.py` | Pydantic models | ~80 | pydantic |
| Frontend (all components) | React+TS | ~800 | React, Tailwind, Recharts |
| Tests (backend) | pytest | ~400 | pytest, httpx |
| **Total** | | **~2,200** | |

---

## Agent Registry Format

The console reads `agent-registry.json` as its source of truth. Proposed format:

```json
{
  "agents": [
    {
      "name": "dan",
      "host": "10.0.1.10",
      "port": 8080,
      "role": "developer",
      "enabled": true,
      "azure_resource_group": "rg-agent-dan",
      "monday_item_id": null
    },
    {
      "name": "derrick",
      "host": "10.0.1.11",
      "port": 8080,
      "role": "developer",
      "enabled": true,
      "azure_resource_group": "rg-agent-derrick",
      "monday_item_id": null
    }
  ]
}
```

This extends `AgentRecord` with Azure resource group mapping (for cost API) and optional Monday.com item ID.

---

## Risk Mitigation Matrix

| Risk | Approach A Mitigation |
|------|----------------------|
| Azure Cost API 24-48h delay | Show "estimated" badge; primary cost view uses Loki real-time data |
| Agent VM unreachable | 5s timeout in httpx; `collect_dashboard_health` returns OFFLINE gracefully |
| Loki query slow for 30-day range | Query `[COST_SUMMARY]` pre-aggregated lines; cache 15-min TTL |
| Monday.com rate limits | 5-min cache TTL; only fetch for expanded agent cards |
| Single process crash | systemd `Restart=always` with 5s delay; Nginx returns 502 during restart |
| API key in localStorage | CSP headers restrict XSS vectors; HttpOnly cookie alternative for v2 |
