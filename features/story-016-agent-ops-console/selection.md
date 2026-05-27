# Selection: Agent Operations Console — Approach A (Monolith)

> Phase 5 — Selection
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large

---

## Selected Approach

**Approach A: Monolith (FastAPI + Embedded SPA)**

A single FastAPI application serves both the REST API and the production-built React SPA. One process, one port, one systemd service, one Nginx reverse proxy rule.

---

## Rationale

### Why Approach A

1. **Highest success criteria score (9.6/10)** — Meets or exceeds all 8 success criteria. No criterion scores below 9. The closest competitor (Approach B) scored 9.5/10 — a marginal difference that doesn't justify the added operational complexity.

2. **Lowest risk profile** — All identified risks (single-process crash, coupled deploys, static file performance) have trivial mitigations. No external system dependencies beyond what the API already requires (Loki, Azure Cost API, Monday.com).

3. **Maximum module reuse (70%)** — Six existing Python modules (`cost_dashboard`, `agent_dashboard`, `monday_agent`, `health_api`, `cost_collector`, `monday_hooks`) are imported directly into the service layer. No serialization boundaries, no duplicate business logic, no query reimplementation.

4. **Simplest deployment** — One systemd service, one Nginx `proxy_pass` rule, one `vite build` step. Matches Mark's operational capacity as a solo operator managing the agent fleet.

5. **Scope-appropriate** — A monolith is the right architecture for a single-user internal tool. The "Large story, not Epic" classification confirms this should be delivered as one cohesive unit, not decomposed into independently deployable services.

6. **Fastest path to value (3 weeks)** — One week less than the project's conservative estimate, half a week faster than Approach B.

### Why Not Approach B (Split Services)

Approach B scored 9.5/10 — nearly identical to A. The differences are marginal:
- CORS middleware adds a debugging surface area with zero benefit for a same-origin deployment
- Two systemd services and split Nginx routing add operational overhead
- Independent deployment capability is unnecessary when there is one developer and one user
- The extra 0.5 weeks of implementation effort buys nothing for an internal tool

**Verdict:** Approach B is a valid architecture for a team-maintained production service. It is over-engineered for a solo-operator internal tool.

### Why Not Approach C (Grafana Hybrid)

Approach C scored 7.0/10 — significantly lower. Critical issues:
- **UX fragmentation (permanent):** React components + Grafana iframes create visual seams that can never be fully resolved. Dark theme colors, fonts, spacing, and interaction patterns differ between the two.
- **Grafana auth complexity:** Embedded panels require anonymous access (security risk) or an auth proxy (implementation complexity). Neither is acceptable for MVP.
- **Low reuse (40%):** Bypasses `cost_dashboard.py` and `cost_collector.py` in favor of raw Loki queries through Grafana. This wastes the investment in those modules.
- **Low testability (6/10):** Grafana panels cannot be tested from the console's test suite. Cost accuracy (SC-3's ±5% requirement) cannot be validated in CI.

**Verdict:** Approach C saves ~0.5 weeks of code but introduces permanent UX debt and operational complexity. Rejected.

---

## Trade-offs Accepted

| Trade-off | Impact | Acceptance Rationale |
|-----------|--------|---------------------|
| Coupled deploys (frontend + backend) | Frontend change requires full redeploy (~30s) | Acceptable for 1-user tool. `vite build` + uvicorn restart is fast. |
| Single process failure mode | Both API and UI go down together | systemd `Restart=always` mitigates. Downtime is seconds, not minutes. No SLA for an internal tool. |
| FastAPI serves static files | Marginally slower than Nginx-direct for static assets | Nginx is the actual edge server on port 443. FastAPI serves on localhost only. Performance difference is unmeasurable. |
| No independent frontend deploy | Can't update UI without backend restart | Migration to Approach B is ~2 hours if this becomes a pain point. |
| API key auth (not SSO) | Less secure than Azure AD SSO | MVP-appropriate. Single user, internal network. SSO is a v2 enhancement. |

---

## Implementation Strategy

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
│   │   │   ├── agent_service.py # Health poll fan-out + registry
│   │   │   ├── cost_service.py  # Loki SDK costs + Azure Cost API
│   │   │   ├── alert_service.py # Merge cost + health alerts
│   │   │   ├── monday_service.py# Story data from Monday.com
│   │   │   ├── loki_client.py   # Loki HTTP query client
│   │   │   └── azure_cost.py    # Azure Cost Management API client
│   │   └── models/
│   │       └── responses.py     # Pydantic response models
│   └── tests/
│       ├── test_routes.py
│       ├── test_services.py
│       └── test_loki_client.py
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/client.ts        # Typed fetch + API key header
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

### Implementation Phases (Week-by-Week)

#### Week 1: Backend Foundation
- FastAPI app scaffold with config, auth, health endpoint
- Agent registry loader (`agent-registry.json` → `AgentRecord` list)
- `AgentService`: health poll fan-out via `httpx.AsyncClient` with 30s cache
- `LokiClient`: HTTP query_range with `[COST_SUMMARY]` parsing
- `CostService`: SDK costs (Loki) + Azure Cost API (optional)
- `AlertService`: merge cost + health alerts from existing modules
- All route handlers: `/api/agents`, `/api/fleet`, `/api/alerts`, `/api/health`
- Restart/pause endpoints: POST `/api/agents/{name}/restart`, `/api/agents/{name}/pause`
- Backend tests (pytest + httpx test client)

#### Week 2: Frontend
- Vite + React + TypeScript + Tailwind scaffold
- API client with typed fetch wrapper and API key injection
- Login page (API key → localStorage)
- Fleet Overview bar with TanStack Query (60s poll)
- Agent Card grid with status color badges
- Agent Detail: cost chart (Recharts), activity timeline, alert history, controls
- Alert Banner (top-of-page anomaly warning)
- Alert Panel (filterable table)

#### Week 3: Integration & Polish
- End-to-end testing (backend ↔ Loki ↔ frontend)
- Error states (agent unreachable, Loki down, stale data indicators)
- Responsive layout (desktop-first, basic tablet)
- Deployment: Nginx config, systemd service, Let's Encrypt SSL
- Smoke test on `ops.gorillacommerce.ai`

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Data fetching | TanStack Query with polling (30-60s) | Simple, sufficient for 1-2 users. WebSocket is overkill. |
| Charts | Recharts | Lightweight, React-native, sufficient for daily bar charts and cost breakdowns. |
| State management | TanStack Query cache (no Redux/Zustand) | Server state only; no complex client state needed. |
| API auth | `X-API-Key` header, validated by FastAPI dependency | Matches existing `health_api.validate_api_key` pattern. |
| Backend async | Full async with `httpx.AsyncClient` | Fan-out health polls benefit from `asyncio.gather`. |
| Caching | In-memory with TTL (30s health, 15min costs, 5min Monday) | Simple, adequate for single-process deployment. |
| CSS | Tailwind utility classes (dark mode) | Consistent with existing dashboard patterns. Fast to build. |

### Module Dependency Map

```
ops_console.main
├── ops_console.auth              → health_api.validate_api_key
├── ops_console.routes.agents     → services.agent_service
│   ├── agent_dashboard.*         → build_health_snapshot, evaluate_health_alerts
│   └── monday_agent.*            → AgentMondayClient.get_story
├── ops_console.routes.fleet      → services.cost_service + agent_service
│   ├── cost_dashboard.*          → aggregate_usage, evaluate_alerts
│   ├── services.loki_client      → NEW: Loki HTTP API wrapper
│   └── services.azure_cost       → NEW: Azure Cost Management API (optional)
├── ops_console.routes.alerts     → services.alert_service
│   ├── cost_dashboard.*          → evaluate_alerts
│   ├── agent_dashboard.*         → evaluate_health_alerts
│   └── services.loki_client      → anomaly/SDK health log queries
└── ops_console.routes.health     → self-health check
```

### New Code Estimate

| Component | Lines | Type |
|-----------|-------|------|
| Backend (routes, services, models, config, auth) | ~800 | Python |
| Loki client + Azure Cost client | ~220 | Python |
| Frontend (components, hooks, types, utils) | ~800 | TypeScript/TSX |
| Backend tests | ~400 | Python |
| Config (Makefile, Dockerfile, Nginx, systemd) | ~100 | Various |
| **Total** | **~2,300** | |

### Deployment Architecture

```
Nginx (ops.gorillacommerce.ai:443)
  └── / → proxy_pass http://127.0.0.1:8000  (FastAPI serves SPA + API)
  └── TLS: Let's Encrypt certbot

systemd: ops-console.service
  └── ExecStart: uvicorn ops_console.main:app --host 127.0.0.1 --port 8000
  └── Restart=always, RestartSec=5
```

### Migration Path (if needed)

If Approach A proves insufficient (unlikely for a 1-user tool), migration to Approach B requires:
1. Remove `StaticFiles` mount from `main.py`
2. Add `CORSMiddleware` to `main.py`
3. Update Nginx to serve frontend from `/var/www/ops-console/dist/`
4. Split systemd into two services

Estimated effort: ~2 hours. No changes to routes, services, models, or frontend components.

---

## Success Criteria Mapping

| SC | How Approach A Satisfies It |
|----|-----------------------------|
| SC-1 | Nginx + Let's Encrypt for HTTPS. API key auth via `X-API-Key` header. |
| SC-2 | `AgentService` polls VMs every 30s. TanStack Query refetches every 30s. ≤60s latency. |
| SC-3 | `CostService` combines Loki (SDK) + Azure Cost API (Foundry). Recharts daily bar chart. |
| SC-4 | Monday.com for story/phase. Loki for actions/commits. `ActivityTimeline` component. |
| SC-5 | POST endpoints trigger VM restart/pause via httpx. Status reflects on next 30s poll. |
| SC-6 | `AlertService` merges `evaluate_alerts()` + `evaluate_health_alerts()` + Loki queries. Filterable table. |
| SC-7 | `AlertBanner` checks `/api/alerts?type=cost_anomaly&active=true`. Loki queries for `[COST_ANOMALY]`. |
| SC-8 | `/api/fleet` aggregates costs, agent count, story count across all agents. `FleetOverview` stat cards. |

---

## Next Phase

Phase 6 (Design) will produce the full specification for Approach A, including:
- Detailed API contracts (request/response schemas)
- Pydantic model definitions
- Frontend component specifications
- Database schema (if any persistent state is needed)
- Security review inputs
- UX review inputs
- Operations review inputs
