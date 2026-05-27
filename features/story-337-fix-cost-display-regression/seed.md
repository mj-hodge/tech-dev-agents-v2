# STORY-337: Fix Dashboard Cost Display Regression

## Problem
STORY-305 (PR #35) introduced a regression in the ops console dashboard cost display:
1. **P0 (agents route):** The `/api/agents` endpoint was changed to pass a single `today_cost_usd` computed field instead of the 4 required breakdown fields (`today_foundry_usd`, `today_sdk_usd`, `today_openai_usd`, `today_total_usd`), causing Pydantic `ValidationError` on every request. **Status: Already fixed in current codebase.**
2. **P1 (FleetAgentSummary):** The `FleetAgentSummary` model (used by `/api/fleet`) only has `today_cost_usd`, missing the breakdown fields needed by the fleet overview bar to show real Azure costs.
3. **P1 (Frontend labels):** `AgentCard.tsx` needs an "Azure Spend" label on the primary cost display. `FleetOverviewBar.tsx` needs verification that it uses Azure cost fields.

## Scope
**Small** — Model field additions, route parameter updates, frontend label cleanup.

## Classification
Bug fix / Regression repair

## Affected Files
- `ops_console/models/responses.py` — Add breakdown fields to `FleetAgentSummary`
- `ops_console/routes/fleet.py` — Populate new breakdown fields
- `frontend/src/components/AgentCard.tsx` — Add "Azure Spend" label
- `frontend/src/types/api.ts` — Verify types (no change needed, uses `AgentSummary[]`)

## Success Criteria
- `/api/agents` returns valid JSON with `today_foundry_usd` field (already works)
- `/api/fleet` returns agents with `today_foundry_usd`, `today_sdk_usd`, `today_openai_usd` fields
- AgentCard shows "Azure Spend" label on primary cost display
- All existing pytest tests pass

**Frontend:** true

## Test Criteria

- `/api/fleet` returns `today_foundry_usd`, `today_sdk_usd`, `today_openai_usd` on each agent entry
- `AgentCard.tsx` renders "Azure Spend" label on primary cost field
- Existing unit tests pass without modification

## Validation

Run `pytest tests/` — all tests pass. Verify fleet overview bar shows Azure cost breakdown fields. Manual smoke: load dashboard, confirm cost labels are correct.
