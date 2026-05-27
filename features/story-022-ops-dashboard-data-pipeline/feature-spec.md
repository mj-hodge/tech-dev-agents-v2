# Phase 6: Feature Specification — STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Scope:** Medium
**Approach:** B (Defensive Refactor)

---

## Overview

Fix three categories of bugs preventing the ops dashboard from displaying real data. The frontend is working (59/59 tests pass); all changes are backend Python and MCP TypeScript client fixes.

---

## Change Set 1: MCP Client Response Envelope Unwrapping (AC-1)

### Problem

The MCP TypeScript client (`tools/agent-ops-mcp/src/client.ts`) expects bare arrays from API calls, but the FastAPI backend returns envelope objects (`{agents: [...]}`, `{alerts: [...]}`, `{messages: [...]}`). This causes "is not iterable" errors.

### Design

**File: `tools/agent-ops-mcp/src/client.ts`**

Add envelope-aware response types and unwrap in each method:

```typescript
// New envelope interfaces (internal to client)
interface AgentListEnvelope {
  agents: AgentSummary[];
  total: number;
  fetched_at: string;
}

interface AlertListEnvelope {
  alerts: AlertItem[];
  total: number;
  active_count: number;
  fetched_at: string;
}

interface MessageListEnvelope {
  agent_name: string;
  messages: MessageItem[];
}
```

**Method changes:**

| Method | Current Return | New Implementation |
|--------|---------------|-------------------|
| `listAgents()` | `request<AgentSummary[]>(...)` | `request<AgentListEnvelope>(...)` then return `.agents` |
| `getAlerts()` | `request<AlertItem[]>(...)` | `request<AlertListEnvelope>(...)` then return `.alerts` |
| `readMessages()` | `request<MessageItem[]>(...)` | `request<MessageListEnvelope>(...)` then return `.messages` |

**Defensive guard:** Each method validates the unwrapped field is an array, returns `[]` if not:
```typescript
async listAgents(): Promise<AgentSummary[]> {
  const envelope = await this.request<AgentListEnvelope>("GET", "/api/agents");
  return Array.isArray(envelope.agents) ? envelope.agents : [];
}
```

**Parameter fix for `readMessages`:** Keep `limit` param (backend accepts both `limit` and `count`, prioritizing `count` but falling back to `limit`). No change needed — the current `limit` param works correctly.

### Tests to Update

**File: `tools/agent-ops-mcp/tests/tools.test.ts`**

Update mock responses to return envelope shapes instead of bare arrays. This ensures tests verify the actual API contract:

```typescript
// Before (wrong — bare array):
mockFetchResponse([{ name: "dan", status: "ONLINE", ... }]);

// After (correct — envelope):
mockFetchResponse({ agents: [{ name: "dan", status: "ONLINE", ... }], total: 1, fetched_at: "..." });
```

Update for all three tools: `list_agents`, `get_alerts`, `read_messages`.

---

## Change Set 2: Cost Tracking Regex Fix (AC-2)

### Problem

`loki_client.py` line 234:
```python
_DONE_RE = re.compile(r"\[DONE\]\s+.*?cost=\$?([\d.]+).*?turns=(\d+)")
```
This regex expects `cost=` before `turns=`, but `claude_sdk_tool.py` outputs `turns=` first:
```
[DONE] turns=1 tools=0 cost=$16.59 duration=30s stop=end error=False
```
After matching `cost=$16.59`, the regex looks for `turns=` in the remainder (`duration=30s...`), which never matches.

### Design

**File: `tech_dev_agents/ops_console/services/loki_client.py`**

Replace single regex with two independent field extractors:

```python
# Order-independent field extraction
_DONE_COST_RE = re.compile(r"cost=\$?(?P<cost>[\d.]+)")
_DONE_TURNS_RE = re.compile(r"(?<!\w)turns=(?P<turns>\d+)")


def _parse_done_line(line: str) -> dict[str, Any] | None:
    """Parse a [DONE] log line for cost data. Field-order agnostic."""
    if "[DONE]" not in line:
        return None
    cost_m = _DONE_COST_RE.search(line)
    if not cost_m:
        return None  # Cost is required
    turns_m = _DONE_TURNS_RE.search(line)
    return {
        "cost": float(cost_m.group("cost")),
        "turns": int(turns_m.group("turns")) if turns_m else 0,
    }
```

**Key decisions:**
- Cost is required (skip lines without it — handles `cost=$?` auth failure lines)
- Turns default to 0 if missing
- `(?<!\w)` prevents matching `sdk_turns=` in [COST_SUMMARY] lines accidentally
- Named groups for clarity

### Tests

New test cases:
1. Parse `[DONE]` with `turns=` before `cost=` (actual output format)
2. Parse `[DONE]` with `cost=` before `turns=` (backwards compat)
3. Skip `[DONE]` with `cost=$?` (auth failure)
4. Default turns to 0 when missing

---

## Change Set 3: Cost Collector Systemd Timer (AC-2)

### Problem

`cost_collector.py` exists but nothing schedules it. No `[COST_SUMMARY]` lines are ever generated.

### Design

**New file: `deployment/vm/cost-collector.service`**
```ini
[Unit]
Description=Daily SDK cost collection and summary
After=network.target

[Service]
Type=oneshot
User=hermes
ExecStart=/usr/bin/python3 /opt/agent/cost_collector.py --log-dir /tmp/claude-sdlc-logs --output /tmp/claude-sdlc-logs/cost-summary.log
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cost-collector
```

**New file: `deployment/vm/cost-collector.timer`**
```ini
[Unit]
Description=Run cost collector daily at 23:55 UTC

[Timer]
OnCalendar=*-*-* 23:55:00
Persistent=true

[Install]
WantedBy=timers.target
```

**Update: `deployment/vm/cloud-init.yaml`**

Add to runcmd section:
```yaml
- cp /opt/agent/cost-collector.service /etc/systemd/system/
- cp /opt/agent/cost-collector.timer /etc/systemd/system/
- systemctl daemon-reload
- systemctl enable --now cost-collector.timer
```

### Design Rationale

- Systemd timer (not crontab) — consistent with existing `hermes-gateway.service` and `hermes-log-sync.service` patterns
- `Persistent=true` ensures missed runs fire on next boot
- Output goes to journal, picked up by Promtail automatically
- 23:55 UTC = end of day, captures full daily totals

---

## Change Set 4: Agent Detail Response Enrichment (AC-3)

### Problem

`AgentDetailResponse` doesn't include `cost_7d` or `cost_30d`. The `recent_activity` field returns an empty list (stub).

### Design

**File: `tech_dev_agents/ops_console/models/responses.py`**

Add fields to `AgentDetailResponse`:
```python
class AgentDetailResponse(BaseModel):
    # ... existing fields ...
    cost_today: CostToday
    cost_7d: float = 0.0      # NEW: 7-day total SDK+Azure cost
    cost_30d: float = 0.0     # NEW: 30-day total SDK+Azure cost
    recent_activity: list[ActivityEvent]
```

**File: `tech_dev_agents/ops_console/routes/agents.py`**

In `get_agent_detail()`, fetch 7d and 30d costs concurrently:

```python
import asyncio

# Concurrent fetches
story_task = asyncio.create_task(monday_service.get_current_story(name))
cost_today_task = asyncio.create_task(cost_service.get_today_cost(name))
cost_7d_task = asyncio.create_task(cost_service.get_cost_breakdown(name, days=7))
cost_30d_task = asyncio.create_task(cost_service.get_cost_breakdown(name, days=30))
activity_task = asyncio.create_task(monday_service.get_activity_events(name))

story, cost_today, cost_7d_breakdown, cost_30d_breakdown, activity = await asyncio.gather(
    story_task, cost_today_task, cost_7d_task, cost_30d_task, activity_task
)

return AgentDetailResponse(
    # ... existing fields ...
    cost_today=cost_today,
    cost_7d=cost_7d_breakdown.total_cost_usd,
    cost_30d=cost_30d_breakdown.total_cost_usd,
    recent_activity=activity,
)
```

**Graceful degradation:** Wrap each task in try/except to return defaults on failure:
```python
async def _safe_cost_breakdown(cost_service, name, days):
    try:
        breakdown = await cost_service.get_cost_breakdown(name, days=days)
        return breakdown.total_cost_usd
    except Exception:
        logger.warning("Failed to fetch %dd cost for %s", days, name, exc_info=True)
        return 0.0
```

---

## Change Set 5: Fleet Overview Data (AC-3)

### Problem

Fleet overview shows `stories_in_progress: 0` and `busy_agents: 0` even when agents are working.

### Design

The fleet route (`routes/fleet.py`) already correctly:
- Calls `monday_service.get_stories_in_progress()`
- Computes `busy_count` from `health.active_sessions`
- Computes `total_daily_spend_usd` via `cost_service.get_fleet_daily_spend()`

**Root cause:** The underlying services return 0/empty because:
1. Cost pipeline is broken (fixed by Change Sets 2 + 3)
2. Monday.com integration depends on correct board/column IDs

**No code changes needed in fleet.py** — once the cost pipeline and Monday.com service are working, fleet data will populate automatically.

**Verification:** After deploying Change Sets 2 + 3, verify fleet endpoint returns non-zero values.

---

## Change Set 6: Context Endpoint (AC-4)

### Problem

Context endpoint fields (`teams_link`, `blocker_status`, `last_message`) are already implemented in `context_service.py` but need verification.

### Design

**File: `tech_dev_agents/ops_console/routes/context.py`**

The route already delegates to `context_service.get_full_context(name)`, which concurrently fetches:
- `teams_chat_link` — from agent email registry
- `current_story` — from Monday.com
- `last_commits` — stub (TODO)
- `last_message` — from Teams Graph API
- `blocker_status` — from Teams message content detection

**No structural changes needed.** The context service is correctly implemented with graceful degradation (returns `None` on failures).

**Test verification needed:**
1. Verify `teams_client.get_agent_email()` returns valid emails for registered agents
2. Verify `read_messages()` succeeds against Graph API
3. Verify `detect_blocker()` regex patterns match real message formats

---

## Change Set 7: Alert Pipeline (AC-5)

### Problem

Alert endpoint returns data correctly (envelope unwrapping is the only bug — fixed in Change Set 1). The `stuck_agent` alert needs verification.

### Design

**File: `tech_dev_agents/ops_console/services/alert_service.py`**

Verify that the alert service:
1. Has a `stuck_agent` alert type
2. Fires when health check returns offline for >5 minutes
3. `get_alerts()` returns `AlertListResponse` envelope (already does)

**MCP client fix** (from Change Set 1) resolves the "alerts is not iterable" error.

---

## File Change Summary

| File | Action | Change |
|------|--------|--------|
| `tools/agent-ops-mcp/src/client.ts` | Modify | Add envelope types, unwrap responses in 3 methods |
| `tools/agent-ops-mcp/tests/tools.test.ts` | Modify | Update mock responses to envelope shape |
| `tech_dev_agents/ops_console/services/loki_client.py` | Modify | Replace `_DONE_RE` with order-independent regex pair |
| `tech_dev_agents/ops_console/models/responses.py` | Modify | Add `cost_7d`, `cost_30d` to `AgentDetailResponse` |
| `tech_dev_agents/ops_console/routes/agents.py` | Modify | Fetch 7d/30d costs concurrently, graceful degradation |
| `deployment/vm/cost-collector.service` | New | Systemd service unit |
| `deployment/vm/cost-collector.timer` | New | Systemd timer unit (daily 23:55 UTC) |
| `deployment/vm/cloud-init.yaml` | Modify | Add cost-collector timer setup |
| `tests/test_loki_done_parsing.py` | New | Tests for order-independent [DONE] regex |
| `tests/test_agent_detail_cost.py` | New | Tests for 7d/30d cost enrichment |

**Total files modified:** 6
**Total files created:** 4
**Frontend changes:** 0 (as required by seed)

---

## API Contract Changes

### Modified: `GET /api/agents/{name}`

**Added fields:**
```json
{
  "cost_7d": 45.23,
  "cost_30d": 187.50
}
```

These are additive (non-breaking). Existing consumers that don't read these fields are unaffected. Default value is `0.0`.

### No other API contract changes

All other endpoints keep their existing response shapes. The MCP client changes are client-side only — they correctly parse the existing API responses.

---

## Error Handling Strategy

| Failure | Behavior |
|---------|----------|
| Loki unreachable | Cost fields return 0.0, no error to caller |
| Monday.com API fails | `current_story` returns null, `stories_in_progress` returns 0 |
| Teams Graph API fails | `last_message` and `blocker_status` return null |
| Cost breakdown query fails | `cost_7d`/`cost_30d` return 0.0 |
| Promtail not running | No data flows to Loki — documented as ops prerequisite |

All error paths log warnings with `exc_info=True` for debugging.

---

## Dependencies & Prerequisites

| Dependency | Status | Impact |
|-----------|--------|--------|
| Derrick's API key refresh | Blocker for live testing | Code changes are independent |
| Promtail on agent VMs | Ops prerequisite | Must be verified manually post-deploy |
| Monday.com board (18405631030) | Available | Used for story/phase queries |
| Loki endpoint | Available | Used for cost data queries |
| Teams Graph API | Available | Used for messages/blockers |
