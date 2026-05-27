# Feature Spec — STORY-015: Deploy Agent Dashboards & Wiring

## Scope: Medium

## Overview
Wire STORY-012/013/014 data models into real infrastructure: Grafana dashboard, cost collection cron, Monday.com phase hooks, health/restart API endpoints.

## Components

### 1. Health API (`tech_dev_agents/health_api.py`)

**Endpoints:**
- `GET /api/health` — Returns agent health JSON
  - Response: `{ "agent_name", "status", "last_activity", "uptime_seconds", "active_sessions", "error_count", "version" }`
  - No auth required (read-only health check)
- `POST /api/restart` — Triggers agent restart
  - Requires `X-API-Key` header matching `AGENT_API_KEY` env var
  - Request body: `{ "reason": "string", "requested_by": "string", "force": bool }`
  - Response: `RestartResult` JSON
  - Returns 401 if API key missing/invalid
  - Returns 503 if restart fails

**Implementation:** Pure functions returning dicts. No FastAPI dependency in the module itself — the module provides `build_health_response()` and `build_restart_response()` functions. A thin ASGI wrapper can be added at deploy time.

### 2. Cost Collector (`tech_dev_agents/cost_collector.py`)

**Function:** `collect_costs_from_logs(log_dir, agent_name) -> CostSummary`
- Parses `session-*.log` files for `[DONE] cost=$X` lines
- Extracts: session count, total cost, total turns
- Returns a `CostSummary` dataclass

**Function:** `format_cost_summary_log(summary) -> str`
- Formats: `[COST_SUMMARY] agent=dan date=2026-03-31 sdk_sessions=14 sdk_cost=$16.59 sdk_turns=419`

**Cron integration:** Script entry point that calls both functions and writes to combined log.

### 3. Monday.com Hooks (`tech_dev_agents/monday_hooks.py`)

**Function:** `on_story_start(client, item_id, agent_name) -> StoryStatusTransition`
- Moves story to "In Progress" group
- Returns the transition record

**Function:** `on_story_complete(client, item_id, agent_name, summary) -> tuple[StoryStatusTransition, PhaseComment]`
- Moves story to "Done" group  
- Posts completion summary comment
- Returns transition + comment records

**Function:** `on_phase_complete(client, item_id, comment) -> dict`
- Posts phase comment to Monday.com item

### 4. Grafana Dashboard (`deploy/grafana-agent-dashboard.json`)

Provisioned JSON with panels:
- Cost per agent per day (LogQL)
- Sessions per agent (LogQL)
- Agent status (stat panel querying health endpoints)
- Restart link per agent (link panel with URL to `/api/restart`)

## Data Flow

```
SDK logs → cost_collector.py (cron) → structured log → Promtail → Loki → Grafana
Agent VM → health_api.py → /api/health → Grafana stat panel
Grafana link → /api/restart → health_api.py → restart logic
Phase transition → monday_hooks.py → Monday.com API
```

## Security

- Restart endpoint protected by API key (`AGENT_API_KEY` env var)
- Health endpoint is read-only, no auth required
- Monday.com tokens from per-agent `AgentIdentity` (STORY-013)

## Testing Strategy

All modules are pure functions with injected dependencies. No HTTP framework dependency in tests.
