# Seed: Fix Ops Dashboard Data Pipeline

**Story:** STORY-022
**Date:** 2026-04-07
**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done
**Assignee:** Derrick (after API key fix)

---

## Problem Statement

The ops dashboard (STORY-019) renders correctly but displays no useful data. Every metric shows zero or empty. The operator (Mark) cannot tell which agent is busy, what they're working on, or how much they're spending. The dashboard is functionally useless despite having a complete frontend (59/59 tests passing).

**Root causes identified (three categories):**

### Category 1: MCP Client Response Parsing Bugs
- `list_agents` tool throws `"agents is not iterable"` — backend returns `{agents: [...]}` but MCP client expects bare array
- `get_alerts` tool throws `"alerts is not iterable"` — same wrapping mismatch
- `read_messages` endpoint returns 502: `TeamsClient.read_messages() got an unexpected keyword argument 'count'`

### Category 2: Cost Tracking Pipeline Broken End-to-End
- **SDK auth failure:** Derrick's API key returns 401 — no SDK sessions run, so no `[DONE]` lines are logged
- **Regex mismatch:** `loki_client.py` regex expects `cost=` before `turns=`, but `claude_sdk_tool.py` outputs `turns=` first — even valid logs won't parse
- **No cost collection cron:** `cost_collector.py` exists but no cron/systemd timer runs it, so `[COST_SUMMARY]` lines are never generated
- **Promtail unverified:** No evidence Promtail is running on agent VMs or authenticated to Loki

### Category 3: Agent Context/Status Gaps
- `current_story`: always null — Monday.com/Asana integration not wired
- `recent_activity`: always empty — no activity event sources connected
- `teams_link`: not returned — context endpoint not populating
- `blocker_status`: not returned — message parsing not connected
- `uptime_seconds`: always 0 — health check doesn't track session duration
- `cost_7d` / `cost_30d`: not returned from detail endpoint

---

## Desired Outcome

When this story is complete, the ops dashboard shows **real, live data**:

1. **Fleet overview bar:** Accurate total daily spend, correct busy/idle/offline counts, real story count
2. **Agent cards:** Each card shows current story name + phase, today's cost (non-zero when running), correct status badge, last activity timestamp
3. **Agent detail view:** 30-day cost chart with real data, activity timeline with recent commits/phase changes, Teams link, blocker badge
4. **MCP tools:** `list_agents`, `get_alerts`, `read_messages` all return data without errors
5. **Alerts:** Alert history queryable and displays in dashboard

---

## Users

**Primary:** Mark (engineering manager) — needs at-a-glance fleet visibility from CLI (MCP tools) and browser (dashboard)
**Secondary:** Future ops team members using the dashboard

---

## Constraints

| Constraint | Value |
|-----------|-------|
| Budget | Internal tooling — minimize Azure cost, SDK cost acceptable |
| Scale | 2-5 agents (current: 2) |
| Timeline | Blocking ops visibility — high priority |
| Resources | Derrick (after API key fix) |
| Tech | FastAPI backend, React frontend (no frontend changes expected), MCP TypeScript client, Loki for logs, Azure Cost Management |

---

## Acceptance Criteria

### AC-1: MCP Client Response Parsing
- [ ] `list_agents` returns agent list without error
- [ ] `get_alerts` returns alert list (empty or populated) without error
- [ ] `read_messages` returns message history without 502 error
- [ ] All three tools tested against live API and return valid markdown

### AC-2: Cost Tracking Pipeline
- [ ] `[DONE]` log lines from `claude_sdk_tool.py` are correctly parsed by `loki_client.py` regex (field order agnostic)
- [ ] `cost_collector.py` runs on a schedule (cron or systemd timer) and writes `[COST_SUMMARY]` lines
- [ ] Promtail is verified running on both agent VMs and pushing logs to Loki
- [ ] `/api/fleet` returns non-zero `total_daily_spend_usd` when agents have run sessions
- [ ] `/api/agents/{name}` returns non-zero `cost_today`, `cost_7d`, `cost_30d` with real data
- [ ] CostChart component renders actual 30-day history

### AC-3: Agent Status & Current Work
- [ ] `/api/agents/{name}` returns `current_story` with name and phase when agent is working
- [ ] `/api/agents/{name}` returns `recent_activity` with at least commit and phase-change events
- [ ] Fleet overview shows correct `busy_agents` count (agents actively running SDK sessions)
- [ ] Fleet overview shows correct `stories_in_progress` count

### AC-4: Agent Context
- [ ] `/api/agents/{name}/context` returns Teams chat deep link
- [ ] `/api/agents/{name}/context` returns blocker status parsed from recent messages
- [ ] `/api/agents/{name}/context` returns last message sender, timestamp, content

### AC-5: Alerts Pipeline
- [ ] Alerts endpoint returns historical alerts without error
- [ ] At minimum: `stuck_agent` alert fires when health check returns offline for >5 minutes
- [ ] Alert banner in dashboard shows correct active alert count

---

## Out of Scope (v1)

- Frontend component changes (already tested and working)
- Azure Cost Management API integration (Loki-based SDK cost is sufficient for now)
- Real-time WebSocket updates (30s polling is acceptable)
- Multi-tenant auth (single API key is fine for internal tool)
- Historical cost backfill (start tracking from fix date forward)

---

## Dependencies

- **BLOCKER: Derrick's Claude Code API key must be refreshed** before he can work on this or generate any SDK cost data to test against
- Dan's VM SSH key needs verification (couldn't connect from this session)
- Promtail must be installed/running on agent VMs (infra prereq Mark may need to handle)

---

## Key Assumptions

- Loki is accessible and accepting pushes from agent VMs (needs verification)
- Monday.com board has story/phase data for active agents (board ID: 18405631030)
- Teams Graph API permissions allow reading chat messages (was working for STORY-016)
- Frontend components will correctly render data once API returns it (covered by existing 59 tests)

---

## Technical Notes

### Files to Fix (MCP Client — TypeScript)
- `tools/agent-ops-mcp/src/client.ts` — Unwrap `{agents: [...]}` and `{alerts: [...]}` response envelopes
- `tools/agent-ops-mcp/src/client.ts` — Fix `read_messages` to not pass `count` kwarg (use correct param name)

### Files to Fix (Backend — Python)
- `tech_dev_agents/ops_console/services/loki_client.py` — Fix `[DONE]` line regex to be field-order agnostic
- `tech_dev_agents/ops_console/services/cost_service.py` — Verify aggregation logic with real Loki data
- `tech_dev_agents/ops_console/services/monday_service.py` — Wire up current story/phase queries
- `tech_dev_agents/ops_console/routes/agents.py` — Populate `cost_7d`, `cost_30d`, `recent_activity`
- `tech_dev_agents/ops_console/routes/context.py` — Populate Teams link, blocker status, last message
- `tech_dev_agents/ops_console/services/teams_client.py` — Fix `read_messages()` signature

### Files to Fix (Infra)
- Deploy cost_collector cron on agent VMs
- Verify/fix Promtail config and systemd service on both VMs

---

## Real Data Samples

### Actual [DONE] log line format (from Derrick's VM):
```
[Claude Code] [DONE] turns=1 tools=0 cost=$? duration=0s stop=stop_sequence error=True
```
Note: `cost=$?` when auth fails. Valid sessions should produce `cost=$X.XX`.

### Expected [COST_SUMMARY] format (from cost_collector.py):
```
[COST_SUMMARY] agent=dan date=2026-04-07 sdk_sessions=14 sdk_cost=$16.59 sdk_turns=419
```

### Current API response for agent detail (all zeros):
```json
{
  "name": "derrick",
  "status": "online",
  "cost_today": {"sdk_cost_usd": 0.0, "azure_cost_usd": 0.0, "total_cost_usd": 0.0}
}
```
