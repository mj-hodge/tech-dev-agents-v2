"""Tests for STORY-024: Azure Foundry Cost Tracking + Agent Status Fix — T46-T56."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tech_dev_agents.agent_dashboard import build_health_snapshot
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.models.responses import (
    CostBreakdownResponse,
    CostToday,
    DailyCost,
    DataFreshness,
)
from tech_dev_agents.ops_console.services.cost_service import CostService
from tech_dev_agents.ops_console.services.loki_client import LokiClient, LokiLogEntry
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    _make_cost_today,
    _make_health_snapshots,
    _make_story_info,
    inject_mock_services,
)


# ---------------------------------------------------------------------------
# Fix 1: Azure Cost as Primary Source (AC-1, AC-2, AC-3)
# ---------------------------------------------------------------------------


class TestFleetAzureCostSource:
    """T46-T48: Fleet spend fields use Azure costs, not SDK+Azure total."""

    @pytest.mark.asyncio
    async def test_fleet_daily_spend_azure_only(self):
        """T46: get_fleet_daily_spend sums azure_cost_usd, not total_cost_usd.

        Agent 'dan': sdk=10.25, azure=2.22, total=12.47
        Agent 'derrick': sdk=8.00, azure=1.50, total=9.50

        Fleet daily spend should be 2.22 + 1.50 = 3.72 (Azure only).
        """
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"cost": 10.25, "sessions": 8, "turns": 142}
        ]

        mock_azure = AsyncMock()

        # Dan: azure=$2.22
        # Derrick: azure=$1.50
        async def _agent_daily_costs(agent_name, start, end):
            costs = {
                "dan": [DailyCost(date="2026-04-08", sdk_cost_usd=0.0, azure_cost_usd=2.22,
                                  total_cost_usd=2.22, sdk_sessions=0, sdk_turns=0)],
                "derrick": [DailyCost(date="2026-04-08", sdk_cost_usd=0.0, azure_cost_usd=1.50,
                                      total_cost_usd=1.50, sdk_sessions=0, sdk_turns=0)],
            }
            return costs.get(agent_name, [])

        mock_azure.get_agent_daily_costs = AsyncMock(side_effect=_agent_daily_costs)

        service = CostService(loki=mock_loki, azure=mock_azure)
        result = await service.get_fleet_daily_spend(["dan", "derrick"])

        # Must be Azure-only: 2.22 + 1.50 = 3.72
        assert result == 3.72

    @pytest.mark.asyncio
    async def test_fleet_monthly_spend_azure_only(self):
        """T47: get_fleet_monthly_spend sums azure costs only across 30 days.

        With azure=None, monthly spend should be 0.0 (not SDK total).
        """
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"date": "2026-04-01", "cost": 10.0, "sessions": 5, "turns": 80}
        ]

        # No Azure client — fleet monthly spend should be 0.0
        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_fleet_monthly_spend(["dan"])

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_fleet_endpoint_per_agent_azure_cost(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T48: GET /api/fleet per-agent today_cost_usd shows Azure cost, not total.

        CostToday has sdk=10.25, azure=2.22, total=12.47.
        Fleet agent summary today_cost_usd should be 2.22 (Azure only).
        """
        mock_cost_service = AsyncMock()
        mock_cost_service.get_today_cost.return_value = CostToday(
            sdk_cost_usd=10.25,
            azure_cost_usd=2.22,
            foundry_cost_usd=2.22,
            openai_cost_usd=0.0,
            total_cost_usd=12.47,
            sdk_sessions=8,
            sdk_turns=142,
        )
        mock_cost_service.get_fleet_daily_spend.return_value = 4.44
        mock_cost_service.get_fleet_monthly_spend.return_value = 66.60

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

        # Each agent's today_cost_usd should be Foundry cost (2.22)
        for agent in data["agents"]:
            assert agent["today_cost_usd"] == 2.22


class TestAgentDetailAzureCost:
    """T49: Agent detail cost_7d/cost_30d use Azure-only totals."""

    @pytest.mark.asyncio
    async def test_agent_detail_cost_7d_30d_azure_only(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T49: GET /api/agents/dan cost_7d and cost_30d reflect Azure costs only.

        Breakdown has total_azure_cost_usd=18.50, total_cost_usd=103.80.
        cost_7d and cost_30d should be 18.50, not 103.80.
        """
        now = datetime.now(timezone.utc).isoformat()
        mock_cost_service = AsyncMock()
        mock_cost_service.get_today_cost.return_value = CostToday(
            sdk_cost_usd=10.25, azure_cost_usd=2.22, foundry_cost_usd=2.22,
            openai_cost_usd=0.0, total_cost_usd=12.47,
            sdk_sessions=8, sdk_turns=142,
        )
        mock_cost_service.get_cost_breakdown.return_value = CostBreakdownResponse(
            agent_name="dan",
            period_start="2026-03-25",
            period_end="2026-04-01",
            granularity="daily",
            total_sdk_cost_usd=85.30,
            total_azure_cost_usd=18.50,
            total_cost_usd=103.80,
            daily=[
                DailyCost(date=f"2026-03-{25+i}", sdk_cost_usd=12.0, azure_cost_usd=2.5,
                          total_cost_usd=14.5, sdk_sessions=10, sdk_turns=180)
                for i in range(7)
            ],
            data_freshness=DataFreshness(sdk_as_of=now, azure_as_of=now, azure_is_estimated=True),
        )

        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/dan")
        assert resp.status_code == 200
        data = resp.json()

        # cost_7d and cost_30d should reflect Azure-only totals
        assert data["cost_7d"] == 18.50
        assert data["cost_30d"] == 18.50


# ---------------------------------------------------------------------------
# Fix 2: Loki-Based Agent Status (AC-4, AC-5, AC-6)
# ---------------------------------------------------------------------------


class TestLokiGetLastActivity:
    """T50-T51: LokiClient.get_last_activity method."""

    @pytest.mark.asyncio
    async def test_get_last_activity_returns_timestamp(self):
        """T50: get_last_activity returns ISO timestamp from most recent Loki entry."""
        now = datetime.now(timezone.utc)
        ts_ns = str(int(now.timestamp() * 1e9))

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "stream": {"agent": "dan"},
                            "values": [[ts_ns, "some log line"]],
                        }
                    ]
                }
            },
        )

        client = LokiClient(
            base_url="http://loki:3100",
            api_key="test-key",
            http_client=mock_http,
        )
        result = await client.get_last_activity("dan")

        assert result is not None
        # Should be a valid ISO timestamp
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_get_last_activity_no_entries_returns_none(self):
        """T51: get_last_activity returns None when Loki has no entries for the agent."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(
            200,
            json={"data": {"result": []}},
        )

        client = LokiClient(
            base_url="http://loki:3100",
            api_key="test-key",
            http_client=mock_http,
        )
        result = await client.get_last_activity("unknown-agent")

        assert result is None


class TestAgentServiceLokiHealth:
    """T52-T56: AgentService health polling via Loki instead of HTTP."""

    def _make_registry_file(self, tmp_path, agents=None):
        if agents is None:
            agents = [
                {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
                {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer", "enabled": True},
            ]
        path = tmp_path / "registry.json"
        path.write_text(json.dumps(agents))
        return str(path)

    @pytest.mark.asyncio
    async def test_health_poll_uses_loki_not_http(self, tmp_path):
        """T52: _poll_agent_health queries Loki for activity, does NOT call HTTP /health.

        The HTTP client should NOT receive any GET requests for health polling.
        """
        from tech_dev_agents.ops_console.services.agent_service import AgentService

        reg_path = self._make_registry_file(tmp_path)
        now = datetime.now(timezone.utc).isoformat()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_loki = AsyncMock()
        mock_loki.get_last_activity = AsyncMock(return_value=now)

        service = AgentService(
            registry_path=reg_path,
            http_client=mock_http,
            agent_api_key="test-key",
            loki_client=mock_loki,
        )
        snapshots = await service.get_all_health()

        # Loki should have been called, HTTP GET should NOT
        assert mock_loki.get_last_activity.call_count >= 1
        mock_http.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_online_recent_loki_activity(self, tmp_path):
        """T53: Agent with Loki activity in last 5 min shows as ONLINE."""
        from tech_dev_agents.ops_console.services.agent_service import AgentService

        agents = [{"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True}]
        reg_path = self._make_registry_file(tmp_path, agents)

        recent = datetime.now(timezone.utc).isoformat()
        mock_loki = AsyncMock()
        mock_loki.get_last_activity = AsyncMock(return_value=recent)
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        service = AgentService(
            registry_path=reg_path,
            http_client=mock_http,
            agent_api_key="test-key",
            loki_client=mock_loki,
        )
        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].status == AgentActivityStatus.ONLINE

    @pytest.mark.asyncio
    async def test_agent_idle_old_loki_activity(self, tmp_path):
        """T54: Agent with Loki activity 15 min ago shows as IDLE."""
        from tech_dev_agents.ops_console.services.agent_service import AgentService

        agents = [{"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True}]
        reg_path = self._make_registry_file(tmp_path, agents)

        fifteen_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        mock_loki = AsyncMock()
        mock_loki.get_last_activity = AsyncMock(return_value=fifteen_min_ago)
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        service = AgentService(
            registry_path=reg_path,
            http_client=mock_http,
            agent_api_key="test-key",
            loki_client=mock_loki,
        )
        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].status == AgentActivityStatus.IDLE

    @pytest.mark.asyncio
    async def test_agent_offline_no_loki_activity(self, tmp_path):
        """T55: Agent with no Loki activity shows as OFFLINE."""
        from tech_dev_agents.ops_console.services.agent_service import AgentService

        agents = [{"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True}]
        reg_path = self._make_registry_file(tmp_path, agents)

        mock_loki = AsyncMock()
        mock_loki.get_last_activity = AsyncMock(return_value=None)
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        service = AgentService(
            registry_path=reg_path,
            http_client=mock_http,
            agent_api_key="test-key",
            loki_client=mock_loki,
        )
        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].status == AgentActivityStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_loki_failure_degrades_to_offline(self, tmp_path):
        """T56: Loki error in health poll returns OFFLINE, does not raise."""
        from tech_dev_agents.ops_console.services.agent_service import AgentService

        agents = [{"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True}]
        reg_path = self._make_registry_file(tmp_path, agents)

        mock_loki = AsyncMock()
        mock_loki.get_last_activity = AsyncMock(side_effect=Exception("Loki down"))
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        service = AgentService(
            registry_path=reg_path,
            http_client=mock_http,
            agent_api_key="test-key",
            loki_client=mock_loki,
        )
        # Should not raise
        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].status == AgentActivityStatus.OFFLINE
