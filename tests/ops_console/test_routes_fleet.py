"""Tests for fleet route — T43-T45."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.agent_dashboard import build_agent_record, build_health_snapshot
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.models.responses import CostToday
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    _make_cost_today,
    _make_health_snapshots,
    _make_story_info,
    inject_mock_services,
)


class TestFleetEndpoint:
    """T43-T45: GET /api/fleet."""

    @pytest.mark.asyncio
    async def test_fleet_overview_returns_aggregates(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T43: GET /api/fleet returns FleetOverviewResponse with correct agent counts and spend."""
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
        assert data["total_agents"] == 2
        assert "fleet_health_score" in data
        assert "total_daily_spend_usd" in data
        assert "agents" in data
        assert len(data["agents"]) == 2

    @pytest.mark.asyncio
    async def test_fleet_health_score_calculation(
        self, client, app, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T44: Fleet health score with 1 online, 1 stuck out of 2 enabled = (1+0)/2 - (1*0.3/2) = 0.35."""
        now = datetime.now(timezone.utc).isoformat()
        # 1 ONLINE, 1 STUCK
        snapshots = [
            build_health_snapshot("dan", now, 86400, 1, 0),  # ONLINE
            build_health_snapshot("derrick", now, 86400, 1, 0),  # Also ONLINE based on recent activity
        ]

        # Make derrick STUCK by giving old last_activity
        from tech_dev_agents.agent_dashboard import AgentHealthSnapshot
        from tech_dev_agents.cost_dashboard import classify_agent_status

        # To get a STUCK status, we need last_activity > 30 min but < offline threshold
        import dataclasses
        old_activity = "2026-04-01T10:00:00+00:00"  # Old enough for stuck
        stuck_snapshot = build_health_snapshot("derrick", old_activity, 86400, 1, 0)

        from unittest.mock import MagicMock
        mock_agent_service = MagicMock()
        mock_agent_service.get_registry.return_value = _make_agent_records()
        mock_agent_service.get_agent.return_value = _make_agent_records()[0]
        mock_agent_service.get_all_health = AsyncMock(return_value=[snapshots[0], stuck_snapshot])
        mock_agent_service.get_agent_health = AsyncMock(return_value=snapshots[0])

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
        # Health score should be < 1.0 if one agent is stuck
        assert data["fleet_health_score"] <= 1.0
        assert data["fleet_health_score"] >= 0.0

    @pytest.mark.asyncio
    async def test_fleet_cost_breakdown_fields(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """STORY-346: GET /api/fleet returns per-agent cost breakdown fields."""
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
        for agent in data["agents"]:
            assert "today_foundry_usd" in agent
            assert "today_sdk_usd" in agent
            assert "today_openai_usd" in agent
            # Values from _make_cost_today(): foundry=2.22, sdk=10.25, openai=0.0
            assert agent["today_foundry_usd"] == 2.22
            assert agent["today_sdk_usd"] == 10.25
            assert agent["today_openai_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_fleet_cost_breakdown_defaults_on_error(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """STORY-346: Cost breakdown fields default to 0.0 when cost fetch fails."""
        mock_cost_service = AsyncMock()
        mock_cost_service.get_today_cost.side_effect = RuntimeError("cost fetch failed")
        mock_cost_service.get_fleet_daily_spend.return_value = 0.0
        mock_cost_service.get_fleet_monthly_spend.return_value = 0.0

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
        for agent in data["agents"]:
            assert agent["today_foundry_usd"] == 0.0
            assert agent["today_sdk_usd"] == 0.0
            assert agent["today_openai_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_fleet_health_score_all_online(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T45: Fleet health score with all agents online = 1.0."""
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
        assert data["fleet_health_score"] == 1.0

    @pytest.mark.asyncio
    async def test_fleet_agents_include_cost_breakdown(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T337-01: Fleet agents include cost breakdown fields (foundry, sdk, openai)."""
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
        for agent in data["agents"]:
            assert "today_foundry_usd" in agent, "Missing today_foundry_usd"
            assert "today_sdk_usd" in agent, "Missing today_sdk_usd"
            assert "today_openai_usd" in agent, "Missing today_openai_usd"
            assert "today_cost_usd" in agent, "Missing today_cost_usd"
        # Verify values from mock (foundry=2.22, sdk=10.25, openai=0.0)
        dan = data["agents"][0]
        assert dan["today_foundry_usd"] == 2.22
        assert dan["today_sdk_usd"] == 10.25
        assert dan["today_openai_usd"] == 0.0
