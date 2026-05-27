# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Feature Name | Deploy Agent Dashboards & Wiring |
| Story ID | STORY-015 |

## Problem Statement

STORY-012/013/014 built data models, aggregation logic, and business rules as Python modules. But nothing is deployed or running. The operator still has no working dashboard to see agent costs, no Monday.com auto-updates, and no management UI. The code exists but isn't wired into the actual infrastructure.

Specific gaps:
- No Grafana dashboard JSON provisioned — cost and usage data isn't visualized
- No scheduled job collecting cost data from Azure or SDK logs
- Agents don't call the Monday integration code at phase transitions
- No health API endpoint on agent VMs
- No restart API endpoint on agent VMs
- The `cost_dashboard.py`, `monday_agent.py`, and `agent_dashboard.py` modules are untested against real infrastructure

## Target User / Use Case

- **Mark (operator)** — opens Grafana and sees per-agent: cost today, stories completed, health status, last activity
- **Agents (Dan, Derrick)** — automatically update Monday.com when completing phases/stories
- **Mark** — can restart a stuck agent from Grafana or a simple API call without SSH

## Success Criteria

- [ ] SC-1: Grafana dashboard deployed and accessible showing per-agent cost (Azure Foundry + Claude Code SDK), usage (sessions, turns), and health (online/busy/offline)
- [ ] SC-2: Cron job on each agent VM that collects cost data from SDK logs and pushes to Loki in a format the dashboard queries
- [ ] SC-3: Agents update Monday.com at story start (move to In Progress) and story completion (move to Review, add summary comment)
- [ ] SC-4: Health API endpoint on each agent VM (`/api/health`) returning agent status, last activity, uptime, session count
- [ ] SC-5: Restart API endpoint on each agent VM (`/api/restart`) that clears sessions and restarts the gateway
- [ ] SC-6: Grafana dashboard has a link/button per agent that triggers the restart endpoint

## Technical Approach

### 1. Grafana Dashboard (SC-1)
Create a Grafana dashboard JSON and provision it. Panels:
- **Cost per agent per day** — LogQL: parse `[DONE]` lines for `cost=$X`, sum by agent
  ```
  sum by (agent) (
    sum_over_time({job="claude-code"} |~ "DONE.*cost" | regexp "cost=\\$(?P<cost>[\\d.]+)" | unwrap cost [24h])
  )
  ```
- **Sessions per agent** — count `[START]` lines per agent
- **Agent status** — stat panel querying health API endpoints
- **Story activity** — count commits from hermes-gateway logs

### 2. Cost Collection Cron (SC-2)
Script on each VM that runs every 15 min:
- Parses `/tmp/claude-sdlc-logs/session-*.log` for `[DONE] cost=$X` lines
- Aggregates daily totals
- Writes structured log line to `/tmp/hermes-combined.log` for Promtail:
  ```
  [COST_SUMMARY] agent=dan date=2026-03-31 sdk_sessions=14 sdk_cost=$16.59 sdk_turns=419
  ```

### 3. Monday.com Auto-Update (SC-3)
Add to the SDK tool or as a post-phase hook:
- On `[START]`: call Monday API to move story to In Progress
- On `[DONE]`: call Monday API to move story to Review, add comment with summary
- Use the `monday_agent.py` module from STORY-013

### 4. Agent Health + Restart API (SC-4, SC-5)
Extend the existing health server or add a simple Flask/FastAPI app on each VM:
- `GET /api/health` — returns JSON: status, last activity, uptime, session count, errors
- `POST /api/restart` — clears sessions, restarts hermes-gateway, returns new status
- Protected by a simple API key (from .env)

### 5. Deploy via agent-push.sh (SC-6)
Use the existing multi-agent tooling to deploy dashboard, cron, and API to all agents.

## Dependencies
- STORY-012 (cost_dashboard.py) — provides data models
- STORY-013 (monday_agent.py) — provides Monday integration
- STORY-014 (agent_dashboard.py) — provides health models

## Notes
- Use feature branch, open PR — do NOT push to main
- Test against real infrastructure (Dan and Derrick VMs)
- Dashboard must work with existing Promtail → Loki pipeline
