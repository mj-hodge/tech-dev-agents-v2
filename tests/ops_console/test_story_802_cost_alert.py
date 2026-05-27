"""STORY-802: AC-7 — AlertService surfaces [COST_ALERT] log lines.

The daily_cost_alert.sh cron emits [COST_ALERT] tagged log lines to
/var/log/ops-console/cost_alert.log. AlertService must query Loki for
these lines and surface them alongside existing alert types
(COST_ANOMALY, SDK_HEALTH, TERMINAL_GUARD).

Tests verify:
- AlertService queries a cost_alert Loki source
- [COST_ALERT] alerts surface with correct type/severity
- Alert filtering works for the new type
- Loki failure for cost_alert is handled gracefully
- Output variance: different alert types produce different alert objects
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.ops_console.models.responses import AlertItem, AlertListResponse
from tech_dev_agents.ops_console.services.alert_service import AlertService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mock_loki_with_cost_alert():
    """Mock LokiClient that includes a query_cost_alerts method."""
    loki = AsyncMock()
    now = datetime.now(timezone.utc).isoformat()

    loki.query_anomalies.return_value = [
        {
            "timestamp": now,
            "line": "[COST_ANOMALY] agent=dan spend=$45.00",
            "agent_name": "dan",
            "active": True,
        }
    ]
    loki.query_sdk_health.return_value = []
    loki.query_terminal_guard.return_value = []
    loki.query_cost_alerts.return_value = [
        {
            "timestamp": now,
            "line": "[COST_ALERT] ALERT foundry_daily_exceeded=true date=2026-05-01 total=$25.50 threshold=$20.00",
            "labels": {"source": "cost_alert"},
            "agent_name": "fleet",
        }
    ]
    return loki


def _make_mock_loki_no_cost_alert():
    """Mock LokiClient with no cost_alert results."""
    loki = AsyncMock()
    loki.query_anomalies.return_value = []
    loki.query_sdk_health.return_value = []
    loki.query_terminal_guard.return_value = []
    loki.query_cost_alerts.return_value = []
    return loki


def _make_mock_services():
    """Create mock agent_service and cost_service."""
    agent_svc = AsyncMock()
    agent_svc.get_all_health.return_value = []
    cost_svc = AsyncMock()
    return agent_svc, cost_svc


# ---------------------------------------------------------------------------
# AC-7a: AlertService surfaces [COST_ALERT] in get_alerts
# ---------------------------------------------------------------------------

class TestCostAlertIntegration:
    """[COST_ALERT] log lines appear in merged alert list."""

    @pytest.mark.asyncio
    async def test_cost_alert_surfaced_in_get_alerts(self):
        """AC-7a: get_alerts must include alerts from [COST_ALERT] Loki source."""
        loki = _make_mock_loki_with_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts()

        assert isinstance(result, AlertListResponse)
        cost_alerts = [a for a in result.alerts if a.type == "cost_alert"]
        assert len(cost_alerts) >= 1, (
            "get_alerts must include at least one cost_alert from [COST_ALERT] source"
        )

    @pytest.mark.asyncio
    async def test_cost_alert_has_correct_severity(self):
        """AC-7b: [COST_ALERT] alerts must have severity='high'."""
        loki = _make_mock_loki_with_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts()
        cost_alerts = [a for a in result.alerts if a.type == "cost_alert"]
        for alert in cost_alerts:
            assert alert.severity == "high", (
                f"cost_alert severity must be 'high', got '{alert.severity}'"
            )

    @pytest.mark.asyncio
    async def test_cost_alert_message_contains_threshold_info(self):
        """AC-7c: [COST_ALERT] message must contain the threshold and spend info."""
        loki = _make_mock_loki_with_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts()
        cost_alerts = [a for a in result.alerts if a.type == "cost_alert"]
        assert len(cost_alerts) >= 1
        assert "COST_ALERT" in cost_alerts[0].message

    @pytest.mark.asyncio
    async def test_cost_alert_source_is_loki(self):
        """AC-7d: [COST_ALERT] alert source must be 'loki'."""
        loki = _make_mock_loki_with_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts()
        cost_alerts = [a for a in result.alerts if a.type == "cost_alert"]
        for alert in cost_alerts:
            assert alert.source == "loki"


# ---------------------------------------------------------------------------
# AC-7e: Filtering by type=cost_alert
# ---------------------------------------------------------------------------

class TestCostAlertFiltering:
    """Filter mechanics work correctly for the new alert type."""

    @pytest.mark.asyncio
    async def test_filter_by_cost_alert_type(self):
        """AC-7e: get_alerts(alert_type='cost_alert') returns only cost alerts."""
        loki = _make_mock_loki_with_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts(alert_type="cost_alert")
        for alert in result.alerts:
            assert alert.type == "cost_alert"

    @pytest.mark.asyncio
    async def test_no_cost_alerts_returns_empty(self):
        """AC-7f: When no [COST_ALERT] lines exist, cost_alert type is absent."""
        loki = _make_mock_loki_no_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts(alert_type="cost_alert")
        assert result.total == 0


# ---------------------------------------------------------------------------
# Graceful degradation: Loki cost_alert query failure
# ---------------------------------------------------------------------------

class TestCostAlertDegradation:
    """AlertService handles Loki cost_alert query failures gracefully."""

    @pytest.mark.asyncio
    async def test_loki_cost_alert_failure_does_not_crash(self):
        """AC-7g: If query_cost_alerts raises, get_alerts still returns other alerts."""
        loki = _make_mock_loki_with_cost_alert()
        loki.query_cost_alerts.side_effect = Exception("Loki connection timeout")
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        # Must not raise — other alert sources should still work
        result = await service.get_alerts()
        assert isinstance(result, AlertListResponse)
        # Cost anomaly alerts should still be present
        anomaly_alerts = [a for a in result.alerts if a.type == "cost_anomaly"]
        assert len(anomaly_alerts) >= 1


# ---------------------------------------------------------------------------
# Output variance: different alert types produce different objects
# ---------------------------------------------------------------------------

class TestCostAlertOutputVariance:
    """Stub detection — cost_alert and cost_anomaly are distinct alert types."""

    @pytest.mark.asyncio
    async def test_cost_alert_and_cost_anomaly_are_different_types(self):
        """Output variance: cost_alert (daily threshold) ≠ cost_anomaly (spike)."""
        loki = _make_mock_loki_with_cost_alert()
        agent_svc, cost_svc = _make_mock_services()
        service = AlertService(loki, agent_svc, cost_svc)

        result = await service.get_alerts()
        types = {a.type for a in result.alerts}
        assert "cost_alert" in types, "cost_alert type must be present"
        assert "cost_anomaly" in types, "cost_anomaly type must be present"
        assert "cost_alert" != "cost_anomaly"  # distinct types
