# Seed: Azure Foundry Cost Tracking + Agent Status Fix

**Story:** STORY-024
**Date:** 2026-04-08
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Assignee:** Derrick

---

## Problem Statement

Two dashboard issues:

### 1. Wrong cost source
The dashboard shows Claude Code SDK costs (`[DONE] cost=$X` from session logs). Mark doesn't care about these — they're covered by the Team subscription. He needs **Azure AI Foundry costs** — what the Hermes gateway spends on Sonnet via Azure OpenAI. This is the real operational cost.

### 2. Agents always show offline
The ops console checks `http://{agent_ip}:8080/health` but agents don't have an HTTP health server. They only have the Hermes gateway (Teams bot) and the Claude Code SDK tool. Status should be derived from **recent Loki log activity** — if an agent has log entries in the last 5 minutes, it's online.

## Acceptance Criteria

| ID | Criterion | Measurable |
|----|-----------|------------|
| AC-1 | Fleet daily spend shows Azure Foundry cost, not Claude Code SDK cost | `/api/fleet` `total_daily_spend_usd` comes from Azure Cost Management API |
| AC-2 | Fleet monthly spend shows Azure Foundry cost | `/api/fleet` `total_monthly_spend_usd` comes from Azure Cost Management API |
| AC-3 | Per-agent cost breakdown includes Azure Foundry | `/api/agents/{name}` `cost_today`, `cost_7d`, `cost_30d` from Azure Cost Management |
| AC-4 | Agent status is online when Loki shows recent activity | Agent with log entries in last 5 min shows as "online", not "offline" |
| AC-5 | Agent status is idle when no recent activity but gateway was seen | Agent with log entries in last 30 min but not last 5 min shows "idle" |
| AC-6 | Agent status is offline when no Loki activity for 30+ min | No recent logs → offline |

## Out of Scope
- Claude Code SDK cost tracking (keep it, just don't show as primary)
- CostChart historical data migration
- Azure VM compute costs (just AI Foundry model usage)

## Technical Notes

### Azure Cost Management API
The `azure_cost_client.py` already exists in `tech_dev_agents/ops_console/services/` with client credentials auth. Needs:
- `OPS_AZURE_TENANT_ID`, `OPS_AZURE_CLIENT_ID`, `OPS_AZURE_CLIENT_SECRET`, `OPS_AZURE_SUBSCRIPTION_ID` in the ops console `.env`
- Query: filter by `meterCategory eq 'Azure AI Foundry'` or similar, group by resource group per agent
- Agent-to-resource-group mapping: `azure_agent_map` in config.py (already exists: `{"rg-agent-dan": "dan", "rg-agent-derrick": "derrick"}`)

### Agent Status via Loki
Replace the HTTP health check in `agent_service.py` with a Loki query:
```
{agent="dan"} | last 5m → online
{agent="dan"} | last 30m → idle  
no results → offline
```
The `loki_client.py` already has query methods. Just need to add a `get_last_activity(agent_name)` method.

### Files to Change
| File | Action |
|------|--------|
| `services/cost_service.py` | Query Azure Cost Management instead of Loki for costs |
| `services/azure_cost_client.py` | Verify/fix the existing client |
| `services/agent_service.py` | Replace HTTP health check with Loki activity check |
| `services/loki_client.py` | Add `get_last_activity(agent)` method |
| `routes/fleet.py` | Use Azure costs for spend fields |
| `routes/agents.py` | Use Azure costs for detail view |
| `config.py` | Verify Azure env vars are loaded |

### Mark TODO (after implementation)
- [ ] Add Azure Cost Management env vars to ops console VM `.env`
- [ ] Verify `azure_agent_map` matches actual resource group names
