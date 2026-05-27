"""Tests for health route — T49-T50."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.ops_console.config import Settings
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    _make_health_snapshots,
    inject_mock_services,
)


class TestHealthEndpoint:
    """T49-T50: GET /api/health."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T49: GET /api/health returns 200 with status='ok', version, uptime, agent counts."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
        )

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "uptime_seconds" in data
        assert "agents_reachable" in data
        assert "agents_total" in data
        assert "loki_reachable" in data

    @pytest.mark.asyncio
    async def test_health_no_auth_required(
        self, unauthed_client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T50: GET /api/health without X-API-Key header returns 200 (public endpoint)."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
        )

        resp = await unauthed_client.get("/api/health")
        assert resp.status_code == 200
