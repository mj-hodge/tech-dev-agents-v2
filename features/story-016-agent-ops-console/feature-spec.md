# Feature Specification: Agent Operations Console

> Phase 6 — Design
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large
> Selected Approach: A (Monolith — FastAPI + Embedded SPA)

---

## Table of Contents

1. [Overview](#overview)
2. [File and Folder Structure](#file-and-folder-structure)
3. [Backend API Design](#backend-api-design)
4. [Pydantic Response Models](#pydantic-response-models)
5. [Service Layer Design](#service-layer-design)
6. [External Client Design](#external-client-design)
7. [Auth Flow](#auth-flow)
8. [Frontend Component Hierarchy](#frontend-component-hierarchy)
9. [Data Flow](#data-flow)
10. [Caching Strategy](#caching-strategy)
11. [Configuration](#configuration)
12. [Integration Points with Existing Modules](#integration-points-with-existing-modules)
13. [Error Handling](#error-handling)
14. [Implementation Plan](#implementation-plan)

---

## 1. Overview

The Agent Operations Console is a single-process web application providing a unified view of a fleet of autonomous dev agents. It consists of a FastAPI backend serving both REST API endpoints and a production-built React SPA via `StaticFiles`. The backend wraps six existing Python modules (`cost_dashboard`, `agent_dashboard`, `monday_agent`, `health_api`, `cost_collector`, `monday_hooks`) and adds two new external clients (Loki HTTP API, Azure Cost Management API).

**Key constraint:** This is a single-user internal tool. Design for simplicity and reliability, not scale.

---

## 2. File and Folder Structure

```
ops-console/
├── backend/
│   ├── ops_console/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app, lifespan, StaticFiles mount
│   │   ├── config.py                # Pydantic Settings from env vars
│   │   ├── auth.py                  # API key dependency (Depends)
│   │   ├── dependencies.py          # Shared FastAPI dependencies (registry, services)
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── agents.py            # /api/agents, /api/agents/{name}, restart, pause
│   │   │   ├── fleet.py             # /api/fleet
│   │   │   ├── alerts.py            # /api/alerts
│   │   │   └── health.py            # /api/health (console self-check)
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── agent_service.py     # Health poll fan-out + registry management
│   │   │   ├── cost_service.py      # SDK costs (Loki) + Azure Foundry costs
│   │   │   ├── alert_service.py     # Merge cost + health + Loki anomaly alerts
│   │   │   ├── monday_service.py    # Story/phase data from Monday.com
│   │   │   ├── loki_client.py       # Loki HTTP API query client
│   │   │   └── azure_cost_client.py # Azure Cost Management REST client
│   │   └── models/
│   │       ├── __init__.py
│   │       └── responses.py         # Pydantic response schemas
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py              # Shared fixtures (mock registry, mock httpx)
│   │   ├── test_routes_agents.py
│   │   ├── test_routes_fleet.py
│   │   ├── test_routes_alerts.py
│   │   ├── test_routes_health.py
│   │   ├── test_agent_service.py
│   │   ├── test_cost_service.py
│   │   ├── test_alert_service.py
│   │   ├── test_monday_service.py
│   │   ├── test_loki_client.py
│   │   └── test_azure_cost_client.py
│   ├── pyproject.toml               # Backend Python deps (fastapi, httpx, uvicorn, pydantic)
│   └── requirements.txt             # Pinned deps for deployment
├── frontend/
│   ├── public/
│   │   └── favicon.ico
│   ├── src/
│   │   ├── main.tsx                 # React root + QueryClientProvider
│   │   ├── App.tsx                  # Router (login vs. dashboard)
│   │   ├── api/
│   │   │   └── client.ts            # Typed fetch wrapper with X-API-Key header
│   │   ├── components/
│   │   │   ├── Layout.tsx           # App shell: header + alert banner + content
│   │   │   ├── LoginPage.tsx        # API key entry form
│   │   │   ├── FleetOverview.tsx    # Top stat bar (spend, agents, stories, health)
│   │   │   ├── AgentGrid.tsx        # Grid of AgentCard components
│   │   │   ├── AgentCard.tsx        # Per-agent summary card (status, story, cost)
│   │   │   ├── AgentDetail/
│   │   │   │   ├── AgentDetailPage.tsx  # Full agent view (tabs or sections)
│   │   │   │   ├── CostChart.tsx        # Recharts daily cost breakdown (SDK vs Azure)
│   │   │   │   ├── ActivityTimeline.tsx # Phase transitions, commits, actions
│   │   │   │   ├── AgentAlertHistory.tsx# Agent-specific alert table
│   │   │   │   └── Controls.tsx         # Restart, pause, enable/disable buttons
│   │   │   ├── AlertBanner.tsx      # Top-of-page prominent anomaly warning
│   │   │   ├── AlertPanel.tsx       # Fleet-wide filterable alert table
│   │   │   └── ui/
│   │   │       ├── StatCard.tsx     # Reusable stat display card
│   │   │       ├── StatusBadge.tsx  # Color-coded status pill
│   │   │       ├── Spinner.tsx      # Loading indicator
│   │   │       └── ErrorMessage.tsx # Error state display
│   │   ├── hooks/
│   │   │   ├── useAgents.ts         # TanStack Query: GET /api/agents (30s poll)
│   │   │   ├── useAgent.ts          # TanStack Query: GET /api/agents/{name} (30s poll)
│   │   │   ├── useAgentCost.ts      # TanStack Query: GET /api/agents/{name}/cost
│   │   │   ├── useFleet.ts          # TanStack Query: GET /api/fleet (60s poll)
│   │   │   ├── useAlerts.ts         # TanStack Query: GET /api/alerts (30s poll)
│   │   │   ├── useRestartAgent.ts   # TanStack Mutation: POST restart
│   │   │   └── usePauseAgent.ts     # TanStack Mutation: POST pause
│   │   ├── types/
│   │   │   └── api.ts               # TypeScript interfaces mirroring Pydantic models
│   │   └── utils/
│   │       ├── format.ts            # Currency, date, duration formatters
│   │       └── constants.ts         # Status colors, poll intervals
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   └── package.json
├── deployment/
│   ├── nginx/
│   │   └── ops-console.conf        # Nginx reverse proxy config
│   ├── systemd/
│   │   └── ops-console.service     # systemd unit file
│   └── scripts/
│       └── deploy.sh               # Build frontend + restart service
├── Makefile                         # dev, build, test, deploy targets
├── Dockerfile                       # Multi-stage: node build -> python runtime
└── README.md                        # Setup and development instructions
```

---

## 3. Backend API Design

### 3.1 Base URL and Versioning

- **Base URL:** `https://ops.gorillacommerce.ai/api`
- **No URL versioning for MVP.** The sole consumer is the co-located SPA. Version via backward-compatible additions.

### 3.2 Authentication

All `/api/*` endpoints (except `/api/health`) require the `X-API-Key` header.

```
X-API-Key: <value>
```

Returns `401 Unauthorized` with `{"detail": "Missing API key"}` or `{"detail": "Invalid API key"}`.

### 3.3 Endpoints

#### GET /api/health

Console self-health check. No auth required.

**Response 200:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "uptime_seconds": 3600,
  "agents_reachable": 2,
  "agents_total": 2,
  "loki_reachable": true,
  "checked_at": "2026-04-01T12:00:00Z"
}
```

---

#### GET /api/agents

List all registered agents with current health status, active story, and today's cost.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `status` | string | (all) | Filter by status: `online`, `idle`, `stuck`, `offline` |
| `enabled` | bool | (all) | Filter by enabled flag |

**Response 200:**
```json
{
  "agents": [
    {
      "name": "dan",
      "status": "online",
      "role": "developer",
      "enabled": true,
      "last_activity": "2026-04-01T11:45:00Z",
      "uptime_seconds": 86400,
      "active_sessions": 1,
      "error_count": 0,
      "checked_at": "2026-04-01T12:00:00Z",
      "current_story": "STORY-016: Agent Operations Console",
      "current_phase": "Phase 8: Implementation",
      "today_cost_usd": 12.47
    }
  ],
  "total": 2,
  "fetched_at": "2026-04-01T12:00:00Z"
}
```

---

#### GET /api/agents/{name}

Detailed view of a single agent: health, cost summary, current story, recent activity.

**Path Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `name` | string | Agent name (e.g., `dan`, `derrick`) |

**Response 200:**
```json
{
  "name": "dan",
  "status": "online",
  "role": "developer",
  "enabled": true,
  "host": "10.0.1.10",
  "port": 8080,
  "last_activity": "2026-04-01T11:45:00Z",
  "uptime_seconds": 86400,
  "active_sessions": 1,
  "error_count": 0,
  "checked_at": "2026-04-01T12:00:00Z",
  "current_story": {
    "item_id": 12345,
    "name": "STORY-016: Agent Operations Console",
    "phase": "Phase 8: Implementation",
    "status": "In Progress",
    "group": "In Progress"
  },
  "cost_today": {
    "sdk_cost_usd": 10.25,
    "azure_cost_usd": 2.22,
    "total_cost_usd": 12.47,
    "sdk_sessions": 8,
    "sdk_turns": 142
  },
  "recent_activity": [
    {
      "type": "phase_transition",
      "description": "Phase 7 → Phase 8",
      "timestamp": "2026-04-01T10:30:00Z"
    },
    {
      "type": "commit",
      "description": "feat(ops-console): add agent health endpoint",
      "timestamp": "2026-04-01T11:00:00Z"
    }
  ]
}
```

**Response 404:**
```json
{"detail": "Agent 'unknown' not found in registry"}
```

---

#### GET /api/agents/{name}/cost

Cost breakdown for an agent over a time range.

**Path Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `name` | string | Agent name |

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `days` | int | 7 | Number of days of history (max 90) |
| `granularity` | string | `daily` | `daily` or `weekly` |

**Response 200:**
```json
{
  "agent_name": "dan",
  "period_start": "2026-03-25",
  "period_end": "2026-04-01",
  "granularity": "daily",
  "total_sdk_cost_usd": 85.30,
  "total_azure_cost_usd": 18.50,
  "total_cost_usd": 103.80,
  "daily": [
    {
      "date": "2026-03-25",
      "sdk_cost_usd": 12.00,
      "azure_cost_usd": 2.50,
      "total_cost_usd": 14.50,
      "sdk_sessions": 10,
      "sdk_turns": 180
    }
  ],
  "data_freshness": {
    "sdk_as_of": "2026-04-01T12:00:00Z",
    "azure_as_of": "2026-03-30T23:59:59Z",
    "azure_is_estimated": true
  }
}
```

---

#### GET /api/agents/{name}/activity

Activity feed for an agent.

**Path Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `name` | string | Agent name |

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max events to return (max 200) |
| `type` | string | (all) | Filter: `phase_transition`, `commit`, `test_run`, `alert`, `restart` |

**Response 200:**
```json
{
  "agent_name": "dan",
  "events": [
    {
      "id": "evt_abc123",
      "type": "phase_transition",
      "description": "Phase 7 (Test Design) completed",
      "detail": "14 tests designed, all ACs mapped",
      "timestamp": "2026-04-01T10:30:00Z",
      "source": "monday.com"
    }
  ],
  "total": 42,
  "has_more": false
}
```

---

#### POST /api/agents/{name}/restart

Trigger an agent restart.

**Path Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `name` | string | Agent name |

**Request Body:**
```json
{
  "reason": "Agent stuck on Phase 8 for 3 hours",
  "force": false
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `reason` | string | yes | — | Human-readable reason for restart |
| `force` | bool | no | false | Force restart even if agent is mid-session |

**Response 200:**
```json
{
  "agent_name": "dan",
  "success": true,
  "message": "Restart initiated successfully",
  "previous_status": "stuck",
  "new_status": "online",
  "completed_at": "2026-04-01T12:01:00Z",
  "requested_by": "ops-console"
}
```

**Response 400:**
```json
{"detail": "Agent 'dan' is disabled. Enable before restarting."}
```

**Response 502:**
```json
{"detail": "Failed to reach agent 'dan' at 10.0.1.10:8080"}
```

---

#### POST /api/agents/{name}/pause

Pause or resume an agent.

**Path Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `name` | string | Agent name |

**Request Body:**
```json
{
  "action": "pause",
  "reason": "Reducing spend overnight"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action` | string | yes | `pause` or `resume` |
| `reason` | string | no | Optional reason for audit trail |

**Response 200:**
```json
{
  "agent_name": "dan",
  "action": "pause",
  "success": true,
  "message": "Agent paused successfully",
  "timestamp": "2026-04-01T12:00:00Z"
}
```

---

#### GET /api/fleet

Fleet-wide overview aggregates.

**Response 200:**
```json
{
  "total_daily_spend_usd": 24.50,
  "active_agents": 2,
  "total_agents": 2,
  "online_agents": 1,
  "idle_agents": 1,
  "stuck_agents": 0,
  "offline_agents": 0,
  "stories_in_progress": 3,
  "fleet_health_score": 0.95,
  "active_alerts": 0,
  "fetched_at": "2026-04-01T12:00:00Z",
  "agents": [
    {
      "name": "dan",
      "status": "online",
      "current_story": "STORY-016",
      "today_cost_usd": 12.47
    },
    {
      "name": "derrick",
      "status": "idle",
      "current_story": null,
      "today_cost_usd": 12.03
    }
  ]
}
```

**Fleet health score calculation:**
```
score = online_count / enabled_count
       - (stuck_count * 0.3 / enabled_count)
       - (offline_count * 0.5 / enabled_count)
       clamped to [0.0, 1.0]
```

---

#### GET /api/alerts

Alert history across all agents.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `agent` | string | (all) | Filter by agent name |
| `type` | string | (all) | Filter: `cost_threshold`, `cost_anomaly`, `sdk_health`, `terminal_guard`, `agent_offline`, `agent_stuck`, `high_errors` |
| `active` | bool | (all) | Filter by active/resolved status |
| `since` | string | 24h ago | ISO datetime or relative (`24h`, `7d`) |
| `limit` | int | 100 | Max alerts to return (max 500) |

**Response 200:**
```json
{
  "alerts": [
    {
      "id": "alert_xyz789",
      "agent_name": "dan",
      "type": "cost_anomaly",
      "severity": "high",
      "message": "Agent 'dan' detected coding without Claude Code SDK. Azure Foundry spend $45.00 in 2 hours.",
      "active": true,
      "triggered_at": "2026-04-01T10:15:00Z",
      "resolved_at": null,
      "source": "loki"
    }
  ],
  "total": 3,
  "active_count": 1,
  "fetched_at": "2026-04-01T12:00:00Z"
}
```

---

## 4. Pydantic Response Models

All response models in `ops_console/models/responses.py`:

```python
from pydantic import BaseModel, Field
from enum import Enum


class AgentStatusEnum(str, Enum):
    ONLINE = "online"
    IDLE = "idle"
    STUCK = "stuck"
    OFFLINE = "offline"


# --- Health Check ---

class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: int
    agents_reachable: int
    agents_total: int
    loki_reachable: bool
    checked_at: str


# --- Agent List ---

class AgentSummary(BaseModel):
    name: str
    status: AgentStatusEnum
    role: str
    enabled: bool
    last_activity: str | None
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str
    current_story: str | None
    current_phase: str | None
    today_cost_usd: float


class AgentListResponse(BaseModel):
    agents: list[AgentSummary]
    total: int
    fetched_at: str


# --- Agent Detail ---

class StoryInfo(BaseModel):
    item_id: int
    name: str
    phase: str | None
    status: str | None
    group: str | None


class CostToday(BaseModel):
    sdk_cost_usd: float
    azure_cost_usd: float
    total_cost_usd: float
    sdk_sessions: int
    sdk_turns: int


class ActivityEvent(BaseModel):
    id: str
    type: str
    description: str
    detail: str | None = None
    timestamp: str
    source: str


class AgentDetailResponse(BaseModel):
    name: str
    status: AgentStatusEnum
    role: str
    enabled: bool
    host: str
    port: int
    last_activity: str | None
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str
    current_story: StoryInfo | None
    cost_today: CostToday
    recent_activity: list[ActivityEvent]


# --- Cost Breakdown ---

class DailyCost(BaseModel):
    date: str
    sdk_cost_usd: float
    azure_cost_usd: float
    total_cost_usd: float
    sdk_sessions: int
    sdk_turns: int


class DataFreshness(BaseModel):
    sdk_as_of: str
    azure_as_of: str | None
    azure_is_estimated: bool


class CostBreakdownResponse(BaseModel):
    agent_name: str
    period_start: str
    period_end: str
    granularity: str
    total_sdk_cost_usd: float
    total_azure_cost_usd: float
    total_cost_usd: float
    daily: list[DailyCost]
    data_freshness: DataFreshness


# --- Activity Feed ---

class ActivityFeedResponse(BaseModel):
    agent_name: str
    events: list[ActivityEvent]
    total: int
    has_more: bool


# --- Restart/Pause ---

class RestartRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)
    force: bool = False


class RestartResponse(BaseModel):
    agent_name: str
    success: bool
    message: str
    previous_status: str
    new_status: str | None
    completed_at: str
    requested_by: str


class PauseRequest(BaseModel):
    action: str = Field(..., pattern="^(pause|resume)$")
    reason: str | None = None


class PauseResponse(BaseModel):
    agent_name: str
    action: str
    success: bool
    message: str
    timestamp: str


# --- Fleet ---

class FleetAgentSummary(BaseModel):
    name: str
    status: AgentStatusEnum
    current_story: str | None
    today_cost_usd: float


class FleetOverviewResponse(BaseModel):
    total_daily_spend_usd: float
    active_agents: int
    total_agents: int
    online_agents: int
    idle_agents: int
    stuck_agents: int
    offline_agents: int
    stories_in_progress: int
    fleet_health_score: float = Field(..., ge=0.0, le=1.0)
    active_alerts: int
    fetched_at: str
    agents: list[FleetAgentSummary]


# --- Alerts ---

class AlertItem(BaseModel):
    id: str
    agent_name: str
    type: str
    severity: str
    message: str
    active: bool
    triggered_at: str
    resolved_at: str | None
    source: str


class AlertListResponse(BaseModel):
    alerts: list[AlertItem]
    total: int
    active_count: int
    fetched_at: str
```

---

## 5. Service Layer Design

Services are thin orchestration classes. They compose existing module functions with HTTP clients and caching. Each service is instantiated once at application startup and injected via FastAPI `Depends()`.

### 5.1 AgentService

**File:** `ops_console/services/agent_service.py`

**Responsibility:** Agent registry management, health poll fan-out, restart/pause forwarding.

```python
class AgentService:
    def __init__(self, registry_path: str, http_client: httpx.AsyncClient):
        """
        Args:
            registry_path: Path to agent-registry.json
            http_client: Shared async HTTP client for agent VM calls
        """

    def get_registry(self) -> list[AgentRecord]:
        """Return all agents from registry. Reloads if file changed (mtime check)."""

    def get_agent(self, name: str) -> AgentRecord:
        """Return a single agent by name. Raises AgentNotFoundError if not found."""

    async def get_all_health(self) -> list[AgentHealthSnapshot]:
        """
        Fan-out GET /health to all enabled agent VMs.
        Uses asyncio.gather with 5s timeout per VM.
        Returns AgentHealthSnapshot per agent (OFFLINE for unreachable).
        Caches for 30s.
        Reuses: agent_dashboard.build_health_snapshot
        """

    async def get_agent_health(self, name: str) -> AgentHealthSnapshot:
        """Get health for a single agent. Returns from cache if fresh."""

    async def restart_agent(self, name: str, reason: str, force: bool) -> RestartResult:
        """
        POST restart to agent VM's health API.
        Reuses: agent_dashboard.validate_restart, agent_dashboard.build_restart_command
        """

    async def pause_agent(self, name: str, action: str, reason: str | None) -> dict:
        """POST pause/resume to agent VM."""
```

**Integration with existing modules:**
- `agent_dashboard.AgentRecord` — registry entries
- `agent_dashboard.build_health_snapshot()` — create snapshots from VM response data
- `agent_dashboard.validate_restart()` — validate restart preconditions
- `agent_dashboard.build_restart_command()` / `build_restart_result()` — structured restart data
- `agent_dashboard.collect_dashboard_health()` — could be used, but we implement async fan-out directly for `httpx.AsyncClient` compatibility

### 5.2 CostService

**File:** `ops_console/services/cost_service.py`

**Responsibility:** Combine SDK costs (from Loki) and Azure Foundry costs (from Azure Cost API) into unified cost views.

```python
class CostService:
    def __init__(self, loki: LokiClient, azure: AzureCostClient | None):
        """
        Args:
            loki: Loki HTTP API client for SDK cost queries
            azure: Azure Cost Management client (None if not configured)
        """

    async def get_today_cost(self, agent_name: str) -> CostToday:
        """
        Get today's cost for a single agent.
        SDK cost: Loki query for [COST_SUMMARY] today, or sum of [DONE] lines.
        Azure cost: Azure Cost API daily query (may be stale/estimated).
        Caches for 5min.
        Reuses: cost_collector.parse_done_lines (regex pattern)
        """

    async def get_cost_breakdown(self, agent_name: str, days: int, granularity: str) -> CostBreakdownResponse:
        """
        Get historical cost breakdown.
        SDK: Loki query for [COST_SUMMARY] lines over date range.
        Azure: Azure Cost API with daily granularity.
        Caches for 15min.
        Reuses: cost_dashboard.aggregate_usage (for event aggregation)
        """

    async def get_fleet_daily_spend(self) -> float:
        """Sum today's cost across all agents. Caches for 5min."""
```

**Integration with existing modules:**
- `cost_dashboard.CostEvent` — cost event data model
- `cost_dashboard.CostSource` — SDK vs Azure Foundry enum
- `cost_dashboard.aggregate_usage()` — aggregate cost events by agent/period
- `cost_collector.parse_done_lines()` — regex extraction from log lines (reused for Loki results)

### 5.3 AlertService

**File:** `ops_console/services/alert_service.py`

**Responsibility:** Merge alerts from three sources (cost thresholds, health alerts, Loki anomaly logs) into a unified alert feed.

```python
class AlertService:
    def __init__(self, loki: LokiClient, agent_service: AgentService, cost_service: CostService):
        """
        Args:
            loki: For querying [COST_ANOMALY], [SDK_HEALTH], [TERMINAL_GUARD] logs
            agent_service: For health snapshots (feeds evaluate_health_alerts)
            cost_service: For cost events (feeds evaluate_alerts)
        """

    async def get_alerts(
        self,
        agent: str | None = None,
        alert_type: str | None = None,
        active: bool | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> AlertListResponse:
        """
        Merge alerts from three sources:
        1. cost_dashboard.evaluate_alerts() — cost threshold breaches
        2. agent_dashboard.evaluate_health_alerts() — offline/stuck/high_errors
        3. Loki queries for [COST_ANOMALY], [SDK_HEALTH], [TERMINAL_GUARD] DENIED
        Sort by timestamp descending. Apply filters.
        """

    async def get_active_anomalies(self) -> list[AlertItem]:
        """
        Get active cost anomaly alerts for the AlertBanner component.
        Loki query: {job="agent-logs"} |= "[COST_ANOMALY]" in last 15 min.
        Caches for 60s.
        """
```

**Integration with existing modules:**
- `cost_dashboard.evaluate_alerts()` — cost threshold evaluation
- `cost_dashboard.AlertThreshold` — threshold configuration
- `agent_dashboard.evaluate_health_alerts()` — health-based alert evaluation
- `agent_dashboard.DashboardAlert` — health alert data model

### 5.4 MondayService

**File:** `ops_console/services/monday_service.py`

**Responsibility:** Fetch story/phase data for agents from Monday.com.

```python
class MondayService:
    def __init__(self, clients: dict[str, AgentMondayClient]):
        """
        Args:
            clients: Map of agent_name -> configured AgentMondayClient
        """

    async def get_current_story(self, agent_name: str) -> StoryInfo | None:
        """
        Get the current story for an agent from Monday.com.
        Caches for 5min.
        Reuses: monday_agent.AgentMondayClient.get_story()
        """

    async def get_stories_in_progress(self) -> int:
        """Count of stories in "In Progress" group across all agents. Caches for 5min."""
```

**Integration with existing modules:**
- `monday_agent.AgentMondayClient` — Monday.com API client
- `monday_agent.AgentIdentity` — per-agent credentials
- `monday_agent.PHASE_NAMES` — phase display names

### 5.5 Calling Convention for Sync Module Functions

Several existing module functions (`aggregate_usage`, `evaluate_alerts`, `evaluate_health_alerts`, `validate_restart`) are synchronous. In the async FastAPI context:

1. **CPU-bound or fast functions** (under 10ms): Call directly in async handlers. FastAPI handles this correctly for simple sync calls within async functions.
2. **I/O-bound sync functions** (Monday.com `get_story` uses `requests`): Wrap in `asyncio.to_thread()` to avoid blocking the event loop.

```python
# Example: wrapping sync Monday.com call
story_data = await asyncio.to_thread(client.get_story, item_id)
```

---

## 6. External Client Design

### 6.1 LokiClient

**File:** `ops_console/services/loki_client.py`

```python
class LokiClient:
    """HTTP client for Grafana Loki query_range API."""

    def __init__(self, base_url: str, api_key: str, http_client: httpx.AsyncClient):
        """
        Args:
            base_url: Loki API base URL (e.g., https://grafana.gorillacommerce.ai)
            api_key: Grafana Cloud API key (bearer token)
            http_client: Shared async HTTP client
        """

    async def query_range(
        self, query: str, start: str, end: str, limit: int = 1000
    ) -> list[LokiLogEntry]:
        """
        Execute a LogQL query_range request.
        Returns parsed log entries sorted by timestamp.
        Raises LokiError on non-2xx response.
        """

    async def query_cost_summaries(
        self, agent_name: str, start_date: str, end_date: str
    ) -> list[dict]:
        """
        Query [COST_SUMMARY] lines for an agent over a date range.
        LogQL: {job="agent-logs", agent="<name>"} |= "[COST_SUMMARY]"
        Parses structured fields from log lines.
        """

    async def query_done_lines(
        self, agent_name: str, start: str, end: str
    ) -> list[dict]:
        """
        Query [DONE] lines for real-time cost data.
        LogQL: {job="agent-logs", agent="<name>"} |= "[DONE]"
        Uses cost_collector.parse_done_lines() regex for parsing.
        """

    async def query_anomalies(
        self, agent_name: str | None, start: str, end: str
    ) -> list[dict]:
        """
        Query [COST_ANOMALY] lines.
        LogQL: {job="agent-logs"} |= "[COST_ANOMALY]"
        """

    async def query_sdk_health(
        self, agent_name: str | None, start: str, end: str
    ) -> list[dict]:
        """Query [SDK_HEALTH] lines."""

    async def query_terminal_guard(
        self, agent_name: str | None, start: str, end: str
    ) -> list[dict]:
        """Query [TERMINAL_GUARD] |= "DENIED" lines."""

    async def is_reachable(self) -> bool:
        """Health check: GET /ready on Loki. Returns True/False."""
```

**LogQL patterns used:**

| Query | LogQL |
|-------|-------|
| Cost summaries | `{job="agent-logs", agent="<name>"} \|= "[COST_SUMMARY]"` |
| Session costs | `{job="agent-logs", agent="<name>"} \|= "[DONE]"` |
| Cost anomalies | `{job="agent-logs"} \|= "[COST_ANOMALY]"` |
| SDK health | `{job="agent-logs"} \|= "[SDK_HEALTH]"` |
| Terminal guard | `{job="agent-logs"} \|= "[TERMINAL_GUARD]" \|= "DENIED"` |

### 6.2 AzureCostClient

**File:** `ops_console/services/azure_cost_client.py`

```python
class AzureCostClient:
    """Client for Azure Cost Management REST API."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        subscription_id: str,
        http_client: httpx.AsyncClient,
    ):
        """Service principal credentials for Azure Cost Management Reader role."""

    async def _get_token(self) -> str:
        """
        Acquire OAuth2 access token via client credentials grant.
        Caches until 5 min before expiry.
        """

    async def get_daily_costs(
        self, start_date: str, end_date: str
    ) -> dict[str, list[DailyCost]]:
        """
        Query daily costs grouped by resource group.
        Maps resource group names to agent names via config.
        Returns: {agent_name: [DailyCost, ...]}
        """

    async def get_agent_daily_costs(
        self, agent_name: str, start_date: str, end_date: str
    ) -> list[DailyCost]:
        """Get daily costs for a single agent (filters from get_daily_costs)."""
```

**Azure resource group to agent mapping** is defined in config:

```python
# config.py
AGENT_AZURE_MAP: dict[str, str] = {
    "rg-agent-dan": "dan",
    "rg-agent-derrick": "derrick",
}
```

---

## 7. Auth Flow

### 7.1 API Key Flow

```
┌─────────────────────────────────────────────────────┐
│ User opens ops.gorillacommerce.ai                    │
│                                                       │
│ 1. SPA loads (served from StaticFiles, no auth)       │
│ 2. SPA checks localStorage for "ops_api_key"          │
│    ├── Found → set X-API-Key header on all requests  │
│    └── Not found → render LoginPage                  │
│                                                       │
│ 3. LoginPage: user enters API key, clicks "Login"    │
│ 4. SPA makes GET /api/health with X-API-Key header   │
│    ├── 200 → store key in localStorage, navigate to  │
│    │          dashboard                               │
│    └── 401 → show "Invalid API key" error            │
│                                                       │
│ 5. All subsequent API calls include X-API-Key header  │
│ 6. On 401 response → clear localStorage, show login  │
└─────────────────────────────────────────────────────┘
```

### 7.2 Backend Auth Dependency

```python
# auth.py
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from tech_dev_agents.health_api import validate_api_key, AuthError
from ops_console.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(
    api_key: str | None = Security(api_key_header),
) -> str:
    """FastAPI dependency that validates the API key."""
    settings = get_settings()
    try:
        validate_api_key(provided=api_key, expected=settings.ops_console_api_key)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return api_key
```

### 7.3 Frontend API Client

```typescript
// api/client.ts
const API_KEY_STORAGE = "ops_api_key";

function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const key = getApiKey();
  if (!key) throw new AuthError("No API key");

  const resp = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "X-API-Key": key,
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (resp.status === 401) {
    localStorage.removeItem(API_KEY_STORAGE);
    window.location.reload();
    throw new AuthError("Invalid API key");
  }

  if (!resp.ok) {
    throw new ApiError(resp.status, await resp.text());
  }

  return resp.json();
}
```

---

## 8. Frontend Component Hierarchy

```
<App>
├── <LoginPage />                              # shown when no API key
└── <Layout>                                   # shown when authenticated
    ├── <AlertBanner />                        # top-of-page anomaly warning
    ├── <FleetOverview />                      # stat cards bar
    │   ├── <StatCard label="Daily Spend" />
    │   ├── <StatCard label="Active Agents" />
    │   ├── <StatCard label="Stories" />
    │   └── <StatCard label="Fleet Health" />
    ├── <Routes>
    │   ├── "/" → <AgentGrid />                # default: agent card grid
    │   │   └── <AgentCard /> × N
    │   │       └── <StatusBadge />
    │   ├── "/agents/:name" → <AgentDetailPage />
    │   │   ├── <CostChart />                  # Recharts bar chart
    │   │   ├── <ActivityTimeline />           # event list
    │   │   ├── <AgentAlertHistory />          # alert table
    │   │   └── <Controls />                   # restart/pause buttons
    │   └── "/alerts" → <AlertPanel />         # fleet-wide alert table
    └── <footer />                             # version, last fetched
```

### Component Specifications

#### AlertBanner
- **Data:** `useAlerts({ type: "cost_anomaly", active: true })`
- **Behavior:** Show red banner at top when active cost anomalies exist. Auto-dismiss when resolved. Pulsing animation for attention.
- **Poll:** 30s via TanStack Query `refetchInterval`

#### FleetOverview
- **Data:** `useFleet()` (60s poll)
- **Behavior:** 4 stat cards in a row. Color-code health score (green >0.8, yellow >0.5, red <=0.5).

#### AgentCard
- **Data:** From `useAgents()` list
- **Behavior:** Clickable → navigates to `/agents/:name`. Status badge color-coded. Shows name, status, current story name, current phase, today's cost.

#### CostChart
- **Data:** `useAgentCost(name, { days: 30 })`
- **Behavior:** Stacked bar chart (SDK in blue, Azure in orange). Hover tooltip with daily breakdown. Date range selector (7d, 14d, 30d).
- **Library:** Recharts `<BarChart>` with `<Bar>` stacks

#### ActivityTimeline
- **Data:** `useAgent(name)` → `recent_activity` array
- **Behavior:** Vertical timeline with event type icons. Filter by type. Most recent at top.

#### Controls
- **Data:** Agent detail status
- **Behavior:** Restart button (red, with confirmation modal). Pause/Resume toggle. All trigger mutations via `useRestartAgent` / `usePauseAgent`. Disable buttons during pending mutations. Show success/error toast.

### Routing

```typescript
// App.tsx
<BrowserRouter>
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<RequireAuth><Layout /></RequireAuth>}>
      <Route index element={<AgentGrid />} />
      <Route path="/agents/:name" element={<AgentDetailPage />} />
      <Route path="/alerts" element={<AlertPanel />} />
    </Route>
  </Routes>
</BrowserRouter>
```

### Poll Intervals

| Hook | Endpoint | Interval | Rationale |
|------|----------|----------|-----------|
| `useAgents` | GET /api/agents | 30s | SC-2 requires ≤60s status update |
| `useAgent` | GET /api/agents/{name} | 30s | Same |
| `useAgentCost` | GET /api/agents/{name}/cost | 5min | Cost data changes slowly |
| `useFleet` | GET /api/fleet | 60s | Aggregates need less frequent refresh |
| `useAlerts` | GET /api/alerts | 30s | SC-6 requires ≤1min alert visibility |

---

## 9. Data Flow

### 9.1 Page Load: Dashboard (Agent Grid)

```
Browser                    FastAPI                     External
  │                          │                            │
  ├─ GET /api/fleet ─────────►                            │
  │                          ├─ agent_service.get_all_health()
  │                          │   ├─ Load registry ────────┤
  │                          │   ├─ GET /health (VM1) ────► Agent VM 1
  │                          │   ├─ GET /health (VM2) ────► Agent VM 2
  │                          │   └─ asyncio.gather ◄──────┤
  │                          ├─ cost_service.get_fleet_daily_spend()
  │                          │   └─ loki.query_cost_summaries() ──► Loki
  │                          ├─ monday_service.get_stories_in_progress()
  │                          │   └─ client.get_story() ───► Monday.com
  │                          ├─ alert_service.get_active_count()
  │                          │   └─ loki.query_anomalies() ► Loki
  │  ◄── FleetOverviewResponse
  │                          │
  ├─ GET /api/agents ────────►
  │                          ├─ (cached health from above)
  │                          ├─ monday_service per agent
  │                          ├─ cost_service.get_today_cost per agent
  │  ◄── AgentListResponse   │
  │                          │
  ├─ GET /api/alerts?active=true
  │                          ├─ alert_service.get_active_anomalies()
  │  ◄── AlertListResponse   │
```

### 9.2 Agent Detail View

```
Browser                    FastAPI                     External
  │                          │                            │
  ├─ GET /api/agents/dan ───►│                            │
  │                          ├─ agent_service.get_agent_health("dan")
  │                          │   └─ GET /health (dan VM) ─► Agent VM
  │                          ├─ monday_service.get_current_story("dan")
  │                          │   └─ client.get_story() ───► Monday.com
  │                          ├─ cost_service.get_today_cost("dan")
  │                          │   └─ loki.query_cost_summaries() ► Loki
  │                          ├─ Build recent_activity from Monday phases
  │  ◄── AgentDetailResponse │
  │                          │
  ├─ GET /api/agents/dan/cost?days=30
  │                          ├─ cost_service.get_cost_breakdown("dan", 30, "daily")
  │                          │   ├─ loki.query_cost_summaries() ──► Loki
  │                          │   └─ azure.get_agent_daily_costs() ► Azure
  │  ◄── CostBreakdownResponse
```

### 9.3 Restart Flow

```
Browser                    FastAPI                     External
  │                          │                            │
  ├─ POST /api/agents/dan/restart
  │  { reason: "stuck", force: false }
  │                          │
  │                          ├─ auth.require_api_key()
  │                          ├─ agent_service.get_agent("dan")
  │                          ├─ agent_service.get_agent_health("dan")
  │                          ├─ validate_restart(command, registry, health)
  │                          │   └─ Checks: agent exists, enabled, valid state
  │                          ├─ POST /restart to agent VM ──► Agent VM
  │                          │   └─ Uses AGENT_API_KEY header
  │                          ├─ build_restart_result()
  │  ◄── RestartResponse     │
  │                          │
  │  (TanStack Query auto-refetches /api/agents in ≤30s)
```

---

## 10. Caching Strategy

All caches are in-memory Python dicts with TTL. Single-process deployment means no cache coherency issues.

| Data | Cache TTL | Rationale |
|------|-----------|-----------|
| Agent health snapshots | 30s | Health changes frequently; SC-2 requires ≤60s |
| Agent registry (file) | Until mtime changes | Static file, rarely changes |
| Today's cost per agent | 5min | Cost accumulates slowly within a day |
| Historical cost breakdown | 15min | Historical data doesn't change |
| Azure Cost API results | 15min | Rate limited (30 req/min); 24-48h data delay |
| Monday.com story data | 5min | Story transitions are infrequent |
| Fleet overview | 60s | Aggregate of health + cost |
| Active anomaly alerts | 60s | For AlertBanner; 15min detection window per SC-7 |
| Loki reachability | 60s | For /api/health self-check |

**Cache implementation:**

```python
class TTLCache:
    """Simple in-memory TTL cache."""

    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        if key in self._store:
            value, cached_at = self._store[key]
            if time.time() - cached_at < self._ttl:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.time())
```

---

## 11. Configuration

**File:** `ops_console/config.py`

```python
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """All configuration from environment variables."""

    # App
    app_name: str = "Agent Operations Console"
    app_version: str = "0.1.0"
    debug: bool = False

    # Auth
    ops_console_api_key: str  # Required, no default

    # Agent Registry
    agent_registry_path: str = "deployment/vm/agent-registry.json"

    # Loki
    loki_url: str = "https://grafana.gorillacommerce.ai"
    loki_api_key: str  # Required
    loki_timeout_seconds: int = 30

    # Azure Cost Management (optional — all-or-nothing)
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    azure_subscription_id: str | None = None

    # Azure Resource Group → Agent Name mapping (JSON string)
    azure_agent_map: str = '{"rg-agent-dan": "dan", "rg-agent-derrick": "derrick"}'

    # Monday.com (per-agent, JSON string of {agent_name: {api_token, board_id}})
    monday_config: str = '{}'

    # Agent Communication
    agent_api_key: str  # For authenticating to agent VMs

    # Cache TTLs
    health_cache_ttl: int = 30
    cost_cache_ttl: int = 300
    monday_cache_ttl: int = 300
    fleet_cache_ttl: int = 60

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # CORS (for dev mode)
    cors_origins: str = ""  # Comma-separated origins, empty = no CORS

    @property
    def azure_enabled(self) -> bool:
        return all([
            self.azure_tenant_id,
            self.azure_client_id,
            self.azure_client_secret,
            self.azure_subscription_id,
        ])

    class Config:
        env_prefix = "OPS_"
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Environment variables (production):**

```bash
OPS_OPS_CONSOLE_API_KEY=<secret>
OPS_LOKI_URL=https://grafana.gorillacommerce.ai
OPS_LOKI_API_KEY=<grafana-cloud-key>
OPS_AZURE_TENANT_ID=<tenant>
OPS_AZURE_CLIENT_ID=<app-id>
OPS_AZURE_CLIENT_SECRET=<secret>
OPS_AZURE_SUBSCRIPTION_ID=<sub-id>
OPS_AGENT_API_KEY=<shared-agent-key>
OPS_AGENT_REGISTRY_PATH=/opt/ops-console/agent-registry.json
OPS_MONDAY_CONFIG='{"dan": {"api_token": "...", "board_id": 18405631030}, "derrick": {"api_token": "...", "board_id": 18405631030}}'
```

---

## 12. Integration Points with Existing Modules

### 12.1 Integration Map

```
ops_console                        tech_dev_agents (existing)
─────────────                      ────────────────────────────
auth.py ──────────────────────────► health_api.validate_api_key()
                                    health_api.AuthError

services/agent_service.py ────────► agent_dashboard.AgentRecord
                                    agent_dashboard.AgentHealthSnapshot
                                    agent_dashboard.build_health_snapshot()
                                    agent_dashboard.build_restart_command()
                                    agent_dashboard.build_restart_result()
                                    agent_dashboard.validate_restart()
                                    agent_dashboard.AgentNotFoundError

services/cost_service.py ─────────► cost_dashboard.CostEvent
                                    cost_dashboard.CostSource
                                    cost_dashboard.aggregate_usage()
                                    cost_dashboard.build_cost_event()
                                    cost_collector.parse_done_lines()

services/alert_service.py ────────► cost_dashboard.evaluate_alerts()
                                    cost_dashboard.AlertThreshold
                                    cost_dashboard.CostAlert
                                    agent_dashboard.evaluate_health_alerts()
                                    agent_dashboard.DashboardAlert

services/monday_service.py ───────► monday_agent.AgentMondayClient
                                    monday_agent.AgentIdentity
                                    monday_agent.PHASE_NAMES
```

### 12.2 Import Pattern

All existing modules live in `tech_dev_agents/` package. The ops console imports them as:

```python
from tech_dev_agents.cost_dashboard import (
    CostEvent, CostSource, aggregate_usage, evaluate_alerts, AlertThreshold
)
from tech_dev_agents.agent_dashboard import (
    AgentRecord, AgentHealthSnapshot, build_health_snapshot,
    validate_restart, build_restart_command, build_restart_result,
    evaluate_health_alerts, AgentNotFoundError
)
from tech_dev_agents.health_api import validate_api_key, AuthError
from tech_dev_agents.monday_agent import AgentMondayClient, AgentIdentity, PHASE_NAMES
from tech_dev_agents.cost_collector import parse_done_lines
```

### 12.3 Adaptation Notes

| Module Function | Adaptation Needed | Approach |
|----------------|-------------------|----------|
| `validate_api_key()` | None | Direct use in FastAPI dependency |
| `build_health_snapshot()` | Need to map VM HTTP response to function args | Parse JSON response from agent VM, pass fields as kwargs |
| `aggregate_usage()` | Need to create `CostEvent` list from Loki data | Parse Loki results into `CostEvent` via `build_cost_event()` |
| `evaluate_alerts()` | Need `AlertThreshold` config | Load from env/config, pass with cost events |
| `evaluate_health_alerts()` | Synchronous, needs snapshots | Call after `get_all_health()`, pass snapshots directly |
| `AgentMondayClient.get_story()` | Synchronous (uses `requests`) | Wrap in `asyncio.to_thread()` |
| `parse_done_lines()` | Designed for file text, works on any text | Apply to Loki log line strings directly |

---

## 13. Error Handling

### 13.1 Backend Error Strategy

```python
# main.py exception handlers

@app.exception_handler(AgentNotFoundError)
async def agent_not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})

@app.exception_handler(AuthError)
async def auth_error_handler(request, exc):
    return JSONResponse(status_code=401, content={"detail": str(exc)})

@app.exception_handler(httpx.HTTPError)
async def upstream_error_handler(request, exc):
    return JSONResponse(status_code=502, content={
        "detail": f"Upstream service error: {exc}"
    })
```

### 13.2 Error Response Format

All error responses follow:

```json
{
  "detail": "Human-readable error message"
}
```

### 13.3 HTTP Status Codes

| Code | When |
|------|------|
| 200 | Success |
| 400 | Invalid request (bad params, agent disabled) |
| 401 | Missing or invalid API key |
| 404 | Agent not found in registry |
| 422 | Request body validation failure (Pydantic) |
| 502 | Upstream service failure (agent VM, Loki, Azure, Monday.com) |
| 503 | Console itself is degraded (startup in progress) |

### 13.4 Frontend Error States

| Scenario | UI Behavior |
|----------|-------------|
| API returns 401 | Clear localStorage, redirect to login |
| API returns 502 | Show "Service unavailable" with retry button |
| API returns 404 | Show "Agent not found" message |
| Network error | Show "Connection lost" banner, auto-retry |
| Stale data (cache hit, upstream down) | Show data with "Last updated X ago" badge |
| Partial failure (1 of N VMs down) | Show available agents; offline agent shows "unknown" status |

---

## 14. Implementation Plan

### Phase 8 Implementation Order

#### Week 1: Backend Foundation (8 tasks)

1. **Project scaffold** — Create `ops-console/` directory structure, `pyproject.toml`, FastAPI app shell (`main.py`), config module
2. **Auth module** — `auth.py` with `require_api_key` dependency wrapping `health_api.validate_api_key`
3. **Agent service** — Registry loader, async health poll fan-out, cache layer
4. **Routes: agents + health** — `/api/agents`, `/api/agents/{name}`, `/api/health`
5. **Loki client** — HTTP query_range wrapper, cost summary parser, anomaly queries
6. **Cost service** — SDK costs from Loki, Azure cost client (optional), cost breakdown aggregation
7. **Alert service** — Merge cost + health + Loki alerts. Routes: `/api/alerts`
8. **Fleet + controls** — `/api/fleet` (aggregation), `/api/agents/{name}/restart`, `/api/agents/{name}/pause`

#### Week 2: Frontend (7 tasks)

1. **Scaffold** — Vite + React + TypeScript + Tailwind + TanStack Query setup
2. **API client + auth** — Typed fetch wrapper, login page, auth guard
3. **FleetOverview + AgentGrid** — Stat cards, agent card grid with status badges
4. **AgentDetail** — Detail page shell with tabs/sections
5. **CostChart** — Recharts stacked bar chart with date range selector
6. **ActivityTimeline + AlertHistory** — Event list, agent-specific alert table
7. **Controls + AlertBanner + AlertPanel** — Restart/pause buttons, anomaly banner, fleet alert table

#### Week 3: Integration & Deploy (5 tasks)

1. **Backend tests** — pytest with httpx TestClient, mocked external services
2. **Frontend integration** — End-to-end smoke with real backend
3. **Error states** — Loading spinners, error messages, stale data badges
4. **Deployment** — Nginx config, systemd service, Let's Encrypt, deploy script
5. **Smoke test** — Verify on `ops.gorillacommerce.ai`

### Dependencies Between Tasks

```
Auth ──► Agent Service ──► Routes (agents) ──► Fleet
                │                                 │
                └──► Cost Service ────────────────┘
                │         │
                │    Loki Client
                │
                └──► Alert Service ──► Routes (alerts)

(Frontend tasks depend on corresponding backend routes being complete)
```

---

## Appendix A: Agent Registry Format

**File:** `agent-registry.json`

```json
{
  "agents": [
    {
      "name": "dan",
      "host": "10.0.1.10",
      "port": 8080,
      "role": "developer",
      "enabled": true,
      "azure_resource_group": "rg-agent-dan"
    },
    {
      "name": "derrick",
      "host": "10.0.1.11",
      "port": 8080,
      "role": "developer",
      "enabled": true,
      "azure_resource_group": "rg-agent-derrick"
    }
  ]
}
```

The registry is loaded by `AgentService` at startup and reloaded when the file's mtime changes. Each entry maps to an `agent_dashboard.AgentRecord` (with the additional `azure_resource_group` field stored separately for the cost service).

---

## Appendix B: TypeScript API Types

```typescript
// types/api.ts

export type AgentStatus = "online" | "idle" | "stuck" | "offline";

export interface AgentSummary {
  name: string;
  status: AgentStatus;
  role: string;
  enabled: boolean;
  last_activity: string | null;
  uptime_seconds: number;
  active_sessions: number;
  error_count: number;
  checked_at: string;
  current_story: string | null;
  current_phase: string | null;
  today_cost_usd: number;
}

export interface AgentListResponse {
  agents: AgentSummary[];
  total: number;
  fetched_at: string;
}

export interface StoryInfo {
  item_id: number;
  name: string;
  phase: string | null;
  status: string | null;
  group: string | null;
}

export interface CostToday {
  sdk_cost_usd: number;
  azure_cost_usd: number;
  total_cost_usd: number;
  sdk_sessions: number;
  sdk_turns: number;
}

export interface ActivityEvent {
  id: string;
  type: string;
  description: string;
  detail: string | null;
  timestamp: string;
  source: string;
}

export interface AgentDetailResponse {
  name: string;
  status: AgentStatus;
  role: string;
  enabled: boolean;
  host: string;
  port: number;
  last_activity: string | null;
  uptime_seconds: number;
  active_sessions: number;
  error_count: number;
  checked_at: string;
  current_story: StoryInfo | null;
  cost_today: CostToday;
  recent_activity: ActivityEvent[];
}

export interface DailyCost {
  date: string;
  sdk_cost_usd: number;
  azure_cost_usd: number;
  total_cost_usd: number;
  sdk_sessions: number;
  sdk_turns: number;
}

export interface DataFreshness {
  sdk_as_of: string;
  azure_as_of: string | null;
  azure_is_estimated: boolean;
}

export interface CostBreakdownResponse {
  agent_name: string;
  period_start: string;
  period_end: string;
  granularity: string;
  total_sdk_cost_usd: number;
  total_azure_cost_usd: number;
  total_cost_usd: number;
  daily: DailyCost[];
  data_freshness: DataFreshness;
}

export interface FleetAgentSummary {
  name: string;
  status: AgentStatus;
  current_story: string | null;
  today_cost_usd: number;
}

export interface FleetOverviewResponse {
  total_daily_spend_usd: number;
  active_agents: number;
  total_agents: number;
  online_agents: number;
  idle_agents: number;
  stuck_agents: number;
  offline_agents: number;
  stories_in_progress: number;
  fleet_health_score: number;
  active_alerts: number;
  fetched_at: string;
  agents: FleetAgentSummary[];
}

export interface AlertItem {
  id: string;
  agent_name: string;
  type: string;
  severity: string;
  message: string;
  active: boolean;
  triggered_at: string;
  resolved_at: string | null;
  source: string;
}

export interface AlertListResponse {
  alerts: AlertItem[];
  total: number;
  active_count: number;
  fetched_at: string;
}

export interface RestartRequest {
  reason: string;
  force?: boolean;
}

export interface RestartResponse {
  agent_name: string;
  success: boolean;
  message: string;
  previous_status: string;
  new_status: string | null;
  completed_at: string;
  requested_by: string;
}

export interface PauseRequest {
  action: "pause" | "resume";
  reason?: string;
}

export interface PauseResponse {
  agent_name: string;
  action: string;
  success: boolean;
  message: string;
  timestamp: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  uptime_seconds: number;
  agents_reachable: number;
  agents_total: number;
  loki_reachable: boolean;
  checked_at: string;
}
```

---

## Appendix C: FastAPI Application Startup

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import httpx

from ops_console.config import get_settings
from ops_console.routes import agents, fleet, alerts, health

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create shared HTTP client and services. Shutdown: close client."""
    settings = get_settings()
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    # Initialize services (stored in app.state for dependency injection)
    app.state.agent_service = AgentService(
        registry_path=settings.agent_registry_path,
        http_client=app.state.http_client,
    )
    app.state.loki_client = LokiClient(
        base_url=settings.loki_url,
        api_key=settings.loki_api_key,
        http_client=app.state.http_client,
    )
    app.state.azure_client = (
        AzureCostClient(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
            subscription_id=settings.azure_subscription_id,
            http_client=app.state.http_client,
        )
        if settings.azure_enabled
        else None
    )
    app.state.cost_service = CostService(
        loki=app.state.loki_client,
        azure=app.state.azure_client,
    )
    app.state.alert_service = AlertService(
        loki=app.state.loki_client,
        agent_service=app.state.agent_service,
        cost_service=app.state.cost_service,
    )
    # Monday.com clients
    monday_cfg = json.loads(settings.monday_config)
    app.state.monday_service = MondayService(
        clients={
            name: AgentMondayClient(AgentIdentity(
                agent_name=name,
                api_token=cfg["api_token"],
                board_id=cfg["board_id"],
            ))
            for name, cfg in monday_cfg.items()
        }
    )
    app.state.start_time = time.time()

    yield

    await app.state.http_client.aclose()


app = FastAPI(title="Agent Operations Console", lifespan=lifespan)

# API routes
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(agents.router, prefix="/api", tags=["agents"])
app.include_router(fleet.router, prefix="/api", tags=["fleet"])
app.include_router(alerts.router, prefix="/api", tags=["alerts"])

# Static files (React SPA) — must be last (catch-all)
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="spa")
```

---

## Appendix D: Deployment Configuration

### Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name ops.gorillacommerce.ai;

    ssl_certificate /etc/letsencrypt/live/ops.gorillacommerce.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ops.gorillacommerce.ai/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name ops.gorillacommerce.ai;
    return 301 https://$host$request_uri;
}
```

### systemd

```ini
[Unit]
Description=Agent Operations Console
After=network.target

[Service]
Type=simple
User=ops-console
Group=ops-console
WorkingDirectory=/opt/ops-console
ExecStart=/opt/ops-console/venv/bin/uvicorn ops_console.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/opt/ops-console/.env

[Install]
WantedBy=multi-user.target
```

### Makefile

```makefile
.PHONY: dev build test deploy

dev:
	cd frontend && npm run dev &
	cd backend && uvicorn ops_console.main:app --reload --host 0.0.0.0 --port 8000

build:
	cd frontend && npm ci && npm run build
	cp -r frontend/dist backend/ops_console/static

test:
	cd backend && python -m pytest tests/ -v

deploy: build
	rsync -avz backend/ ops-server:/opt/ops-console/
	ssh ops-server "sudo systemctl restart ops-console"
```
