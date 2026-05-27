# STORY-304: Event-Driven Teams Presence

**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Assignee:** Dan

## Problem

Current presence detection uses `_presence_monitor_loop` which polls `pgrep` every 5 seconds to guess whether the agent is busy. This is unreliable and racey — dispatch already knows the ground truth (claim/complete/fail/cancel transitions).

## Solution

Two distinct presence paths:

### 1. Dev Agents (Dan, etc.)
Ops-console pushes `Busy`/`Available` on dispatch claim/complete/fail/cancel via new `POST /internal/presence` on the agent's health server (port 8080). Source of truth = postgres `dispatch_items` table.

### 2. Morris (Manager)
Doesn't take dispatch tickets. His heartbeat decides locally:
- `sdk_count + unread_inbox > 0` → Busy
- else → Available
Heartbeat calls `_set_presence` directly.

## Acceptance Criteria

- **AC-1:** `POST /internal/presence` endpoint on health_server.py accepts `{"availability": "...", "activity": "..."}`, authenticated via `OPS_CONSOLE_API_KEY`
- **AC-2:** Dispatch claim pushes `Busy` to the claiming agent's gateway
- **AC-3:** Dispatch complete/fail/cancel pushes `Available` to the agent's gateway
- **AC-4:** `_presence_monitor_loop` and `_is_busy` removed from TeamsAdapter
- **AC-5:** Morris heartbeat: `sdk_count + unread_inbox > 0` → Busy, else Available
- **AC-6:** Backward compat: ops-console presence push fails silently if endpoint not deployed yet

## Constraints

- No new infra (no Redis/bus); direct HTTP push from ops-console to agent gateway
- Authenticate `/internal/presence` with `OPS_CONSOLE_API_KEY` (already on each VM)
- Remove `_presence_monitor_loop` and `_is_busy` entirely; dispatch DB + heartbeat are authoritative
- Agent IP captured during dispatch registration for push targeting

## Files Changed

| File | Change |
|------|--------|
| `deployment/hermes/health_server.py` | Add `POST /internal/presence` with API key auth |
| `deployment/hermes/teams_m365.py` | Remove `_presence_monitor_loop`, `_is_busy`; add Morris heartbeat |
| `tech_dev_agents/ops_console/routes/dispatch.py` | Push presence on claim/complete/fail/cancel |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | Capture agent IP on registration; add `get_agent_ip()` |
| `scripts/presence_manager.py` | No changes (reused by health_server) |
