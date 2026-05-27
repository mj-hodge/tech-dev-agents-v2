# Test Design — STORY-346: Restore Cost Breakdown Fields in Fleet Overview

## Test Cases

### T1: Fleet endpoint returns cost breakdown fields
- **Input**: `GET /api/fleet` with mock `CostToday(foundry_cost_usd=2.22, sdk_cost_usd=10.25, openai_cost_usd=0.0)`
- **Expected**: Each agent in `agents[]` has `today_foundry_usd=2.22`, `today_sdk_usd=10.25`, `today_openai_usd=0.0`
- **Location**: `tests/ops_console/test_routes_fleet.py::TestFleetEndpoint::test_fleet_cost_breakdown_fields`

### T2: Cost breakdown defaults to zero on fetch failure
- **Input**: `GET /api/fleet` with `cost_service.get_today_cost` raising an exception
- **Expected**: Each agent has `today_foundry_usd=0.0`, `today_sdk_usd=0.0`, `today_openai_usd=0.0`
- **Location**: `tests/ops_console/test_routes_fleet.py::TestFleetEndpoint::test_fleet_cost_breakdown_defaults_on_error`

## Existing Tests
All existing fleet tests (T43-T45) must continue to pass unchanged.
