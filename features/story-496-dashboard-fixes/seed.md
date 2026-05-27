# STORY-496: Dashboard Fix — 6 Critical Issues from STORY-480 Review

## Problem Statement

STORY-480 (Dashboard Overhaul) was completed and merged but the implementation has 6 critical issues identified during Mark's review on 2026-04-21. The dashboard is not usable for fleet management in its current state.

## Issues to Fix

### 1. Missing quota visibility per agent
**Expected:** Each agent card shows a progress bar or percentage of their 5-hour Claude Code billing window consumption with time to reset.
**Actual:** No quota data visible anywhere on the dashboard.
**Fix:** Add a `/api/agents/{name}/quota` endpoint that runs `quota_check.py` via SSH on the agent VM. Display in each agent card as a progress bar with percentage and "resets in Xm" text. Color: green <50%, yellow 50-80%, red >80%.

### 2. Cost display unclear
**Expected:** Each agent card shows Azure Foundry spend only (the real invoice cost). SDK cost is irrelevant (flat-rate seat).
**Actual:** Cards show a cost number but it's unclear whether it's Foundry-only or mixed.
**Fix:** Label the cost explicitly as "Foundry: $X.XX" in each card. Remove any SDK cost from the visible display. The FleetOverviewBar daily/monthly spend should also be labeled "Azure Foundry Spend."

### 3. Presence/status is wrong
**Expected:** 
- Dan/Derrick: "Rate Limited — resets Apr 23, 7pm UTC" (they're out of Claude Code capacity)
- Daisy/Devon: "Working — STORY-XXX Phase N" (actively running stories)
- Morris: "Idle" or "Reviewing PRs" based on his actual activity
**Actual:** All agents show the same status regardless of actual state. Dan/Derrick show as online when they're rate-limited and can't do work.
**Fix:** Parse the dispatch poller logs from Loki for actual state:
- `[DISPATCH] busy, skipping` → Working
- `[DISPATCH] queue empty` → Idle  
- `[DISPATCH] RATE LIMITED` or `dispatch-poller disabled/masked` → Rate Limited
- No recent logs → Offline
- For rate-limited agents, show the reset time from `/var/run/dispatch-poller-paused-until` or from the `ccusage` rate limit message.
- Push Teams presence to match: DND for rate-limited, Busy for working, Available for idle.

### 4. FleetOverviewBar shows all zeros
**Expected:** Accurate counts of active agents, busy agents, stories in progress, queued stories.
**Actual:** Shows 0/0 active, 0 busy, 0 active stories, 0 queued.
**Fix:** 
- Active agents = count of agents where status is Working or Idle (poller running, not rate-limited)
- Busy agents = count where SDK is actively running (status = Working)
- Active stories = count of claimed items in dispatch queue
- Queued stories = count of pending items in dispatch queue
- The data source should be the `/api/fleet` endpoint which aggregates from Loki + dispatch queue API.

### 5. Queue display needs "In Review" status
**Expected:** Clear distinction between: Pending (waiting for agent), In Progress (agent working), In Review (PR created, waiting for Morris/Mark).
**Actual:** Queue shows claimed stories as "claimed" which is confusing — looks like the agent is working on multiple tickets when really the work is done and pending review.
**Fix:** Add a new dispatch status: `in_review`. The phase runner should transition stories to `in_review` after creating a PR. The queue display shows three tabs: Pending | In Progress | In Review.

### 6. "Busy (SDK active/waiting)" label is meaningless
**Expected:** Show what the agent is actually working on: "STORY-447 Phase 8 (Implementation) — 12m"
**Actual:** Every agent shows "Busy (SDK active/waiting)" regardless of what they're doing.
**Fix:** Replace with parsed data from Loki `[DISPATCH]` logs:
- Extract story ID and phase from `[DISPATCH] Phase N (PhaseName) for STORY-XXX — starting SDK`
- Calculate duration from the timestamp of that log line
- Display: "STORY-447 Phase 8 (Impl) — 12m" or "Idle — last completed STORY-400 (5m ago)"

## Scope Classification

**Medium** — Backend API changes + frontend component updates. No database schema changes except adding `in_review` status to the dispatch_items status enum.

## Acceptance Criteria

- [ ] Each agent card shows quota progress bar with % used and time to reset
- [ ] Cost labeled explicitly as "Foundry: $X.XX"
- [ ] Rate-limited agents show "Rate Limited — resets [date/time]" instead of "online"
- [ ] Working agents show "Working — STORY-XXX Phase N"
- [ ] FleetOverviewBar shows accurate counts (not zeros)
- [ ] Queue has three sections: Pending / In Progress / In Review
- [ ] Agent work detail shows story ID + phase + duration, not "Busy (SDK active/waiting)"
- [ ] Teams presence matches dashboard status

## Technical Notes

### Key files to modify:
- `tech_dev_agents/ops_console/routes/agents.py` — agent status + quota endpoint
- `tech_dev_agents/ops_console/routes/fleet.py` — fleet overview aggregation
- `tech_dev_agents/ops_console/services/loki_client.py` — dispatch log parsing
- `tech_dev_agents/ops_console/services/agent_service.py` — status classification
- `tech_dev_agents/ops_console/models/responses.py` — QuotaInfo model, status enum
- `frontend/src/components/AgentCard.tsx` — quota bar, status display, work detail
- `frontend/src/components/FleetOverviewBar.tsx` — accurate counts
- `frontend/src/components/StatusBadge.tsx` — new states (rate-limited, working)
- `frontend/src/components/DispatchQueue.tsx` — three-tab layout

### Data sources:
- Quota: `quota_check.py` on each agent VM (already deployed at `/opt/agent/quota_check.py`)
- Status: Loki `{job="hermes-gateway"} |~ "[DISPATCH]"` with agent label
- Queue: `/api/dispatch/queue` (already exists)
- Cost: `CostService.get_today_cost()` (already exists, just needs proper labeling)

### FEATURE FLAGS:
- Not needed — this is fixing existing dashboard features, not adding new endpoints

## Out of Scope

- CI/CD pipeline for deployment (STORY-495)
- Dispatch claim sync fixes (STORY-494 — already completed)
- Morris's fleet-vigilance updates

## Recommended Next Phase

**Phase 7 (Test Design)** — skip Phase 4/6, the analysis and design are in this seed.
