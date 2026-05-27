# STORY-480: Dashboard Overhaul — Foundry Costs, Quota %, Presence, Work Detail

## Problem Statement

The tech-dev-agents ops dashboard is unreliable for fleet management. Cost widgets mix Azure Foundry (real invoice cost) with Claude Code SDK (flat-rate seat). There's no visibility into Claude Code quota consumption — Mark was blindsided when agents burned their entire weekly token allotment over a weekend. Agent presence shows incorrect busy/available state. The "working on" field displays raw dispatch prompts ("CONTEXT FROM DISPATCH") instead of actionable info.

## Target User

Mark Oreta (engineering manager) — needs a single glance to answer: Are agents working? What are they on? How much is Foundry costing? Are they about to hit their Claude Code quota?

## Scope Classification

**Medium** — Crosses frontend + backend + external data source (ccusage via SSH). No DB changes. 4 distinct fixes touching ~8 files.

## Acceptance Criteria

### AC-1: Cost widgets show Azure Foundry spend only
- [ ] FleetOverviewBar "Daily Spend" and "Monthly Spend" show Azure Foundry cost only (already the case in `get_fleet_daily_spend()` / `get_fleet_monthly_spend()` — verify and fix if not)
- [ ] AgentCard headline cost shows `today_foundry_usd` only (remove SDK cost from visible display)
- [ ] AgentCard tooltip breakdown removes SDK line — show only Foundry + OpenAI
- [ ] CostChart on AgentDetailView shows Foundry cost only
- [ ] Cost breakdown API (`GET /agents/{name}/cost`) returns Foundry cost as primary, SDK cost removed from response or hidden

### AC-2: Claude Code 5-hour quota % per agent
- [ ] New backend endpoint: `GET /api/agents/{name}/quota` returns current 5-hour block usage from `ccusage blocks --json`
- [ ] Backend SSHs to agent VM, runs `ccusage blocks --json`, parses the active block's token count
- [ ] Response includes: `total_tokens`, `block_start`, `block_end`, `is_active`, `cost_usd`, `pct_used` (percentage of estimated 5-hour limit)
- [ ] AgentCard displays a progress bar or percentage showing quota consumption for the current 5-hour window
- [ ] FleetOverviewBar shows an aggregate quota indicator (e.g., "Quota: 3/4 agents under 50%")
- [ ] Color coding: green (<50%), yellow (50-80%), red (>80%)
- [ ] Cache the SSH result for 5 minutes (don't SSH every 10 seconds)

### AC-3: Fix agent presence/status
- [ ] Status reflects actual agent state from dispatch poller logs, not just "last Loki activity"
- [ ] States: **Working** (SDK process running + story ID + phase), **Idle** (poller running, no SDK), **Rate Limited** (pause flag exists), **Offline** (no recent logs)
- [ ] Parse `[DISPATCH] Phase N (PhaseName) for STORY-XXX — starting SDK` from Loki for active work
- [ ] Parse `[DISPATCH] PAUSED — Claude Code rate limit, resets X` from Loki for rate-limited state
- [ ] Parse `[DISPATCH] queue empty` for idle state
- [ ] StatusBadge updated: green=Working, blue=Idle, amber=Rate Limited, gray=Offline
- [ ] Remove "Busy (SDK active/waiting)" / "Not busy" text — replace with actual state

### AC-4: Meaningful agent work detail
- [ ] Current work shows: `STORY-XXX Phase N (PhaseName)` — e.g., "STORY-450 Phase 7 (Test Design)"
- [ ] Parse from Loki `[DISPATCH]` log lines — the phase runner logs structured data
- [ ] Show duration: "for 12m" based on time since phase start
- [ ] If idle: show "Idle — last completed STORY-XXX (Nm ago)"
- [ ] If rate limited: show "Rate limited — resets Xpm"
- [ ] Remove "CONTEXT FROM DISPATCH" from display — this is the raw prompt, not user-facing info
- [ ] AgentCard truncates story title sensibly (not raw prompt text)

## Technical Notes

### Data sources for each fix:

**Costs (AC-1):**
- Backend already separates Foundry vs SDK in `CostService`
- `get_fleet_daily_spend()` and `get_fleet_monthly_spend()` already return Azure-only
- Frontend `AgentCard.tsx` line 77 already shows `today_foundry_usd` — verify tooltip and chart
- May need to remove `today_sdk_usd` and `today_total_usd` from `AgentSummary` response or hide in UI

**Quota (AC-2):**
- `ccusage blocks --json` returns array of 5-hour blocks with `totalTokens`, `isActive`, `startTime`, `endTime`, `costUSD`
- SSH command: `ssh -p 443 azureagent@<IP> "sudo -u hermes ccusage blocks --json"`
- Agent IPs in `deployment/vm/agent-registry.json`
- Estimated 5-hour limit is unknown (Anthropic doesn't publish it) — use P90 from historical data or allow manual config
- Cache aggressively (5 min TTL) — SSH to 4 agents every 10s would overload

**Presence (AC-3):**
- Dispatch poller logs to journal → promtail → Loki with `agent=<name>` label
- Key log patterns to parse:
  - `[DISPATCH] Phase N (PhaseName) for STORY-XXX — starting SDK` → Working
  - `[DISPATCH] Phase N (PhaseName) ✓ for STORY-XXX` → Phase complete
  - `[DISPATCH] queue empty` → Idle
  - `[DISPATCH] PAUSED — Claude Code rate limit, resets X` → Rate Limited
  - `[DISPATCH] STORY-XXX COMPLETE in Ns` → Just finished
  - `[DISPATCH] STORY-XXX FAILED` → Error state
- Loki query: `{job="claude-code", agent="<name>"} |~ "\\[DISPATCH\\]"` last 30 min

**Work detail (AC-4):**
- Same Loki source as presence — parse the `[DISPATCH]` lines
- Replace `_resolve_current_work()` in `agents.py` to use dispatch log parsing instead of Monday.com / `[Agent Guidance]`
- The phase runner now logs: `[DISPATCH] Phase N (PhaseName) for STORY-XXX — starting SDK` and `[DISPATCH] Phase N (PhaseName) ✓ for STORY-XXX`

### Files to modify:

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/services/loki_client.py` | New methods: `get_dispatch_status()`, `get_current_phase()` |
| `tech_dev_agents/ops_console/services/agent_service.py` | New: `get_quota()` via SSH + ccusage, updated status classification |
| `tech_dev_agents/ops_console/routes/agents.py` | New endpoint `/agents/{name}/quota`, update `_resolve_current_work()` |
| `tech_dev_agents/ops_console/routes/fleet.py` | Add quota summary to fleet response |
| `tech_dev_agents/ops_console/models/responses.py` | New: `QuotaInfo`, updated `AgentSummary` |
| `frontend/src/components/AgentCard.tsx` | Quota bar, updated status display, work detail |
| `frontend/src/components/FleetOverviewBar.tsx` | Replace cost widgets, add quota indicator |
| `frontend/src/components/StatusBadge.tsx` | New states: Working (green), Idle (blue), Rate Limited (amber) |
| `frontend/src/types/api.ts` | Updated types for quota and status |
| `frontend/src/hooks/useAgents.ts` | Potentially new quota hook |

### Key constraints:
- SSH to agent VMs requires the ops-console VM to have SSH access (key authorized, port 443)
- ccusage takes ~3-5 seconds to run (parses JSONL files) — must cache results
- The 5-hour quota limit number is not known — use configurable threshold or P90 auto-detection
- Morris (manager) doesn't have a dispatch poller — skip quota widget for him
- Don't break existing MCP tool access to the API (uses API key auth, not MSAL)

## Out of Scope

- Rebuilding the dashboard framework or layout
- Changing the dispatch queue API or phase runner
- Adding new pages or views — fix existing components
- Morris-specific manager dashboard features
- Historical quota trend charts (future story)

## Dependencies

- `ccusage` v18.0.11 installed on all agent VMs (done)
- Promtail shipping `[DISPATCH]` logs to Loki with `agent=<name>` label (done)
- Agent registry at `deployment/vm/agent-registry.json` with all 5 agents (done)
- Ops-console VM SSH access to agent VMs on port 443 (needs verification)

## Recommended Next Phase

**Phase 4 (Analysis)** — Medium scope. Need to trace exact Loki query patterns, verify SSH from ops-console to agents, and map the full data flow for quota integration before designing.
