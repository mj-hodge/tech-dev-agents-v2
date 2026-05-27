"""Shared test fixtures for ops-console backend tests."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.agent_dashboard import (
    AgentHealthSnapshot,
    AgentRecord,
    DashboardAlert,
    RestartResult,
    build_agent_record,
    build_health_snapshot,
    build_restart_result,
)
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app
from tech_dev_agents.ops_console.models.responses import (
    AgentStatusEnum,
    AlertItem,
    AlertListResponse,
    CostToday,
    StoryInfo,
)

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


@pytest.fixture
def registry_file(tmp_path):
    """Write test registry to a temp JSON file and return the path."""
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps(TEST_AGENT_REGISTRY))
    return str(path)


@pytest.fixture
def test_settings(registry_file, tmp_path) -> Settings:
    """Settings with test values (no real secrets)."""
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=registry_file,
        azure_subscription_id=None,
        database_url="",  # Empty = skip DB, use JSON fallback
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dashboard_overhaul_enabled=True,  # STORY-480: enable filters in tests
        dispatch_pause_enabled=True,  # STORY-507: enable pause endpoint in tests
        grafana_webhook_enabled=True,  # STORY-508: enable Grafana webhook in tests
        dispatch_priority_enabled=True,  # STORY-508: enable priority API in tests
        OPS_DISPATCH_NEEDS_INFO_ENABLED=True,  # STORY-532: enable needs_info endpoint in tests (alias required)
    )


@pytest_asyncio.fixture
async def app(test_settings):
    """FastAPI app instance with test config."""
    application = create_app(settings=test_settings)
    yield application


@pytest_asyncio.fixture
async def client(app):
    """Authenticated httpx AsyncClient for route tests."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


@pytest_asyncio.fixture
async def unauthed_client(app):
    """Unauthenticated httpx AsyncClient for auth tests."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# --- Mock Service Fixtures ---


@pytest.fixture
def mock_agent_service():
    """Mocked AgentService with sensible defaults.

    get_registry and get_agent are synchronous; other methods are async.
    """
    service = MagicMock()
    service.get_registry.return_value = _make_agent_records()
    service.get_agent.return_value = _make_agent_records()[0]

    # Async methods
    service.get_all_health = AsyncMock(return_value=_make_health_snapshots())
    service.get_agent_health = AsyncMock(return_value=_make_health_snapshots()[0])
    service.restart_agent = AsyncMock(return_value=_make_restart_result())
    service.pause_agent = AsyncMock(return_value={
        "agent_name": "dan",
        "action": "pause",
        "success": True,
        "message": "Agent paused successfully",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
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
    service.get_activity_events.return_value = []
    return service


@pytest.fixture
def mock_loki_client():
    """Mocked LokiClient."""
    client = AsyncMock()
    client.is_reachable.return_value = True
    client.query_range.return_value = []
    client.query_cost_summaries.return_value = []
    client.query_done_lines.return_value = []
    client.query_anomalies.return_value = []
    client.query_sdk_health.return_value = []
    client.query_terminal_guard.return_value = []
    client.get_agent_queue.return_value = None
    return client


@pytest.fixture
def mock_http_client():
    """Mocked httpx.AsyncClient for external HTTP calls."""
    return AsyncMock(spec=httpx.AsyncClient)


# --- Helper Functions ---


def _make_agent_records() -> list[AgentRecord]:
    """Build test AgentRecord list."""
    return [
        build_agent_record(
            name="dan", host="10.0.1.10", port=8080, role="developer", enabled=True
        ),
        build_agent_record(
            name="derrick",
            host="10.0.1.11",
            port=8080,
            role="developer",
            enabled=True,
        ),
    ]


def _make_health_snapshots() -> list[AgentHealthSnapshot]:
    """Build test AgentHealthSnapshot list (both ONLINE)."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        build_health_snapshot(
            agent_name="dan",
            last_activity=now,
            uptime_seconds=86400,
            active_sessions=1,
            error_count=0,
        ),
        build_health_snapshot(
            agent_name="derrick",
            last_activity=now,
            uptime_seconds=43200,
            active_sessions=1,
            error_count=0,
        ),
    ]


def _make_cost_today() -> CostToday:
    """Build test CostToday (sdk=10.25, azure=2.22, total=12.47)."""
    return CostToday(
        sdk_cost_usd=10.25,
        azure_cost_usd=2.22,
        foundry_cost_usd=2.22,  # STORY-038: Foundry = primary Azure cost
        openai_cost_usd=0.0,  # STORY-038: OpenAI separate
        total_cost_usd=12.47,
        sdk_sessions=8,
        sdk_turns=142,
    )


def _make_story_info() -> StoryInfo:
    """Build test StoryInfo for STORY-016."""
    return StoryInfo(
        item_id=12345,
        name="STORY-016: Agent Operations Console",
        phase="Phase 8: Implementation",
        status="In Progress",
        group="In Progress",
    )


def _make_restart_result() -> RestartResult:
    """Build test RestartResult."""
    return build_restart_result(
        agent_name="dan",
        success=True,
        message="Restart initiated successfully",
        previous_status=AgentActivityStatus.ONLINE,
        new_status=AgentActivityStatus.ONLINE,
    )


def _make_alert_list() -> AlertListResponse:
    """Build test AlertListResponse with 3 mixed alerts."""
    now = datetime.now(timezone.utc).isoformat()
    return AlertListResponse(
        alerts=[
            AlertItem(
                id="alert_001",
                agent_name="dan",
                type="cost_anomaly",
                severity="high",
                message="Cost anomaly detected for dan",
                active=True,
                triggered_at=now,
                resolved_at=None,
                source="loki",
            ),
            AlertItem(
                id="alert_002",
                agent_name="derrick",
                type="agent_offline",
                severity="medium",
                message="Agent derrick went offline",
                active=False,
                triggered_at=now,
                resolved_at=now,
                source="health",
            ),
            AlertItem(
                id="alert_003",
                agent_name="dan",
                type="sdk_health",
                severity="low",
                message="SDK health warning for dan",
                active=True,
                triggered_at=now,
                resolved_at=None,
                source="loki",
            ),
        ],
        total=3,
        active_count=2,
        fetched_at=now,
    )


def inject_mock_services(app, **kwargs):
    """Inject mock services into app.state for route testing."""
    for key, value in kwargs.items():
        setattr(app.state, key, value)
