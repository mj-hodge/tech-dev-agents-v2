# Test Design: STORY-627 — Fix ops-console stale Azure SP for Cost Management

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-627 |
| Scope | Small |
| Coverage Target | 50% (critical paths) |
| Test Framework | pytest + pytest-asyncio |
| Mocking | unittest.mock (AsyncMock, MagicMock) — matches existing patterns |
| Frontend | None (backend only) |

## Test Structure

```
tests/ops_console/
├── test_azure_cost_health.py          # Unit: health_check() method (AC-1, AC-4)
├── test_routes_health.py              # Route: /api/health cost_mgmt_reachable (AC-2) — extend existing
└── conftest.py                        # Existing fixtures reused
```

## Acceptance Criteria → Test Mapping

| AC | Test(s) | Level |
|----|---------|-------|
| AC-1 | `test_health_check_auth_failure_returns_auth_false`, `test_health_check_success_returns_ok_true` | Unit |
| AC-2 | `test_health_includes_cost_mgmt_reachable_true`, `test_health_includes_cost_mgmt_reachable_false`, `test_health_stays_200_when_cost_unreachable` | Route |
| AC-3 | (Documentation — no automated test; verified by diff review) | — |
| AC-4 | `test_health_check_auth_failure_*`, `test_health_check_query_failure_*`, `test_health_check_happy_path_*` | Unit |
| AC-5 | `test_get_daily_costs_still_works_after_health_check_added` | Unit (regression) |

## Shared Fixtures

Reuses existing fixtures from `tests/ops_console/conftest.py`:
- `app`, `client`, `unauthed_client` — FastAPI test app + httpx clients
- `mock_agent_service`, `mock_loki_client` — mock services for health endpoint
- `inject_mock_services()` — inject mocks into app.state

New fixtures in `test_azure_cost_health.py`:
- `_make_credential()` — reused from existing `test_azure_cost_client.py` pattern
- `_FakeAccessToken` — mimics `azure.core.credentials.AccessToken`

## Unit Tests: `test_azure_cost_health.py`

### `test_health_check_auth_failure_returns_auth_false`

**Verifies:** AC-1 + AC-4 — When credential.get_token raises ClientAuthenticationError, health_check returns `{"ok": False, "auth": False, "query": False, "error": "AADSTS700016..."}`

**Why this matters:** The whole point of this story — detect stale SP credentials and surface the failure.

**Arrange:**
- Create AzureCostClient with a credential mock that raises Exception("AADSTS700016: Application not found")
- Provide a mock httpx.AsyncClient

**Act:**
- Call `await client.health_check()`

**Assert:**
- `result["ok"]` is False
- `result["auth"]` is False
- `result["query"]` is False
- `result["error"]` contains "AADSTS700016"

### `test_health_check_query_failure_returns_auth_true_query_false`

**Verifies:** AC-4 — When auth succeeds but the Cost Management query returns 4xx, health_check returns `{"ok": False, "auth": True, "query": False, "error": ...}`

**Why this matters:** Distinguishes auth failure from API failure — different remediation paths.

**Arrange:**
- Create AzureCostClient with valid credential mock
- Mock httpx.post to return 403 response

**Act:**
- Call `await client.health_check()`

**Assert:**
- `result["ok"]` is False
- `result["auth"]` is True
- `result["query"]` is False
- `result["error"]` is not None

### `test_health_check_success_returns_ok_true`

**Verifies:** AC-1 + AC-4 — Happy path: both auth and query succeed.

**Arrange:**
- Create AzureCostClient with valid credential mock
- Mock httpx.post to return 200 with valid Cost Management response

**Act:**
- Call `await client.health_check()`

**Assert:**
- `result["ok"]` is True
- `result["auth"]` is True
- `result["query"]` is True
- `result["error"]` is None

### `test_health_check_returns_dict_with_required_keys`

**Verifies:** AC-1 — The return shape always includes ok, auth, query, error keys.

**Arrange:**
- Create AzureCostClient with valid credential mock + successful response

**Act:**
- Call `await client.health_check()`

**Assert:**
- Result is a dict with exactly keys: `ok`, `auth`, `query`, `error`

### `test_health_check_never_raises`

**Verifies:** Error handling — health_check catches all exceptions and returns structured error, never raises.

**Arrange:**
- Create AzureCostClient with credential that raises an unexpected RuntimeError

**Act:**
- Call `await client.health_check()` (should NOT raise)

**Assert:**
- Returns dict with `ok=False`
- No exception propagated

### `test_health_check_logs_warning_on_auth_failure`

**Verifies:** Gate 10 (Error Observability) — auth failures are logged at WARNING level.

**Arrange:**
- Create AzureCostClient with credential that raises auth error
- Capture log output

**Act:**
- Call `await client.health_check()`

**Assert:**
- WARNING log emitted containing the AADSTS error code

### `test_health_check_does_not_leak_credentials_in_error`

**Verifies:** Security — error dict does not contain full client_secret or tenant_id.

**Arrange:**
- Create AzureCostClient with credential that raises error containing secret text

**Act:**
- Call `await client.health_check()`

**Assert:**
- `result["error"]` does not contain "client_secret" or full secret value

### `test_health_check_output_varies_with_credential_state`

**Verifies:** Output-variance gate — two different credential states produce different outputs.

**Arrange:**
- Client A: valid credential + 200 response
- Client B: failing credential (auth error)

**Act:**
- Call health_check on both

**Assert:**
- Result A has `ok=True, auth=True`
- Result B has `ok=False, auth=False`
- Results are meaningfully different

### `test_get_daily_costs_still_works_after_health_check_added`

**Verifies:** AC-5 — Regression guard: existing get_daily_costs behavior unchanged.

**Arrange:**
- Standard AzureCostClient with agent_map + valid credential + mock 200 response

**Act:**
- Call `await client.get_daily_costs("2026-04-01", "2026-04-01")`

**Assert:**
- Returns expected agent-keyed dict with DailyCost entries (same as existing T30)

## Route Tests: extend `test_routes_health.py`

### `test_health_includes_cost_mgmt_reachable_true`

**Verifies:** AC-2 — GET /api/health response JSON includes `cost_mgmt_reachable: true` when cost client is healthy.

**Arrange:**
- Inject mock services including a mock `azure_cost_client` where `health_check()` returns `{"ok": True, ...}`

**Act:**
- `GET /api/health`

**Assert:**
- `resp.status_code == 200`
- `resp.json()["cost_mgmt_reachable"]` is True

### `test_health_includes_cost_mgmt_reachable_false`

**Verifies:** AC-2 — GET /api/health response JSON includes `cost_mgmt_reachable: false` when cost client is unhealthy.

**Arrange:**
- Inject mock `azure_cost_client` where `health_check()` returns `{"ok": False, ...}`

**Act:**
- `GET /api/health`

**Assert:**
- `resp.status_code == 200`
- `resp.json()["cost_mgmt_reachable"]` is False

### `test_health_stays_200_when_cost_unreachable`

**Verifies:** AC-2 — Health endpoint stays HTTP 200 even when cost_mgmt is unreachable (don't break liveness probes).

**Arrange:**
- Inject mock `azure_cost_client` where `health_check()` returns `{"ok": False, ...}`

**Act:**
- `GET /api/health`

**Assert:**
- `resp.status_code == 200` (NOT 503)
- `resp.json()["status"]` is "ok"

### `test_health_cost_mgmt_reachable_false_when_no_cost_client`

**Verifies:** AC-2 edge case — When azure_cost_client is None (Azure not configured), cost_mgmt_reachable defaults to False.

**Arrange:**
- Inject mock services with `azure_cost_client=None`

**Act:**
- `GET /api/health`

**Assert:**
- `resp.status_code == 200`
- `resp.json()["cost_mgmt_reachable"]` is False

### `test_health_cost_mgmt_reachable_false_when_health_check_raises`

**Verifies:** Defensive — If health_check() raises despite being designed not to, the health endpoint still returns 200 with cost_mgmt_reachable=False.

**Arrange:**
- Inject mock `azure_cost_client` where `health_check()` raises RuntimeError

**Act:**
- `GET /api/health`

**Assert:**
- `resp.status_code == 200`
- `resp.json()["cost_mgmt_reachable"]` is False

## Defensive Gate Coverage

| Gate | Covered By |
|------|-----------|
| Gate 1 (Null/None) | `test_health_cost_mgmt_reachable_false_when_no_cost_client` |
| Gate 2a (External API Isolation) | All tests use mocked HTTP — no real Azure calls |
| Gate 2b (Degradation) | `test_health_check_auth_failure_*`, `test_health_check_query_failure_*`, `test_health_check_never_raises` |
| Gate 10 (Error Observability) | `test_health_check_logs_warning_on_auth_failure` |
| Output-Variance | `test_health_check_output_varies_with_credential_state` |

## Checklist

- [x] Every AC has at least one test
- [x] Output-variance test included
- [x] Error observability test included (Gate 10)
- [x] No real Azure calls in tests (Gate 2a)
- [x] Degradation scenarios tested (Gate 2b)
- [x] Null/None boundary tested (no cost client)
- [x] Regression test for existing get_daily_costs (AC-5)
- [x] All tests designed for RED state (health_check method not yet implemented)
- [x] Test names follow `test_[action]_[condition]_[expected_result]` convention
- [x] Junior-readable AAA structure
