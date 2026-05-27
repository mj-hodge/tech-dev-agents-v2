# Seed — STORY-346: Restore Cost Breakdown Fields in Fleet Overview

## Problem
PR 45 was closed due to conflicts with the STORY-305 refactor of fleet.py on main. The cost breakdown fields (`today_foundry_usd`, `today_sdk_usd`, `today_openai_usd`) were removed from `FleetAgentSummary` during that refactor and need to be restored so the fleet overview endpoint returns per-agent cost breakdowns alongside `today_cost_usd`.

## Scope
**Small** — Three fields added to a response model, one route updated to populate them.

## Changes Required
1. **`responses.py`**: Add `today_foundry_usd: float = 0.0`, `today_sdk_usd: float = 0.0`, `today_openai_usd: float = 0.0` to `FleetAgentSummary`.
2. **`fleet.py`**: In `_fetch_agent_data`, extract breakdown fields from the `CostToday` object (already returned by `cost_service.get_today_cost()`) and pass them to the `FleetAgentSummary` constructor.
3. **Tests**: Verify the fleet endpoint returns the new fields with correct values.

## Acceptance Criteria
- `GET /fleet` returns `today_foundry_usd`, `today_sdk_usd`, `today_openai_usd` per agent.
- All existing tests pass.

## Dependencies
- `CostToday` model already has `foundry_cost_usd`, `sdk_cost_usd`, `openai_cost_usd` fields — no upstream changes needed.
