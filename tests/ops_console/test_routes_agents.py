"""Tests for agent API routes — T33-T42."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from tech_dev_agents.agent_dashboard import AgentNotFoundError, build_health_snapshot, build_restart_result
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.models.responses import (
    CostBreakdownResponse,
    CostToday,
    DailyCost,
    DataFreshness,
    StoryInfo,
)
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    _make_cost_today,
    _make_health_snapshots,
    _make_restart_result,
    _make_story_info,
    inject_mock_services,
)


class TestAgentListEndpoint:
    """T33-T35: GET /api/agents."""

    @pytest.mark.asyncio
    async def test_list_agents_returns_all(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T33: GET /api/agents returns AgentListResponse with all registered agents."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["agents"]) == 2
        names = {a["name"] for a in data["agents"]}
        assert "dan" in names
        assert "derrick" in names
        dan = next(a for a in data["agents"] if a["name"] == "dan")
        # STORY-038: today_cost_usd is now an alias for today_total_usd (sdk + azure)
        assert dan["today_cost_usd"] == 12.47
        assert dan["today_foundry_usd"] == 2.22  # Azure Foundry as primary
        assert dan["today_sdk_usd"] == 10.25

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_status(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T34: GET /api/agents?status=online returns only online agents."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents?status=online")
        assert resp.status_code == 200
        data = resp.json()
        for agent in data["agents"]:
            assert agent["status"] == "online"

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_enabled(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T35: GET /api/agents?enabled=true returns only enabled agents."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents?enabled=true")
        assert resp.status_code == 200
        data = resp.json()
        for agent in data["agents"]:
            assert agent["enabled"] is True


class TestAgentDetailEndpoint:
    """T36-T37: GET /api/agents/{name}."""

    @pytest.mark.asyncio
    async def test_agent_detail_returns_full_response(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T36: GET /api/agents/dan returns AgentDetailResponse with health, cost, story, activity."""
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
        assert data["name"] == "dan"
        assert "status" in data
        assert "cost_today" in data
        assert "current_story" in data
        assert "recent_activity" in data
        assert data["cost_today"]["sdk_cost_usd"] == 10.25

    @pytest.mark.asyncio
    async def test_agent_detail_not_found(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T37: GET /api/agents/unknown returns 404 with 'not found in registry' message."""
        mock_agent_service.get_agent.side_effect = AgentNotFoundError("Agent 'unknown' not found in registry")
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/unknown")
        assert resp.status_code == 404
        assert "not found in registry" in resp.json()["detail"]


class TestAgentCostEndpoint:
    """T38-T39: GET /api/agents/{name}/cost."""

    @pytest.mark.asyncio
    async def test_cost_endpoint_default_7_days(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T38: GET /api/agents/dan/cost returns CostBreakdownResponse with 7 daily entries by default."""
        now = datetime.now(timezone.utc).isoformat()
        mock_cost_service.get_cost_breakdown.return_value = CostBreakdownResponse(
            agent_name="dan",
            period_start="2026-03-25",
            period_end="2026-04-01",
            granularity="daily",
            total_sdk_cost_usd=85.30,
            total_azure_cost_usd=18.50,
            total_cost_usd=103.80,
            daily=[
                DailyCost(date=f"2026-03-{25+i}", sdk_cost_usd=12.0, azure_cost_usd=2.5, total_cost_usd=14.5, sdk_sessions=10, sdk_turns=180)
                for i in range(7)
            ],
            data_freshness=DataFreshness(sdk_as_of=now, azure_as_of=None, azure_is_estimated=False),
        )
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/dan/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["daily"]) == 7
        assert data["granularity"] == "daily"

    @pytest.mark.asyncio
    async def test_cost_endpoint_custom_days(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T39: GET /api/agents/dan/cost?days=30&granularity=daily returns 30 entries."""
        now = datetime.now(timezone.utc).isoformat()
        mock_cost_service.get_cost_breakdown.return_value = CostBreakdownResponse(
            agent_name="dan",
            period_start="2026-03-02",
            period_end="2026-04-01",
            granularity="daily",
            total_sdk_cost_usd=300.0,
            total_azure_cost_usd=60.0,
            total_cost_usd=360.0,
            daily=[
                DailyCost(date=f"2026-03-{i:02d}", sdk_cost_usd=10.0, azure_cost_usd=2.0, total_cost_usd=12.0, sdk_sessions=5, sdk_turns=90)
                for i in range(1, 31)
            ],
            data_freshness=DataFreshness(sdk_as_of=now, azure_as_of=None, azure_is_estimated=False),
        )
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/dan/cost?days=30&granularity=daily")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["daily"]) == 30


class TestAgentActivityEndpoint:
    """T40-T41: GET /api/agents/{name}/activity."""

    @pytest.mark.asyncio
    async def test_activity_feed_default_limit(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T40: GET /api/agents/dan/activity returns up to 50 events by default."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/dan/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "dan"
        assert "events" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_activity_feed_filter_by_type(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T41: GET /api/agents/dan/activity?type=commit returns only commit events."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/dan/activity?type=commit")
        assert resp.status_code == 200
        data = resp.json()
        # All events should be of type 'commit' (or empty if none match)
        for event in data["events"]:
            assert event["type"] == "commit"


class TestAgentControlEndpoints:
    """T42: POST restart and pause."""

    @pytest.mark.asyncio
    async def test_restart_endpoint_returns_result(self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client):
        """T42: POST /api/agents/dan/restart with valid body returns RestartResponse with success=True."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.post(
            "/api/agents/dan/restart",
            json={"reason": "Agent stuck on Phase 8 for 3 hours"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["agent_name"] == "dan"
        assert data["requested_by"] == "ops-console"
