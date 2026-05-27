# STORY-337: Test Design

## Test Strategy
Extend existing fleet endpoint tests to verify cost breakdown fields are present in the `/api/fleet` response.

## Test Cases

### T337-01: Fleet endpoint returns cost breakdown fields
**Location:** `tests/ops_console/test_routes_fleet.py`
**Type:** Integration (HTTP route test)
**Description:** Verify that `GET /api/fleet` response agents contain `today_foundry_usd`, `today_sdk_usd`, and `today_openai_usd` fields alongside the existing `today_cost_usd`.
**Expected:** Each agent in `data["agents"]` has all 4 cost fields with correct values from mock cost service (foundry=2.22, sdk=10.25, openai=0.0, total via today_cost_usd=2.22).

### T337-02: Fleet endpoint cost breakdown defaults to zero on failure
**Type:** Integration
**Description:** When cost_service.get_today_cost raises an exception, all breakdown fields should default to 0.0.
**Expected:** Agent cost fields are all 0.0 when cost fetch fails.

### Manual Verification
- `curl http://localhost:8642/api/agents` returns valid JSON with `today_foundry_usd`
- `curl http://localhost:8642/api/fleet` returns `today_foundry_usd` per agent
- AgentCard UI shows "Azure Spend" label
