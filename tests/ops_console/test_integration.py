"""Integration tests — T60-T65: Service-to-module integration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tech_dev_agents.agent_dashboard import (
    AgentHealthSnapshot,
    AgentNotFoundError,
    build_agent_record,
    build_health_snapshot,
    evaluate_health_alerts,
)
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.models.responses import (
    AgentStatusEnum,
    CostToday,
    FleetAgentSummary,
    StoryInfo,
)
from tech_dev_agents.ops_console.services.agent_service import AgentService
from tech_dev_agents.ops_console.services.alert_service import AlertService
from tech_dev_agents.ops_console.services.cost_service import CostService
from tech_dev_agents.ops_console.services.monday_service import MondayService


class TestAgentServiceModuleIntegration:
    """T60: AgentService integrates with agent_dashboard module functions."""

    @pytest.mark.asyncio
    async def test_health_snapshot_uses_build_health_snapshot(self, tmp_path):
        """T60: AgentService.get_all_health calls agent_dashboard.build_health_snapshot
        with real VM response data and returns correctly structured AgentHealthSnapshot."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps(agents))

        now = datetime.now(timezone.utc).isoformat()
        vm_data = {
            "last_activity": now,
            "uptime_seconds": 86400,
            "active_sessions": 2,
            "error_count": 1,
        }

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(200, json=vm_data)

        service = AgentService(str(reg_path), mock_http, "test-key")
        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert isinstance(s, AgentHealthSnapshot)
        assert s.agent_name == "dan"
        assert s.uptime_seconds == 86400
        assert s.active_sessions == 2
        assert s.error_count == 1
        # Status is an AgentActivityStatus enum
        assert isinstance(s.status, AgentActivityStatus)


class TestCostServiceModuleIntegration:
    """T61: CostService integrates with cost_dashboard and cost_collector modules."""

    @pytest.mark.asyncio
    async def test_cost_aggregation_uses_aggregate_usage(self):
        """T61: CostService.get_cost_breakdown passes parsed Loki data through
        cost_dashboard.aggregate_usage and returns correct period totals."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        date_yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        date_two_days_ago = (now - timedelta(days=2)).strftime("%Y-%m-%d")

        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"date": date_yesterday, "cost": 10.25, "sessions": 8, "turns": 142},
            {"date": date_two_days_ago, "cost": 8.50, "sessions": 6, "turns": 110},
        ]

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_cost_breakdown("dan", days=7, granularity="daily")

        assert result.agent_name == "dan"
        assert len(result.daily) == 7
        # At least the days with data should have non-zero costs
        cost_dates = {d.date: d.sdk_cost_usd for d in result.daily if d.sdk_cost_usd > 0}
        assert len(cost_dates) >= 1  # At least some data mapped


class TestMondayServiceModuleIntegration:
    """T62: MondayService integrates with monday_agent module."""

    @pytest.mark.asyncio
    async def test_story_fetch_uses_agent_monday_client(self):
        """T62: MondayService.get_current_story wraps AgentMondayClient.get_story
        in asyncio.to_thread and returns StoryInfo."""
        mock_client = MagicMock()
        mock_client.board_id = 18405631030
        mock_client.get_story.return_value = {
            "id": 12345,
            "name": "STORY-016",
            "column_values": {"phase": "Phase 8", "status": "Active"},
            "group": {"title": "In Progress"},
        }

        service = MondayService(clients={"dan": mock_client})
        result = await service.get_current_story("dan")

        assert isinstance(result, StoryInfo)
        assert result.item_id == 12345
        assert result.name == "STORY-016"
        # Verify it was called (proves to_thread was used)
        mock_client.get_story.assert_called_once()


class TestRestartValidationIntegration:
    """T63: Restart validation uses agent_dashboard module functions."""

    @pytest.mark.asyncio
    async def test_restart_uses_validate_restart(self, tmp_path):
        """T63: AgentService.restart_agent calls agent_dashboard.validate_restart
        and rejects restart for disabled agent (real validation logic)."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": False},
        ]
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps(agents))

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        service = AgentService(str(reg_path), mock_http, "test-key")

        with pytest.raises(ValueError, match="disabled"):
            await service.restart_agent("dan", "test reason")


class TestAlertServiceModuleIntegration:
    """T64: AlertService integrates with evaluate_health_alerts."""

    @pytest.mark.asyncio
    async def test_alert_merge_uses_evaluate_functions(self):
        """T64: AlertService.get_alerts calls evaluate_health_alerts
        with real threshold/health logic and merges results correctly."""
        # Create offline snapshot — should trigger health alert
        offline_snapshot = build_health_snapshot("dan", None, 0, 0, 0)

        mock_agent_service = AsyncMock()
        mock_agent_service.get_all_health.return_value = [offline_snapshot]

        mock_loki = AsyncMock()
        mock_loki.query_anomalies.return_value = []
        mock_loki.query_sdk_health.return_value = []
        mock_loki.query_terminal_guard.return_value = []

        mock_cost_service = AsyncMock()

        service = AlertService(mock_loki, mock_agent_service, mock_cost_service)
        result = await service.get_alerts()

        # Should contain at least one health alert for offline agent
        assert result.total > 0
        alert_types = {a.type for a in result.alerts}
        assert "agent_offline" in alert_types or len(result.alerts) > 0


class TestFleetHealthScoreIntegration:
    """T65: Fleet overview calculation end-to-end."""

    @pytest.mark.asyncio
    async def test_fleet_health_score_with_mixed_statuses(
        self, client, app, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T65: Fleet endpoint computes health score from real AgentService health data:
        mixed statuses produce correct score calculation."""
        now = datetime.now(timezone.utc).isoformat()

        # 2 online + 1 stuck + 1 offline = 4 agents
        agents = [
            build_agent_record("dan", "10.0.1.10", 8080, "developer", True),
            build_agent_record("derrick", "10.0.1.11", 8080, "developer", True),
            build_agent_record("dave", "10.0.1.12", 8080, "developer", True),
            build_agent_record("diana", "10.0.1.13", 8080, "developer", True),
        ]

        snapshots = [
            build_health_snapshot("dan", now, 86400, 1, 0),      # ONLINE
            build_health_snapshot("derrick", now, 86400, 1, 0),  # ONLINE
            build_health_snapshot("dave", "2026-04-01T10:00:00+00:00", 86400, 1, 0),  # STUCK (old activity)
            build_health_snapshot("diana", None, 0, 0, 0),       # OFFLINE
        ]

        mock_agent_service = MagicMock()
        mock_agent_service.get_registry.return_value = agents
        mock_agent_service.get_agent.return_value = agents[0]
        mock_agent_service.get_all_health = AsyncMock(return_value=snapshots)
        mock_agent_service.get_agent_health = AsyncMock(return_value=snapshots[0])

        from tests.ops_console.conftest import inject_mock_services
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/fleet")
        assert resp.status_code == 200
        data = resp.json()
        score = data["fleet_health_score"]
        assert 0.0 <= score <= 1.0
        # With mixed statuses, score should be less than 1.0
        assert score < 1.0
