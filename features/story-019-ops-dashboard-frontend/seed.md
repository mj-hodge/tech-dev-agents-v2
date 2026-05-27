# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Feature Name | Ops Console Dashboard Frontend |
| Story ID | STORY-019 |

## Problem Statement

The ops console backend API is already live at tech-dev-agents.gorillacommerce.ai, exposing endpoints for fleet overview, agent status, alerts, and agent details. However, there is no frontend UI — non-technical team members (Mark, product managers) cannot see what agents are working on without SSH access or raw API calls.

Specific gaps:
- No visual dashboard showing fleet-wide health, spend, and active stories
- No agent cards with status badges (green/yellow/red/gray) for at-a-glance monitoring
- No agent detail view with cost charts and activity timelines
- No alert visualization or history panel
- No login/auth UI for API key-based access
- No way to restart or pause agents without CLI access
- No Teams deep-link integration for blocker context

## Target User / Use Case

- **Mark (operator)** — opens browser, sees fleet overview (spend, active agents, health score), clicks an agent to see 30-day cost chart and activity timeline
- **Product managers** — see which stories agents are working on, what phase they're in, and whether anything is blocked
- **Mark** — can restart/pause stuck agents from the UI without SSH

## Success Criteria

- [ ] AC-1: LoginPage with API key auth (stored in localStorage, validated against GET /api/health with X-API-Key header)
- [ ] AC-2: FleetOverviewBar showing total spend, active agents, active stories, health score (from GET /api/fleet)
- [ ] AC-3: AgentCards grid with status badge (green=active, yellow=idle, red=error, gray=offline), current story, phase, cost, last activity (from GET /api/agents)
- [ ] AC-4: AgentDetailView with 30-day cost chart (Recharts AreaChart), activity timeline, restart/pause buttons (from GET /api/agents/{name}, POST /api/agents/{name}/restart, POST /api/agents/{name}/pause)
- [ ] AC-5: AlertBanner showing active anomaly count + AlertHistoryPanel filterable by agent/type/date (from GET /api/alerts)
- [ ] AC-6: AgentContextPanel with Teams deep link and blocker badge (red="Blocked:", yellow="Decision needed:") (from GET /api/agents/{name})
- [ ] AC-7: TanStack Query with 30s staleTime for polling with stale-while-revalidate pattern
- [ ] AC-8: Frontend build mounted in FastAPI via StaticFiles with SPA catch-all route
- [ ] AC-9: Responsive layout at 1024px+ breakpoint

## Technical Approach

### Stack
- **React 18** + **TypeScript** + **Vite** for the build toolchain
- **TanStack Query v5** for server state management with 30s stale-while-revalidate polling
- **Recharts** for the 30-day cost AreaChart in AgentDetailView
- **Tailwind CSS** for responsive utility-first styling
- **React Router v6** for SPA routing (login → dashboard → agent detail)

### Architecture
```
frontend/
├── src/
│   ├── main.tsx              # App entry, QueryClientProvider, Router
│   ├── api/                  # API client with X-API-Key header injection
│   │   └── client.ts
│   ├── hooks/                # TanStack Query hooks per endpoint
│   │   ├── useFleet.ts
│   │   ├── useAgents.ts
│   │   ├── useAgent.ts
│   │   └── useAlerts.ts
│   ├── components/
│   │   ├── LoginPage.tsx
│   │   ├── DashboardLayout.tsx
│   │   ├── FleetOverviewBar.tsx
│   │   ├── AgentCard.tsx
│   │   ├── AgentDetailView.tsx
│   │   ├── AgentContextPanel.tsx
│   │   ├── AlertBanner.tsx
│   │   ├── AlertHistoryPanel.tsx
│   │   └── StatusBadge.tsx
│   ├── types/                # TypeScript interfaces matching API responses
│   │   └── api.ts
│   └── index.css             # Tailwind imports
├── index.html
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
└── package.json
```

### API Endpoints (backend already live)
| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | Health check, used for API key validation |
| GET | /api/fleet | Fleet overview: total_spend, active_agents, active_stories, health_score |
| GET | /api/agents | Agent list: name, status, current_story, phase, cost_today, last_activity |
| GET | /api/agents/{name} | Agent detail: cost_history[], activity_timeline[], context |
| POST | /api/agents/{name}/restart | Restart agent |
| POST | /api/agents/{name}/pause | Pause agent |
| GET | /api/alerts | Alerts: active_count, items[] with agent, type, message, timestamp |

### Auth Flow
1. User enters API key on LoginPage
2. App calls GET /api/health with X-API-Key header
3. On 200: store key in localStorage, redirect to dashboard
4. On 401/error: show error message, stay on login
5. All subsequent API calls include X-API-Key from localStorage
6. Logout clears localStorage and redirects to login

### FastAPI Mount
```python
# In existing FastAPI app
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="static")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return FileResponse("frontend/dist/index.html")
```

## Dependencies
- STORY-015 (Deploy Agent Dashboards) — provides the backend API endpoints
- Backend API must be live at tech-dev-agents.gorillacommerce.ai

## Risks
- API response shapes may differ from expected — mitigate with TypeScript interfaces and graceful fallbacks
- Large cost_history arrays could slow rendering — mitigate with Recharts ResponsiveContainer and data windowing

## Notes
- Use feature branch `story-019-ops-dashboard-frontend`, open PR — do NOT push to main
- Frontend build output goes to `frontend/dist/` which FastAPI serves
- All Tailwind classes, no CSS modules or styled-components
