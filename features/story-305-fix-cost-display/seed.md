# STORY-305: Fix Cost Display — Agent Cards Show $0.00

## Story Summary

**ID:** STORY-305
**Scope:** Small (hotfix)
**Phase Path:** 1 → 7 → 8 → Done
**Status:** Done (single-commit fix)

## Problem Statement

Agent cards on the ops-console dashboard always displayed `$0.00` for today's cost,
even when Azure Cost Management was returning real billing data.

**Root cause:** `AzureCostClient._parse_cost_response()` stored all Azure costs in
`azure_cost_usd` on the `DailyCost` model. However, the agent card route
(`routes/agents.py`) and the fleet route (`routes/fleet.py`) read `foundry_cost_usd`
from `CostToday`, which had no corresponding field and therefore defaulted to `0.0`.

The field name mismatch silently swallowed real cost data for every agent on every page
load.

## Solution

The fix required changes across three layers:

1. **Data model** (`models/responses.py`): Added `foundry_cost_usd: float = 0.0`
   and `openai_cost_usd: float = 0.0` to `DailyCost` and `CostToday`, keeping
   `azure_cost_usd` for backward compatibility.

2. **Cost classification** (`services/azure_cost_client.py`): Added
   `_classify_cost(rg_name, cost_value) → (foundry, openai)` that splits Azure costs
   by resource group name heuristic: resource groups starting with `oai-` are Azure
   OpenAI Service; all others are Azure AI Foundry (the primary compute layer for
   the fleet).

3. **Aggregation** (`services/cost_service.py`): `get_today_cost()` and
   `get_cost_breakdown()` now sum `foundry_cost_usd` and `openai_cost_usd` fields
   from parsed daily entries into the corresponding `CostToday` fields.

4. **Routes** (`routes/agents.py`, `routes/fleet.py`): Changed `today_cost_usd`
   assignment to read `cost.foundry_cost_usd` instead of `cost.azure_cost_usd`
   or `cost.sdk_cost_usd`.

## Key Files Changed

- `tech_dev_agents/ops_console/models/responses.py`
- `tech_dev_agents/ops_console/services/azure_cost_client.py`
- `tech_dev_agents/ops_console/services/cost_service.py`
- `tech_dev_agents/ops_console/routes/agents.py`
- `tech_dev_agents/ops_console/routes/fleet.py`
- `deployment/ops-console/.env.example`
- `tests/ops_console/test_azure_cost_client.py` (5 new classification tests)
- `tests/ops_console/conftest.py`
- `tests/ops_console/test_routes_agents.py`
- `tests/ops_console/test_story024_azure_cost_status.py`

## Acceptance Criteria

- [x] Agent cards display real Azure Foundry cost instead of `$0.00`
- [x] OpenAI-service resource groups (`oai-*`) classified separately
- [x] `azure_cost_usd` field retained for backward compatibility (existing dashboards unaffected)
- [x] All existing tests updated and passing
- [x] 5 new cost-classification unit tests GREEN

## Discovery

Bug was found during QA testing of STORY-229 (Bot Teams Messaging). The cost display
regression was introduced when STORY-227 (Managed Identity) refactored the credential
path but did not update the field read by the routes.
