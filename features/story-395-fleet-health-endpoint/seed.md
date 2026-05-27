# Seed: Fleet Health Monitoring Endpoint

**Story:** STORY-395
**Scope:** Small
**Phase Path:** 1 -> 7 -> 8 -> Done
**Created:** 2026-04-18

---

## Problem Statement

Morris is a single point of failure for fleet monitoring. Today, fleet health is only visible through the authenticated `/api/fleet` endpoint (which fetches per-agent cost data, Monday.com stories, and Loki queries -- heavy and slow) or through Morris's cron-generated `state/morris/fleet-health.md` markdown file. Neither is suitable for external monitoring tools (Uptime Kuma, Azure Monitor, Datadog) that need a lightweight, unauthenticated, machine-readable health signal.

If Morris goes down or the ops-console becomes unhealthy, there is no external system that can detect the fleet is degraded. We need a dedicated endpoint that monitoring infrastructure can poll without authentication, returning just enough data to determine fleet health status.

## Target User

- **External monitoring tools** (Uptime Kuma, Azure Monitor, Datadog) that poll HTTP endpoints on a schedule
- **Operations dashboards** that need a quick fleet status check without full authentication
- **Alerting pipelines** that need to detect fleet degradation and page on-call

## Acceptance Criteria

1. **AC-1:** `GET /api/health/fleet` returns HTTP 200 with `Content-Type: application/json` when the fleet is healthy.
2. **AC-2:** The endpoint requires **no authentication** (same as `/api/health`), suitable for external monitor polling.
3. **AC-3:** Response JSON includes an `agents` array where each entry contains: `name` (str), `status` (online|idle|stuck|offline), `last_seen` (ISO 8601 timestamp or null).
4. **AC-4:** Response JSON includes a `queue` object with `pending` (int) and `claimed` (int) counts from the dispatch service.
5. **AC-5:** Response JSON includes a top-level `status` field: `"healthy"` when all agents are online/idle and queue pending <= 20, or `"degraded"` when any agent is stuck/offline or queue pending > 20.
6. **AC-6:** The endpoint returns HTTP 503 with the same JSON body (but `status: "degraded"`) when the fleet is in a degraded state, enabling monitor tools to alert on non-2xx responses.
7. **AC-7:** Response time is under 2 seconds. The endpoint uses only cached agent health data (`AgentService.get_all_health()`, 30s cache) and a single lightweight dispatch queue count -- no per-agent HTTP calls, no Loki queries, no Monday.com lookups.
8. **AC-8:** Response JSON includes a `checked_at` field with the current UTC timestamp in ISO 8601 format.

## Scope Classification

**Small** -- Single new route file, one new response model, reuses existing `AgentService.get_all_health()` and `DispatchService.pending_count()` / `list_queue()`. No new services, no database migrations, no external API integrations.

## Technical Notes

### Existing Infrastructure to Reuse

- **`AgentService.get_all_health()`** -- Returns `list[AgentHealthSnapshot]` with 30-second caching. Each snapshot has `agent_name`, `status` (AgentActivityStatus enum: online/idle/stuck/offline), `last_activity` (ISO 8601), `checked_at`.
- **`DispatchService.pending_count()`** / **`DispatchDBService.pending_count()`** -- Returns int count of pending items.
- **`DispatchService.list_queue()`** -- Returns dict with `pending` and `claimed` lists; we only need `len()` of each.
- **`map_agent_status()`** in `routes/_status.py` -- Converts health snapshots to `AgentStatusEnum`.
- **Route registration pattern** in `main.py` -- Health router is already public (no auth dependency). New endpoint goes on the existing health router or a new router registered without auth.

### Design Decisions

- **Mount on existing health router** (`routes/health.py`) since it's already public and `/health/fleet` is a natural sub-path.
- **Use `AgentHealthSnapshot.last_activity`** as the `last_seen` field (closest available proxy for heartbeat time).
- **Degraded logic:** Any agent with status `stuck` or `offline` OR `queue.pending > 20` triggers degraded state and 503 response code.
- **No UNREACHABLE status** -- The `AgentStatusEnum` used in response models doesn't include UNREACHABLE; agents that can't be reached show as OFFLINE via `map_agent_status()`.

### Response Schema

```json
{
  "status": "healthy",
  "agents": [
    {
      "name": "dan",
      "status": "online",
      "last_seen": "2026-04-18T12:00:00Z"
    }
  ],
  "queue": {
    "pending": 3,
    "claimed": 1
  },
  "checked_at": "2026-04-18T12:00:05Z"
}
```

### Error Resilience

- If `AgentService.get_all_health()` throws, return `agents: []` with `status: "degraded"` and 503.
- If dispatch queue count fails, return `queue: {"pending": -1, "claimed": -1}` with `status: "degraded"` and 503.
- The endpoint itself should never return 500 -- always return structured JSON.

## Out of Scope

- **Per-agent HTTP calls or Loki queries** -- This endpoint must be lightweight; the existing `/api/fleet` handles rich data.
- **Authentication** -- Explicitly excluded; this is a monitoring endpoint.
- **Historical data or trends** -- This is a point-in-time snapshot only.
- **Agent restart/pause controls** -- Read-only endpoint.
- **SDK session or cost data** -- Not relevant for health monitoring.
- **WebSocket or push notifications** -- Polling-only design.
- **Custom thresholds via query params** -- Hardcoded degraded thresholds (pending > 20) for v1; configurable later if needed.
