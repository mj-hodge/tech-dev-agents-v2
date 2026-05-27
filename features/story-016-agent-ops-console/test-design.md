# Test Design: Agent Operations Console

> Phase 7 — Test Design
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large
> State: RED (tests designed, not yet implemented)

---

## Table of Contents

1. [Test Strategy](#test-strategy)
2. [Success Criteria Traceability](#success-criteria-traceability)
3. [Backend Unit Tests — Service Layer](#backend-unit-tests--service-layer)
4. [Backend Unit Tests — API Endpoints](#backend-unit-tests--api-endpoints)
5. [Backend Unit Tests — Auth & Middleware](#backend-unit-tests--auth--middleware)
6. [Backend Unit Tests — External Clients](#backend-unit-tests--external-clients)
7. [Backend Unit Tests — Caching](#backend-unit-tests--caching)
8. [Integration Tests — Service-to-Module](#integration-tests--service-to-module)
9. [Security Tests](#security-tests)
10. [Test Fixtures (conftest.py)](#test-fixtures)

---

## 1. Test Strategy

### Approach

- **Unit tests:** Each service class and route module tested in isolation with mocked dependencies
- **Integration tests:** Service classes tested against real (but test-configured) existing module functions
- **Security tests:** Auth enforcement, rate limiting, input sanitisation, LogQL injection prevention
- **No E2E/browser tests for MVP.** Frontend is React+TS with typed API client; manual QA suffices for Phase 8

### Tech Stack

- **pytest** with `pytest-asyncio` for async tests
- **httpx** `AsyncClient` with FastAPI `TestClient` for route tests
- **unittest.mock** (`AsyncMock`, `MagicMock`, `patch`) for dependency isolation
- **freezegun** for time-dependent cache tests

### File Layout

```
ops-console/backend/tests/
├── conftest.py                  # Shared fixtures
├── test_agent_service.py        # T01–T08
├── test_cost_service.py         # T09–T14
├── test_alert_service.py        # T15–T18
├── test_monday_service.py       # T19–T21
├── test_loki_client.py          # T22–T27
├── test_azure_cost_client.py    # T28–T32
├── test_routes_agents.py        # T33–T42
├── test_routes_fleet.py         # T43–T45
├── test_routes_alerts.py        # T46–T48
├── test_routes_health.py        # T49–T50
├── test_auth.py                 # T51–T55
├── test_cache.py                # T56–T59
├── test_integration.py          # T60–T65
└── test_security.py             # T66–T72
```

### Conventions

- Test IDs: `T01`–`T72` in docstrings (e.g., `"""T01: Fan-out health poll returns..."""`)
- Test classes group related functionality within each file
- Helper functions prefixed with `_make_*` for test data construction
- All async tests use `@pytest.mark.asyncio`

---

## 2. Success Criteria Traceability

| SC | Criterion | Test IDs | Coverage |
|----|-----------|----------|----------|
| SC-1 | Web UI accessible with auth | T49, T50, T51, T52, T53, T54, T55, T66 | Health endpoint, API key validation, missing/invalid key rejection |
| SC-2 | Bot registry with live status (≤60s) | T01, T02, T03, T04, T33, T34, T35, T60 | Health fan-out, status mapping, cache TTL, agent list endpoint |
| SC-3 | Per-agent cost tracking (±5% accuracy) | T09, T10, T11, T12, T13, T14, T22, T23, T24, T28, T29, T30, T31, T38, T39, T61 | SDK costs via Loki, Azure costs, daily breakdown, aggregation |
| SC-4 | Activity feed per agent | T19, T20, T21, T40, T41, T62 | Monday.com story data, activity feed endpoint, event filtering |
| SC-5 | Controls: restart, pause | T05, T06, T07, T08, T42, T63, T67, T72 | Restart validation, pause/resume, disabled agent guard, destructive op protection |
| SC-6 | Alert history (≤1min visibility) | T15, T16, T17, T18, T46, T47, T48, T64 | Alert merging, filtering, anomaly detection, alert endpoint |
| SC-7 | Cost anomaly banner (≤15min) | T17, T18, T25, T48 | Active anomaly query, Loki anomaly detection, banner endpoint |
| SC-8 | Fleet overview aggregates | T43, T44, T45, T65 | Health score calculation, spend aggregation, fleet endpoint |

---

## 3. Backend Unit Tests — Service Layer

### 3.1 AgentService (`test_agent_service.py`)

```python
class TestAgentServiceHealth:
    """T01–T04: Health polling and registry."""

    @pytest.mark.asyncio
    async def test_fan_out_health_all_online(self):
        """T01: get_all_health with 2 reachable VMs returns 2 ONLINE snapshots."""

    @pytest.mark.asyncio
    async def test_fan_out_health_one_unreachable(self):
        """T02: get_all_health with 1 unreachable VM returns OFFLINE for that agent, ONLINE for the other."""

    @pytest.mark.asyncio
    async def test_fan_out_health_timeout(self):
        """T03: get_all_health with 5s timeout exceeded marks agent OFFLINE (not exception)."""

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self):
        """T04: get_agent with unknown name raises AgentNotFoundError."""


class TestAgentServiceControls:
    """T05–T08: Restart and pause operations."""

    @pytest.mark.asyncio
    async def test_restart_success(self):
        """T05: restart_agent on ONLINE agent sends POST to VM and returns RestartResult with success=True."""

    @pytest.mark.asyncio
    async def test_restart_disabled_agent_rejected(self):
        """T06: restart_agent on disabled agent raises ValidationError without calling VM."""

    @pytest.mark.asyncio
    async def test_pause_agent_success(self):
        """T07: pause_agent with action='pause' sends POST and returns success."""

    @pytest.mark.asyncio
    async def test_resume_agent_success(self):
        """T08: pause_agent with action='resume' sends POST and returns success."""
```

**Mocking:** `httpx.AsyncClient` responses mocked for VM health/restart/pause calls. Registry loaded from a test JSON fixture.

### 3.2 CostService (`test_cost_service.py`)

```python
class TestCostServiceToday:
    """T09–T11: Today's cost calculation."""

    @pytest.mark.asyncio
    async def test_today_cost_sdk_only(self):
        """T09: get_today_cost with Loki data but no Azure returns SDK cost with azure_cost_usd=0."""

    @pytest.mark.asyncio
    async def test_today_cost_combined(self):
        """T10: get_today_cost with both Loki and Azure data returns combined total."""

    @pytest.mark.asyncio
    async def test_today_cost_loki_error_degrades_gracefully(self):
        """T11: get_today_cost when Loki is unreachable returns zero costs (not exception)."""


class TestCostServiceBreakdown:
    """T12–T14: Historical cost breakdown."""

    @pytest.mark.asyncio
    async def test_cost_breakdown_daily_granularity(self):
        """T12: get_cost_breakdown with days=7, granularity='daily' returns 7 DailyCost entries."""

    @pytest.mark.asyncio
    async def test_cost_breakdown_weekly_granularity(self):
        """T13: get_cost_breakdown with days=14, granularity='weekly' returns 2 aggregated entries."""

    @pytest.mark.asyncio
    async def test_cost_breakdown_azure_disabled(self):
        """T14: get_cost_breakdown with azure=None returns SDK-only costs with azure_cost_usd=0."""
```

**Mocking:** `LokiClient` and `AzureCostClient` methods mocked with canned response data.

### 3.3 AlertService (`test_alert_service.py`)

```python
class TestAlertServiceMerge:
    """T15–T18: Alert merging and filtering."""

    @pytest.mark.asyncio
    async def test_merge_alerts_from_three_sources(self):
        """T15: get_alerts merges cost_dashboard alerts, health alerts, and Loki alerts, sorted by timestamp desc."""

    @pytest.mark.asyncio
    async def test_filter_by_agent_name(self):
        """T16: get_alerts(agent='dan') returns only alerts for agent 'dan'."""

    @pytest.mark.asyncio
    async def test_active_anomalies_returns_only_active(self):
        """T17: get_active_anomalies returns only alerts where active=True and type='cost_anomaly'."""

    @pytest.mark.asyncio
    async def test_filter_by_type_and_since(self):
        """T18: get_alerts(type='agent_offline', since='24h') returns only matching alerts within time window."""
```

**Mocking:** `LokiClient`, `AgentService`, `CostService` injected as mocks returning predetermined alert data.

### 3.4 MondayService (`test_monday_service.py`)

```python
class TestMondayService:
    """T19–T21: Monday.com story data."""

    @pytest.mark.asyncio
    async def test_get_current_story_returns_story_info(self):
        """T19: get_current_story returns StoryInfo with item_id, name, phase, status from Monday.com client."""

    @pytest.mark.asyncio
    async def test_get_current_story_no_active_story(self):
        """T20: get_current_story returns None when agent has no in-progress story."""

    @pytest.mark.asyncio
    async def test_stories_in_progress_counts_across_agents(self):
        """T21: get_stories_in_progress returns correct count across all configured agents."""
```

**Mocking:** `AgentMondayClient.get_story()` mocked (wrapped in `asyncio.to_thread` in service).

---

## 4. Backend Unit Tests — External Clients

### 4.1 LokiClient (`test_loki_client.py`)

```python
class TestLokiClientQueries:
    """T22–T26: Loki HTTP API query client."""

    @pytest.mark.asyncio
    async def test_query_cost_summaries_parses_lines(self):
        """T22: query_cost_summaries returns parsed cost fields from [COST_SUMMARY] log lines."""

    @pytest.mark.asyncio
    async def test_query_done_lines_uses_regex(self):
        """T23: query_done_lines extracts cost from [DONE] lines using cost_collector regex pattern."""

    @pytest.mark.asyncio
    async def test_query_range_handles_empty_result(self):
        """T24: query_range with no matching logs returns empty list (not error)."""

    @pytest.mark.asyncio
    async def test_query_anomalies_parses_cost_anomaly(self):
        """T25: query_anomalies returns structured anomaly data from [COST_ANOMALY] lines."""

    @pytest.mark.asyncio
    async def test_query_range_loki_500_raises_loki_error(self):
        """T26: query_range with Loki returning 500 raises LokiError with status and message."""


class TestLokiClientHealth:
    """T27: Loki reachability check."""

    @pytest.mark.asyncio
    async def test_is_reachable_returns_true_on_200(self):
        """T27: is_reachable returns True when GET /ready returns 200."""
```

**Mocking:** `httpx.AsyncClient` responses with Loki API JSON payloads.

### 4.2 AzureCostClient (`test_azure_cost_client.py`)

```python
class TestAzureCostClient:
    """T28–T32: Azure Cost Management REST client."""

    @pytest.mark.asyncio
    async def test_get_token_acquires_oauth2_token(self):
        """T28: _get_token sends client_credentials grant and returns access_token string."""

    @pytest.mark.asyncio
    async def test_get_token_caches_until_near_expiry(self):
        """T29: _get_token called twice within TTL returns cached token (1 HTTP call total)."""

    @pytest.mark.asyncio
    async def test_get_daily_costs_maps_resource_groups(self):
        """T30: get_daily_costs maps resource group 'rg-agent-dan' to agent name 'dan'."""

    @pytest.mark.asyncio
    async def test_get_daily_costs_handles_empty_response(self):
        """T31: get_daily_costs with no cost data returns empty dict."""

    @pytest.mark.asyncio
    async def test_get_agent_daily_costs_filters_single_agent(self):
        """T32: get_agent_daily_costs returns only DailyCost entries for the requested agent."""
```

**Mocking:** `httpx.AsyncClient` responses with Azure OAuth2 and Cost Management API JSON.

---

## 5. Backend Unit Tests — API Endpoints

### 5.1 Agent Routes (`test_routes_agents.py`)

```python
class TestAgentListEndpoint:
    """T33–T35: GET /api/agents."""

    @pytest.mark.asyncio
    async def test_list_agents_returns_all(self):
        """T33: GET /api/agents returns AgentListResponse with all registered agents."""

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_status(self):
        """T34: GET /api/agents?status=online returns only online agents."""

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_enabled(self):
        """T35: GET /api/agents?enabled=true returns only enabled agents."""


class TestAgentDetailEndpoint:
    """T36–T37: GET /api/agents/{name}."""

    @pytest.mark.asyncio
    async def test_agent_detail_returns_full_response(self):
        """T36: GET /api/agents/dan returns AgentDetailResponse with health, cost, story, activity."""

    @pytest.mark.asyncio
    async def test_agent_detail_not_found(self):
        """T37: GET /api/agents/unknown returns 404 with 'not found in registry' message."""


class TestAgentCostEndpoint:
    """T38–T39: GET /api/agents/{name}/cost."""

    @pytest.mark.asyncio
    async def test_cost_endpoint_default_7_days(self):
        """T38: GET /api/agents/dan/cost returns CostBreakdownResponse with 7 daily entries by default."""

    @pytest.mark.asyncio
    async def test_cost_endpoint_custom_days(self):
        """T39: GET /api/agents/dan/cost?days=30&granularity=daily returns 30 entries."""


class TestAgentActivityEndpoint:
    """T40–T41: GET /api/agents/{name}/activity."""

    @pytest.mark.asyncio
    async def test_activity_feed_default_limit(self):
        """T40: GET /api/agents/dan/activity returns up to 50 events by default."""

    @pytest.mark.asyncio
    async def test_activity_feed_filter_by_type(self):
        """T41: GET /api/agents/dan/activity?type=commit returns only commit events."""


class TestAgentControlEndpoints:
    """T42: POST restart and pause."""

    @pytest.mark.asyncio
    async def test_restart_endpoint_returns_result(self):
        """T42: POST /api/agents/dan/restart with valid body returns RestartResponse with success=True."""
```

**Pattern:** All route tests use `httpx.AsyncClient` with `app=app` (FastAPI TestClient pattern). Services injected via dependency override.

### 5.2 Fleet Route (`test_routes_fleet.py`)

```python
class TestFleetEndpoint:
    """T43–T45: GET /api/fleet."""

    @pytest.mark.asyncio
    async def test_fleet_overview_returns_aggregates(self):
        """T43: GET /api/fleet returns FleetOverviewResponse with correct agent counts and spend."""

    @pytest.mark.asyncio
    async def test_fleet_health_score_calculation(self):
        """T44: Fleet health score with 1 online, 1 stuck out of 2 enabled = (1/2) - (1*0.3/2) = 0.35."""

    @pytest.mark.asyncio
    async def test_fleet_health_score_all_online(self):
        """T45: Fleet health score with all agents online = 1.0."""
```

### 5.3 Alert Route (`test_routes_alerts.py`)

```python
class TestAlertEndpoint:
    """T46–T48: GET /api/alerts."""

    @pytest.mark.asyncio
    async def test_alerts_returns_list(self):
        """T46: GET /api/alerts returns AlertListResponse with merged alerts."""

    @pytest.mark.asyncio
    async def test_alerts_filter_by_agent(self):
        """T47: GET /api/alerts?agent=dan returns only dan's alerts."""

    @pytest.mark.asyncio
    async def test_alerts_active_anomalies_for_banner(self):
        """T48: GET /api/alerts?type=cost_anomaly&active=true returns only active anomalies (for AlertBanner)."""
```

### 5.4 Health Route (`test_routes_health.py`)

```python
class TestHealthEndpoint:
    """T49–T50: GET /api/health."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        """T49: GET /api/health returns 200 with status='ok', version, uptime, agent counts."""

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self):
        """T50: GET /api/health without X-API-Key header returns 200 (public endpoint)."""
```

---

## 6. Backend Unit Tests — Auth & Middleware

### 6.1 Auth (`test_auth.py`)

```python
class TestApiKeyAuth:
    """T51–T55: API key authentication dependency."""

    @pytest.mark.asyncio
    async def test_valid_api_key_passes(self):
        """T51: Request with correct X-API-Key header returns 200."""

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_401(self):
        """T52: Request without X-API-Key header returns 401 with 'Missing API key'."""

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self):
        """T53: Request with wrong X-API-Key returns 401 with 'Invalid API key'."""

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_401(self):
        """T54: Request with empty X-API-Key header returns 401."""

    @pytest.mark.asyncio
    async def test_health_endpoint_exempt_from_auth(self):
        """T55: GET /api/health returns 200 without any API key (exempt from auth)."""
```

---

## 7. Backend Unit Tests — Caching

### 7.1 TTLCache (`test_cache.py`)

```python
class TestTTLCache:
    """T56–T59: In-memory TTL cache behaviour."""

    def test_cache_set_and_get(self):
        """T56: set(key, value) followed by get(key) returns value within TTL."""

    def test_cache_expired_returns_none(self):
        """T57: get(key) returns None after TTL has elapsed (uses freezegun)."""

    def test_cache_overwrite(self):
        """T58: set(key, new_value) overwrites previous value and resets TTL."""

    def test_cache_different_keys_independent(self):
        """T59: Different keys have independent TTLs and values."""
```

---

## 8. Integration Tests — Service-to-Module

### 8.1 Integration Tests (`test_integration.py`)

These tests verify that service classes correctly call existing module functions with real (not mocked) module code but mocked external I/O (HTTP, filesystem).

```python
class TestAgentServiceModuleIntegration:
    """T60: AgentService integrates with agent_dashboard module functions."""

    @pytest.mark.asyncio
    async def test_health_snapshot_uses_build_health_snapshot(self):
        """T60: AgentService.get_all_health calls agent_dashboard.build_health_snapshot
        with real VM response data and returns correctly structured AgentHealthSnapshot."""


class TestCostServiceModuleIntegration:
    """T61: CostService integrates with cost_dashboard and cost_collector modules."""

    @pytest.mark.asyncio
    async def test_cost_aggregation_uses_aggregate_usage(self):
        """T61: CostService.get_cost_breakdown passes parsed Loki data through
        cost_dashboard.aggregate_usage and returns correct period totals."""


class TestMondayServiceModuleIntegration:
    """T62: MondayService integrates with monday_agent module."""

    @pytest.mark.asyncio
    async def test_story_fetch_uses_agent_monday_client(self):
        """T62: MondayService.get_current_story wraps AgentMondayClient.get_story
        in asyncio.to_thread and returns StoryInfo."""


class TestRestartValidationIntegration:
    """T63: Restart validation uses agent_dashboard module functions."""

    @pytest.mark.asyncio
    async def test_restart_uses_validate_restart(self):
        """T63: AgentService.restart_agent calls agent_dashboard.validate_restart
        and rejects restart for disabled agent (real validation logic)."""


class TestAlertServiceModuleIntegration:
    """T64: AlertService integrates with cost_dashboard.evaluate_alerts and agent_dashboard.evaluate_health_alerts."""

    @pytest.mark.asyncio
    async def test_alert_merge_uses_evaluate_functions(self):
        """T64: AlertService.get_alerts calls evaluate_alerts and evaluate_health_alerts
        with real threshold/health logic and merges results correctly."""


class TestFleetHealthScoreIntegration:
    """T65: Fleet overview calculation end-to-end."""

    @pytest.mark.asyncio
    async def test_fleet_health_score_with_mixed_statuses(self):
        """T65: Fleet endpoint computes health score from real AgentService health data:
        2 online + 1 stuck + 1 offline out of 4 enabled = (2/4) - (1*0.3/4) - (1*0.5/4) = 0.30."""
```

---

## 9. Security Tests

### 9.1 Security Tests (`test_security.py`)

```python
class TestAuthEnforcement:
    """T66: All protected endpoints reject unauthenticated requests."""

    @pytest.mark.asyncio
    async def test_all_api_endpoints_require_auth(self):
        """T66: Every /api/* endpoint (except /api/health) returns 401 without X-API-Key.
        Iterates: GET /api/agents, GET /api/agents/dan, GET /api/agents/dan/cost,
        GET /api/agents/dan/activity, POST /api/agents/dan/restart,
        POST /api/agents/dan/pause, GET /api/fleet, GET /api/alerts."""


class TestInputValidation:
    """T67–T69: Request body and query parameter validation."""

    @pytest.mark.asyncio
    async def test_restart_requires_reason(self):
        """T67: POST /api/agents/dan/restart with empty reason returns 422 validation error."""

    @pytest.mark.asyncio
    async def test_pause_invalid_action_rejected(self):
        """T68: POST /api/agents/dan/pause with action='delete' returns 422 (must be pause|resume)."""

    @pytest.mark.asyncio
    async def test_agent_name_path_param_validated(self):
        """T69: GET /api/agents/<script>alert(1)</script> returns 404 (no injection via path)."""


class TestLogQLInjection:
    """T70: LogQL injection prevention (SEC-08)."""

    @pytest.mark.asyncio
    async def test_logql_query_sanitises_agent_name(self):
        """T70: LokiClient.query_cost_summaries with agent_name containing LogQL metacharacters
        (e.g., '} |= "secret"') sanitises the input before building the query string."""


class TestRateLimiting:
    """T71: Rate limiting on destructive endpoints."""

    @pytest.mark.asyncio
    async def test_restart_rate_limited(self):
        """T71: Sending 10 rapid POST /api/agents/dan/restart requests within 1 second
        results in at least one 429 Too Many Requests response."""


class TestDestructiveOperationGuards:
    """T72: Restart/pause guard rails (SEC-12)."""

    @pytest.mark.asyncio
    async def test_restart_disabled_agent_returns_400(self):
        """T72: POST /api/agents/dan/restart when agent is disabled returns 400
        with message indicating agent must be enabled first."""
```

---

## 10. Test Fixtures

### conftest.py Shared Fixtures

```python
"""Shared test fixtures for ops-console backend tests."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
import httpx
from ops_console.main import create_app
from ops_console.config import Settings

# --- Constants ---

TEST_API_KEY = "test-ops-console-key-12345"
TEST_AGENT_REGISTRY = [
    {
        "name": "dan",
        "host": "10.0.1.10",
        "port": 8080,
        "role": "developer",
        "enabled": True,
    },
    {
        "name": "derrick",
        "host": "10.0.1.11",
        "port": 8080,
        "role": "developer",
        "enabled": True,
    },
]

# --- App & Client Fixtures ---

@pytest.fixture
def test_settings() -> Settings:
    """Settings with test values (no real secrets)."""
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path="/tmp/test-registry.json",
        azure_tenant_id=None,
        azure_client_id=None,
        azure_client_secret=None,
        azure_subscription_id=None,
    )


@pytest_asyncio.fixture
async def app(test_settings):
    """FastAPI app instance with test config."""
    application = create_app(settings=test_settings)
    yield application


@pytest_asyncio.fixture
async def client(app):
    """Authenticated httpx AsyncClient for route tests."""
    async with httpx.AsyncClient(app=app, base_url="http://test") as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


@pytest_asyncio.fixture
async def unauthed_client(app):
    """Unauthenticated httpx AsyncClient for auth tests."""
    async with httpx.AsyncClient(app=app, base_url="http://test") as c:
        yield c


# --- Mock Service Fixtures ---

@pytest.fixture
def mock_agent_service():
    """Mocked AgentService with sensible defaults."""
    service = AsyncMock()
    service.get_registry.return_value = _make_agent_records()
    service.get_all_health.return_value = _make_health_snapshots()
    service.get_agent_health.return_value = _make_health_snapshots()[0]
    return service


@pytest.fixture
def mock_cost_service():
    """Mocked CostService with sensible defaults."""
    service = AsyncMock()
    service.get_today_cost.return_value = _make_cost_today()
    service.get_fleet_daily_spend.return_value = 24.50
    return service


@pytest.fixture
def mock_alert_service():
    """Mocked AlertService with sensible defaults."""
    service = AsyncMock()
    service.get_alerts.return_value = _make_alert_list()
    service.get_active_anomalies.return_value = []
    return service


@pytest.fixture
def mock_monday_service():
    """Mocked MondayService with sensible defaults."""
    service = AsyncMock()
    service.get_current_story.return_value = _make_story_info()
    service.get_stories_in_progress.return_value = 3
    return service


@pytest.fixture
def mock_loki_client():
    """Mocked LokiClient."""
    client = AsyncMock()
    client.is_reachable.return_value = True
    return client


@pytest.fixture
def mock_http_client():
    """Mocked httpx.AsyncClient for external HTTP calls."""
    return AsyncMock(spec=httpx.AsyncClient)


# --- Helper Functions ---

def _make_agent_records():
    """Build test AgentRecord list."""
    # Returns 2 agents matching TEST_AGENT_REGISTRY
    ...

def _make_health_snapshots():
    """Build test AgentHealthSnapshot list (both ONLINE)."""
    ...

def _make_cost_today():
    """Build test CostToday (sdk=10.25, azure=2.22, total=12.47)."""
    ...

def _make_story_info():
    """Build test StoryInfo for STORY-016."""
    ...

def _make_alert_list():
    """Build test AlertListResponse with 3 mixed alerts."""
    ...
```

---

## Test Summary

| Category | Tests | IDs |
|----------|-------|-----|
| Service — AgentService | 8 | T01–T08 |
| Service — CostService | 6 | T09–T14 |
| Service — AlertService | 4 | T15–T18 |
| Service — MondayService | 3 | T19–T21 |
| Client — LokiClient | 6 | T22–T27 |
| Client — AzureCostClient | 5 | T28–T32 |
| Routes — Agents | 10 | T33–T42 |
| Routes — Fleet | 3 | T43–T45 |
| Routes — Alerts | 3 | T46–T48 |
| Routes — Health | 2 | T49–T50 |
| Auth & Middleware | 5 | T51–T55 |
| Caching | 4 | T56–T59 |
| Integration | 6 | T60–T65 |
| Security | 7 | T66–T72 |
| **Total** | **72** | **T01–T72** |

All 72 tests are in RED state — designed but not yet implemented. Phase 8 will implement the production code and test code to bring all tests to GREEN.
