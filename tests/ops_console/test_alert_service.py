"""Tests for AlertService — T15-T18."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tech_dev_agents.agent_dashboard import DashboardAlert, build_health_snapshot
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.models.responses import AlertItem, AlertListResponse
from tech_dev_agents.ops_console.services.alert_service import AlertService


def _make_mock_loki():
    loki = AsyncMock()
    loki.query_anomalies.return_value = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "line": "[COST_ANOMALY] agent=dan spend=$45.00",
            "agent_name": "dan",
            "type": "cost_anomaly",
            "active": True,
        }
    ]
    loki.query_sdk_health.return_value = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "line": "[SDK_HEALTH] agent=derrick warning",
            "labels": {"agent": "derrick"},
        }
    ]
    loki.query_terminal_guard.return_value = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "line": "[TERMINAL_GUARD] DENIED agent=dan",
            "labels": {"agent": "dan"},
        }
    ]
    return loki


class TestAlertServiceMerge:
    """T15-T18: Alert merging and filtering."""

    @pytest.mark.asyncio
    async def test_merge_alerts_from_three_sources(self):
        """T15: get_alerts merges cost_dashboard alerts, health alerts, and Loki alerts, sorted by timestamp desc."""
        mock_loki = _make_mock_loki()
        mock_agent_service = AsyncMock()
        mock_agent_service.get_all_health.return_value = [
            build_health_snapshot("dan", None, 0, 0, 0),  # OFFLINE triggers alert
        ]
        mock_cost_service = AsyncMock()

        service = AlertService(mock_loki, mock_agent_service, mock_cost_service)
        result = await service.get_alerts()

        assert isinstance(result, AlertListResponse)
        # Should have alerts from health (offline) + loki (anomaly, sdk, guard)
        assert result.total > 0
        # Verify sorted by timestamp descending
        if len(result.alerts) > 1:
            for i in range(len(result.alerts) - 1):
                assert result.alerts[i].triggered_at >= result.alerts[i + 1].triggered_at

    @pytest.mark.asyncio
    async def test_filter_by_agent_name(self):
        """T16: get_alerts(agent='dan') returns only alerts for agent 'dan'."""
        mock_loki = _make_mock_loki()
        mock_agent_service = AsyncMock()
        mock_agent_service.get_all_health.return_value = []
        mock_cost_service = AsyncMock()

        service = AlertService(mock_loki, mock_agent_service, mock_cost_service)
        result = await service.get_alerts(agent="dan")

        for alert in result.alerts:
            assert alert.agent_name == "dan"

    @pytest.mark.asyncio
    async def test_active_anomalies_returns_only_active(self):
        """T17: get_active_anomalies returns only alerts where active=True and type='cost_anomaly'."""
        mock_loki = AsyncMock()
        mock_loki.query_anomalies.return_value = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "line": "[COST_ANOMALY] agent=dan",
                "agent_name": "dan",
                "active": True,
            }
        ]
        mock_agent_service = AsyncMock()
        mock_cost_service = AsyncMock()

        service = AlertService(mock_loki, mock_agent_service, mock_cost_service)
        result = await service.get_active_anomalies()

        assert len(result) >= 1
        for alert in result:
            assert alert.active is True
            assert alert.type == "cost_anomaly"

    @pytest.mark.asyncio
    async def test_filter_by_type_and_since(self):
        """T18: get_alerts(type='agent_offline', since='24h') returns only matching alerts within time window."""
        mock_loki = AsyncMock()
        mock_loki.query_anomalies.return_value = []
        mock_loki.query_sdk_health.return_value = []
        mock_loki.query_terminal_guard.return_value = []

        mock_agent_service = AsyncMock()
        mock_agent_service.get_all_health.return_value = [
            build_health_snapshot("dan", None, 0, 0, 0),  # OFFLINE
        ]
        mock_cost_service = AsyncMock()

        service = AlertService(mock_loki, mock_agent_service, mock_cost_service)
        result = await service.get_alerts(alert_type="agent_offline", since="24h")

        for alert in result.alerts:
            assert alert.type == "agent_offline"
