"""Tests for alert routes — T46-T48."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.ops_console.models.responses import AlertItem, AlertListResponse
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_alert_list,
    inject_mock_services,
)


class TestAlertEndpoint:
    """T46-T48: GET /api/alerts."""

    @pytest.mark.asyncio
    async def test_alerts_returns_list(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T46: GET /api/alerts returns AlertListResponse with merged alerts."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert "alerts" in data
        assert "total" in data
        assert "active_count" in data
        assert data["total"] == 3

    @pytest.mark.asyncio
    async def test_alerts_filter_by_agent(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T47: GET /api/alerts?agent=dan returns only dan's alerts."""
        # Return only dan's alerts when filtered
        now = datetime.now(timezone.utc).isoformat()
        mock_alert_service.get_alerts.return_value = AlertListResponse(
            alerts=[
                AlertItem(
                    id="alert_001",
                    agent_name="dan",
                    type="cost_anomaly",
                    severity="high",
                    message="Cost anomaly for dan",
                    active=True,
                    triggered_at=now,
                    resolved_at=None,
                    source="loki",
                )
            ],
            total=1,
            active_count=1,
            fetched_at=now,
        )
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/alerts?agent=dan")
        assert resp.status_code == 200
        data = resp.json()
        for alert in data["alerts"]:
            assert alert["agent_name"] == "dan"

    @pytest.mark.asyncio
    async def test_alerts_active_anomalies_for_banner(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T48: GET /api/alerts?type=cost_anomaly&active=true returns only active anomalies (for AlertBanner)."""
        now = datetime.now(timezone.utc).isoformat()
        mock_alert_service.get_alerts.return_value = AlertListResponse(
            alerts=[
                AlertItem(
                    id="alert_001",
                    agent_name="dan",
                    type="cost_anomaly",
                    severity="high",
                    message="Cost anomaly",
                    active=True,
                    triggered_at=now,
                    resolved_at=None,
                    source="loki",
                )
            ],
            total=1,
            active_count=1,
            fetched_at=now,
        )
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/alerts?type=cost_anomaly&active=true")
        assert resp.status_code == 200
        data = resp.json()
        for alert in data["alerts"]:
            assert alert["type"] == "cost_anomaly"
            assert alert["active"] is True
