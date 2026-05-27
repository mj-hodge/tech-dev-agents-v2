"""Tests for /api/health cost_mgmt_reachable field — STORY-627.

Verifies that GET /api/health includes a top-level `cost_mgmt_reachable` boolean
that surfaces whether the Azure Cost Management client can authenticate and query.
The endpoint MUST stay HTTP 200 regardless of cost client status (liveness probe safety).

Self-contained fixtures to avoid pre-existing conftest alias issue.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app


# --- Self-contained fixtures (avoid conftest alias issue) ---

_TEST_API_KEY = "test-ops-console-key-627"


@pytest.fixture
def _registry_file(tmp_path):
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps([
        {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
    ]))
    return str(path)


@pytest.fixture
def _settings(_registry_file, tmp_path) -> Settings:
    return Settings(
        ops_console_api_key=_TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=_registry_file,
        azure_subscription_id=None,
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
    )


@pytest_asyncio.fixture
async def _app(_settings):
    application = create_app(settings=_settings)
    yield application


@pytest_asyncio.fixture
async def _client(_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = _TEST_API_KEY
        yield c


# --- Helpers ---


def _inject(app, *, azure_cost_client=None):
    """Inject all required mocks for /api/health with a configurable cost client."""
    mock_agent_service = MagicMock()
    mock_agent_service.get_registry.return_value = []
    mock_agent_service.get_all_health = AsyncMock(return_value=[])

    mock_loki_client = AsyncMock()
    mock_loki_client.is_reachable.return_value = True

    app.state.agent_service = mock_agent_service
    app.state.loki_client = mock_loki_client
    app.state.started_at = datetime.now(timezone.utc)
    if azure_cost_client is not None:
        app.state.azure_cost_client = azure_cost_client
    elif hasattr(app.state, "azure_cost_client"):
        # Ensure it's explicitly None when we want no cost client
        app.state.azure_cost_client = None


def _make_healthy_cost_client() -> AsyncMock:
    """Mock AzureCostClient whose health_check returns ok=True."""
    mock = AsyncMock()
    mock.health_check.return_value = {
        "ok": True,
        "auth": True,
        "query": True,
        "error": None,
    }
    return mock


def _make_unhealthy_cost_client() -> AsyncMock:
    """Mock AzureCostClient whose health_check returns ok=False (stale SP)."""
    mock = AsyncMock()
    mock.health_check.return_value = {
        "ok": False,
        "auth": False,
        "query": False,
        "error": "AADSTS700016: Application not found",
    }
    return mock


# --- Tests ---


class TestHealthCostMgmtReachable:
    """AC-2: GET /api/health includes cost_mgmt_reachable boolean."""

    @pytest.mark.asyncio
    async def test_health_includes_cost_mgmt_reachable_true(self, _client, _app):
        """When cost client is healthy, cost_mgmt_reachable is True."""
        _inject(_app, azure_cost_client=_make_healthy_cost_client())

        resp = await _client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "cost_mgmt_reachable" in data
        assert data["cost_mgmt_reachable"] is True

    @pytest.mark.asyncio
    async def test_health_includes_cost_mgmt_reachable_false(self, _client, _app):
        """When cost client reports auth failure, cost_mgmt_reachable is False."""
        _inject(_app, azure_cost_client=_make_unhealthy_cost_client())

        resp = await _client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "cost_mgmt_reachable" in data
        assert data["cost_mgmt_reachable"] is False

    @pytest.mark.asyncio
    async def test_health_stays_200_when_cost_unreachable(self, _client, _app):
        """AC-2: Endpoint stays HTTP 200 even when cost_mgmt is unreachable — don't break liveness probes."""
        _inject(_app, azure_cost_client=_make_unhealthy_cost_client())

        resp = await _client.get("/api/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # Must include cost_mgmt_reachable=False (not omitted)
        assert resp.json()["cost_mgmt_reachable"] is False

    @pytest.mark.asyncio
    async def test_health_cost_mgmt_reachable_false_when_no_cost_client(self, _client, _app):
        """When Azure is not configured (no cost client), cost_mgmt_reachable defaults to False."""
        _inject(_app, azure_cost_client=None)

        resp = await _client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cost_mgmt_reachable"] is False

    @pytest.mark.asyncio
    async def test_health_cost_mgmt_reachable_false_when_health_check_raises(self, _client, _app):
        """Defensive: if health_check() raises, endpoint still returns 200 with cost_mgmt_reachable=False."""
        mock_cost_client = AsyncMock()
        mock_cost_client.health_check.side_effect = RuntimeError("Unexpected")
        _inject(_app, azure_cost_client=mock_cost_client)

        resp = await _client.get("/api/health")

        assert resp.status_code == 200
        assert resp.json()["cost_mgmt_reachable"] is False
