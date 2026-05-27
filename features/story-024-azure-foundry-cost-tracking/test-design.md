# Test Design: STORY-024 Azure Foundry Cost Tracking + Agent Status Fix

**Story:** STORY-024
**Phase:** 7 (Test Design)
**Date:** 2026-04-08
**Scope:** Small

---

## Test Matrix

### Fix 1: Azure Cost as Primary Source (AC-1, AC-2, AC-3)

| ID | Test | Target | Validates |
|----|------|--------|-----------|
| T46 | Fleet daily spend uses Azure cost only | `cost_service.get_fleet_daily_spend` | AC-1 |
| T47 | Fleet monthly spend uses Azure cost only | `cost_service.get_fleet_monthly_spend` | AC-2 |
| T48 | Fleet endpoint `today_cost_usd` per agent is Azure-only | `routes/fleet.py` | AC-1 |
| T49 | Agent detail `cost_7d`/`cost_30d` use Azure-only totals | `routes/agents.py` `_safe_cost_total` | AC-3 |

### Fix 2: Loki-Based Agent Status (AC-4, AC-5, AC-6)

| ID | Test | Target | Validates |
|----|------|--------|-----------|
| T50 | `get_last_activity` returns ISO timestamp from most recent Loki entry | `loki_client.get_last_activity` | Foundation |
| T51 | `get_last_activity` returns None when Loki has no entries | `loki_client.get_last_activity` | AC-6 |
| T52 | Agent health poll uses Loki activity, not HTTP | `agent_service._poll_agent_health` | AC-4 |
| T53 | Agent with recent Loki activity (< 5 min) shows ONLINE | `agent_service.get_all_health` | AC-4 |
| T54 | Agent with old Loki activity (5-30 min) shows IDLE | `agent_service.get_all_health` | AC-5 |
| T55 | Agent with no Loki activity shows OFFLINE | `agent_service.get_all_health` | AC-6 |
| T56 | Loki failure in health poll degrades gracefully to OFFLINE | `agent_service._poll_agent_health` | Resilience |

---

## RED State

All 11 tests written to fail against current implementation. Implementation in Phase 8 will make them GREEN.
