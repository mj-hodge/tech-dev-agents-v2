"""Tests for STORY-480: Dashboard Overhaul — Budget config and fleet-level cost totals.

RED STATE — these tests FAIL until the following are implemented:
  1. `Settings.daily_budget_usd: float = 50.0` added to config.py
  2. `FleetOverviewResponse` gains fields:
       daily_budget_usd: float
       daily_foundry_usd: float
       daily_sdk_usd: float
       daily_openai_usd: float
  3. The GET /api/fleet route populates those new fields.

Test IDs: T480-01 through T480-05.
"""

from __future__ import annotations

import pytest

from tests.ops_console.conftest import inject_mock_services


# ---------------------------------------------------------------------------
# T480-01 / T480-02 — Settings.daily_budget_usd
# ---------------------------------------------------------------------------


class TestSettingsDailyBudget:
    """T480-01 & T480-02: Settings must expose daily_budget_usd with default 50.0."""

    def test_settings_has_daily_budget_usd_attribute(self, test_settings):
        """T480-01: Settings object MUST have a daily_budget_usd attribute.

        Fails until `daily_budget_usd` is added to config.Settings.
        """
        assert hasattr(test_settings, "daily_budget_usd"), (
            "Settings is missing daily_budget_usd — add `daily_budget_usd: float = 50.0` to config.py"
        )

    def test_settings_daily_budget_usd_default_is_50(self, test_settings):
        """T480-02: Settings.daily_budget_usd default value MUST be 50.0.

        Fails until `daily_budget_usd: float = 50.0` is added to config.Settings.
        """
        value = getattr(test_settings, "daily_budget_usd", None)
        assert value == 50.0, (
            f"Settings.daily_budget_usd should default to 50.0, got {value!r}"
        )


# ---------------------------------------------------------------------------
# T480-03 — GET /api/fleet includes daily_budget_usd
# ---------------------------------------------------------------------------


class TestFleetResponseBudgetField:
    """T480-03: GET /api/fleet response MUST include daily_budget_usd."""

    @pytest.mark.asyncio
    async def test_fleet_response_includes_daily_budget_usd(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-03: FleetOverviewResponse must include daily_budget_usd field.

        Fails until FleetOverviewResponse.daily_budget_usd is added and the
        route populates it from settings.
        """
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

        assert "daily_budget_usd" in data, (
            "GET /api/fleet response missing 'daily_budget_usd' — "
            "add it to FleetOverviewResponse and populate it in the route"
        )
        assert isinstance(data["daily_budget_usd"], (int, float)), (
            f"daily_budget_usd must be numeric, got {type(data['daily_budget_usd'])}"
        )


# ---------------------------------------------------------------------------
# T480-04 — GET /api/fleet includes fleet-level provider cost totals
# ---------------------------------------------------------------------------


class TestFleetResponseProviderTotals:
    """T480-04: GET /api/fleet response MUST include fleet-level provider cost breakdowns."""

    @pytest.mark.asyncio
    async def test_fleet_response_includes_provider_total_fields(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-04: FleetOverviewResponse must include daily_foundry_usd, daily_sdk_usd, daily_openai_usd.

        Fails until all three fields are added to FleetOverviewResponse and the
        route aggregates per-agent cost breakdowns into fleet-level totals.
        """
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

        for field in ("daily_foundry_usd", "daily_sdk_usd", "daily_openai_usd"):
            assert field in data, (
                f"GET /api/fleet response missing '{field}' — "
                "add provider total fields to FleetOverviewResponse"
            )
            assert isinstance(data[field], (int, float)), (
                f"{field} must be numeric, got {type(data[field])}"
            )


# ---------------------------------------------------------------------------
# T480-05 — daily_foundry_usd equals sum of per-agent foundry costs
# ---------------------------------------------------------------------------


class TestFleetFoundryTotalEqualsAgentSum:
    """T480-05: fleet daily_foundry_usd MUST equal the sum of each agent's today_foundry_usd."""

    @pytest.mark.asyncio
    async def test_daily_foundry_usd_equals_sum_of_agent_foundry_costs(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-05: daily_foundry_usd == sum(agent.today_foundry_usd for agent in agents).

        conftest._make_cost_today() returns foundry_cost_usd=2.22.
        With 2 agents (dan + derrick) each returning 2.22, the fleet total must be 4.44.

        Fails until the route aggregates per-agent foundry costs into daily_foundry_usd.
        """
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

        assert "daily_foundry_usd" in data, (
            "daily_foundry_usd field missing from fleet response"
        )
        assert "agents" in data

        expected_foundry_total = sum(
            agent.get("today_foundry_usd", 0.0) for agent in data["agents"]
        )
        assert data["daily_foundry_usd"] == pytest.approx(expected_foundry_total), (
            f"daily_foundry_usd ({data['daily_foundry_usd']}) does not equal "
            f"sum of agent foundry costs ({expected_foundry_total})"
        )
        # Explicit check: 2 agents × 2.22 = 4.44
        assert data["daily_foundry_usd"] == pytest.approx(4.44), (
            f"Expected 4.44 (2 agents × 2.22 from conftest), got {data['daily_foundry_usd']}"
        )
