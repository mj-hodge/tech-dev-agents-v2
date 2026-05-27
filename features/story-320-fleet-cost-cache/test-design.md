# Test Design — STORY-320: Fleet Cost Cache

## Test Strategy
Unit tests targeting `CostService.get_today_cost()` caching behavior. Uses `unittest.mock.patch` on `time.time` to control TTL expiry without real delays.

## Test Cases

### T320-01: Cache hit returns cached data without calling Azure
- **Setup:** Create `CostService` with mock Loki + Azure clients. Call `get_today_cost("dan")` once (cold cache).
- **Action:** Call `get_today_cost("dan")` a second time.
- **Assert:** Second call returns identical `CostToday` object. Azure `get_agent_daily_costs` called exactly once (not twice).

### T320-02: Cache miss calls Azure (first call populates cache)
- **Setup:** Create `CostService` with mock Loki + Azure clients.
- **Action:** Call `get_today_cost("dan")` once.
- **Assert:** Loki `query_cost_summaries` called once. Azure `get_agent_daily_costs` called once. Result contains expected cost values.

### T320-03: TTL expiry triggers refresh
- **Setup:** Create `CostService` with `cost_cache_ttl=300`. Call `get_today_cost("dan")` to populate cache.
- **Action:** Advance mocked `time.time` by 301 seconds. Call `get_today_cost("dan")` again.
- **Assert:** Azure `get_agent_daily_costs` called twice total (once for initial, once after expiry). Fresh data returned.

### T320-04: Different agents have independent cache entries
- **Setup:** Create `CostService` with mock Loki + Azure.
- **Action:** Call `get_today_cost("dan")`, then `get_today_cost("derrick")`.
- **Assert:** Azure called twice (once per agent). Each returns agent-specific data.

### T320-05: Fleet daily spend uses cached per-agent costs
- **Setup:** Create `CostService`. Call `get_today_cost("dan")` and `get_today_cost("derrick")` to warm per-agent cache.
- **Action:** Call `get_fleet_daily_spend(["dan", "derrick"])`.
- **Assert:** Azure `get_agent_daily_costs` not called again (per-agent cache warm). Fleet result is sum of cached per-agent Azure costs.

## Test File
`tests/ops_console/test_cost_service_cache.py`

## Dependencies
- `pytest`, `pytest-asyncio`
- `unittest.mock.patch`, `AsyncMock`
- `tech_dev_agents.ops_console.services.cost_service.CostService`
- `tech_dev_agents.ops_console.models.responses.CostToday, DailyCost`
