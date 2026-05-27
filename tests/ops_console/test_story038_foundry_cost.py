"""Tests for STORY-038: Agent card costs MUST show Azure Foundry spend.

Regression tests to prevent future changes from reverting the primary cost
number back to SDK cost or silently zeroing out Azure Foundry spend.

T43-T55.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tech_dev_agents.ops_console.models.responses import (
    AgentSummary,
    CostToday,
    DailyCost,
)
from tech_dev_agents.ops_console.services.cost_service import CostService
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    _make_cost_today,
    _make_health_snapshots,
    _make_story_info,
    inject_mock_services,
)


# ---------------------------------------------------------------------------
# Model regression tests — T43-T45
# ---------------------------------------------------------------------------


class TestAgentSummaryFoundryFields:
    """T43-T45: AgentSummary model MUST contain Foundry breakdown fields."""

    def test_agent_summary_has_foundry_field(self):
        """T43: AgentSummary MUST have today_foundry_usd as a required float field."""
        fields = AgentSummary.model_fields
        assert "today_foundry_usd" in fields, (
            "AgentSummary is missing today_foundry_usd — agent cards will NOT show Foundry cost"
        )

    def test_agent_summary_has_breakdown_fields(self):
        """T44: AgentSummary MUST have today_sdk_usd, today_openai_usd, today_total_usd."""
        fields = AgentSummary.model_fields
        for field_name in ("today_sdk_usd", "today_openai_usd", "today_total_usd"):
            assert field_name in fields, (
                f"AgentSummary missing {field_name} — cost breakdown will be incomplete"
            )

    def test_today_cost_usd_still_exists_as_alias(self):
        """T45: today_cost_usd MUST still exist (backward compatibility) and equal today_total_usd."""
        summary = AgentSummary(
            name="test",
            status="online",
            role="developer",
            enabled=True,
            last_activity=None,
            uptime_seconds=0,
            active_sessions=0,
            error_count=0,
            checked_at=datetime.now(timezone.utc).isoformat(),
            current_story=None,
            current_phase=None,
            today_foundry_usd=5.0,
            today_sdk_usd=10.0,
            today_openai_usd=1.0,
            today_total_usd=16.0,
        )
        assert summary.today_cost_usd == summary.today_total_usd, (
            "today_cost_usd must equal today_total_usd for backward compatibility"
        )


# ---------------------------------------------------------------------------
# CostToday model regression tests — T46
# ---------------------------------------------------------------------------


class TestCostTodayFoundryBreakdown:
    """T46: CostToday MUST split Azure into Foundry + OpenAI."""

    def test_cost_today_has_foundry_and_openai(self):
        """T46: CostToday must have foundry_cost_usd and openai_cost_usd fields."""
        fields = CostToday.model_fields
        assert "foundry_cost_usd" in fields, "CostToday missing foundry_cost_usd"
        assert "openai_cost_usd" in fields, "CostToday missing openai_cost_usd"

    def test_cost_today_azure_is_sum_of_foundry_and_openai(self):
        """CostToday.azure_cost_usd should equal foundry + openai."""
        ct = CostToday(
            sdk_cost_usd=10.0,
            azure_cost_usd=7.0,
            foundry_cost_usd=5.0,
            openai_cost_usd=2.0,
            total_cost_usd=17.0,
            sdk_sessions=5,
            sdk_turns=100,
        )
        assert ct.foundry_cost_usd + ct.openai_cost_usd == ct.azure_cost_usd


# ---------------------------------------------------------------------------
# Route tests — T47-T51
# ---------------------------------------------------------------------------


class TestAgentCardReturnsFoundryAsPrimary:
    """T47-T51: GET /api/agents returns Foundry as primary cost."""

    @pytest.mark.asyncio
    async def test_agent_card_returns_foundry_as_primary_cost(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T47: Each agent in GET /api/agents MUST have today_foundry_usd populated."""
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

        for agent in data["agents"]:
            assert "today_foundry_usd" in agent, (
                f"Agent {agent['name']} missing today_foundry_usd in response"
            )
            assert isinstance(agent["today_foundry_usd"], (int, float))

    @pytest.mark.asyncio
    async def test_agent_card_returns_full_cost_breakdown(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T48: Each agent MUST return sdk + foundry + openai + total cost fields."""
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

        for agent in data["agents"]:
            assert "today_sdk_usd" in agent
            assert "today_foundry_usd" in agent
            assert "today_openai_usd" in agent
            assert "today_total_usd" in agent
            # Total should be >= foundry (foundry is a component of total)
            assert agent["today_total_usd"] >= agent["today_foundry_usd"]

    @pytest.mark.asyncio
    async def test_agent_card_when_azure_disabled(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T49: When Azure is disabled, foundry=0, openai=0, total=sdk."""
        mock_cost = AsyncMock()
        mock_cost.get_today_cost.return_value = CostToday(
            sdk_cost_usd=10.25,
            azure_cost_usd=0.0,
            foundry_cost_usd=0.0,
            openai_cost_usd=0.0,
            total_cost_usd=10.25,
            sdk_sessions=8,
            sdk_turns=142,
        )

        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()

        for agent in data["agents"]:
            assert agent["today_foundry_usd"] == 0.0
            assert agent["today_openai_usd"] == 0.0
            assert agent["today_total_usd"] == agent["today_sdk_usd"]

    @pytest.mark.asyncio
    async def test_agent_card_when_azure_returns_error(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T50: When Azure errors, foundry=0 with sdk still populated (graceful degradation)."""
        mock_cost = AsyncMock()
        # Simulates: Azure errored, Loki worked. CostService returns foundry=0, sdk populated.
        mock_cost.get_today_cost.return_value = CostToday(
            sdk_cost_usd=10.25,
            azure_cost_usd=0.0,
            foundry_cost_usd=0.0,
            openai_cost_usd=0.0,
            total_cost_usd=10.25,
            sdk_sessions=8,
            sdk_turns=142,
        )

        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()

        for agent in data["agents"]:
            assert agent["today_foundry_usd"] == 0.0
            assert agent["today_sdk_usd"] == 10.25

    @pytest.mark.asyncio
    async def test_today_cost_usd_alias_equals_total(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T51: today_cost_usd in JSON response MUST equal today_total_usd."""
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

        for agent in resp.json()["agents"]:
            assert agent["today_cost_usd"] == agent["today_total_usd"], (
                "today_cost_usd must be an alias for today_total_usd"
            )


# ---------------------------------------------------------------------------
# CostService tests — T52-T54
# ---------------------------------------------------------------------------


class TestCostServiceFoundryBreakdown:
    """T52-T54: CostService MUST return Foundry/OpenAI breakdown."""

    @pytest.mark.asyncio
    async def test_get_today_cost_returns_foundry_breakdown(self):
        """T52: get_today_cost returns foundry_cost_usd and openai_cost_usd."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"cost": 10.25, "sessions": 8, "turns": 142}
        ]

        mock_azure = AsyncMock()
        mock_azure.get_agent_daily_costs.return_value = [
            DailyCost(
                date="2026-04-13",
                sdk_cost_usd=0.0,
                azure_cost_usd=5.50,
                foundry_cost_usd=4.00,
                openai_cost_usd=1.50,
                total_cost_usd=5.50,
                sdk_sessions=0,
                sdk_turns=0,
            )
        ]

        service = CostService(loki=mock_loki, azure=mock_azure)
        result = await service.get_today_cost("dan")

        assert result.foundry_cost_usd == 4.00
        assert result.openai_cost_usd == 1.50
        assert result.azure_cost_usd == 5.50
        assert result.total_cost_usd == 15.75  # 10.25 + 5.50

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_disabled_zeros_foundry(self):
        """T53: When azure=None, foundry_cost_usd and openai_cost_usd are 0."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"cost": 10.25, "sessions": 8, "turns": 142}
        ]

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_today_cost("dan")

        assert result.foundry_cost_usd == 0.0
        assert result.openai_cost_usd == 0.0
        assert result.azure_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_error_zeros_foundry(self):
        """T54: When Azure raises ConnectionError, foundry=0, sdk still populated."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"cost": 10.25, "sessions": 8, "turns": 142}
        ]

        mock_azure = AsyncMock()
        mock_azure.get_agent_daily_costs.side_effect = ConnectionError("Azure unreachable")

        service = CostService(loki=mock_loki, azure=mock_azure)
        result = await service.get_today_cost("dan")

        assert result.foundry_cost_usd == 0.0
        assert result.openai_cost_usd == 0.0
        assert result.sdk_cost_usd == 10.25
        assert result.total_cost_usd == 10.25


# ---------------------------------------------------------------------------
# Integration-level test — T55
# ---------------------------------------------------------------------------


class TestDashboardCostsE2E:
    """T55: End-to-end test for dashboard cost API with mocked backends."""

    @pytest.mark.asyncio
    async def test_dashboard_api_e2e_returns_foundry_breakdown(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T55: Full request through route → service → response includes Foundry breakdown."""
        # Build a real CostService with mocked Loki + Azure
        mock_loki_for_cost = AsyncMock()
        mock_loki_for_cost.query_cost_summaries.return_value = [
            {"cost": 8.50, "sessions": 6, "turns": 100}
        ]

        mock_azure = AsyncMock()
        mock_azure.get_agent_daily_costs.return_value = [
            DailyCost(
                date="2026-04-13",
                sdk_cost_usd=0.0,
                azure_cost_usd=3.20,
                foundry_cost_usd=2.50,
                openai_cost_usd=0.70,
                total_cost_usd=3.20,
                sdk_sessions=0,
                sdk_turns=0,
            )
        ]

        cost_service = CostService(loki=mock_loki_for_cost, azure=mock_azure)

        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] >= 1
        agent = data["agents"][0]
        assert agent["today_foundry_usd"] == 2.50
        assert agent["today_openai_usd"] == 0.70
        assert agent["today_sdk_usd"] == 8.50
        assert agent["today_total_usd"] == 11.70
        assert agent["today_cost_usd"] == 11.70  # backward compat alias

    @pytest.mark.asyncio
    async def test_dashboard_handles_azure_outage(
        self, client, app, mock_agent_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T55b: When Azure raises ConnectionError, SDK cost still returned, foundry=0."""
        mock_loki_for_cost = AsyncMock()
        mock_loki_for_cost.query_cost_summaries.return_value = [
            {"cost": 8.50, "sessions": 6, "turns": 100}
        ]

        mock_azure = AsyncMock()
        mock_azure.get_agent_daily_costs.side_effect = ConnectionError("Azure outage")

        cost_service = CostService(loki=mock_loki_for_cost, azure=mock_azure)

        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()

        agent = data["agents"][0]
        assert agent["today_sdk_usd"] == 8.50
        assert agent["today_foundry_usd"] == 0.0
        assert agent["today_openai_usd"] == 0.0
        assert agent["today_total_usd"] == 8.50
