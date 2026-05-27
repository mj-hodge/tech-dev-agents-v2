"""Tests for STORY-480: Dashboard Overhaul — Agent phase tracking fields on fleet endpoint.

RED STATE — these tests FAIL until the following are implemented:
  1. `FleetAgentSummary` gains fields:
       phase_total: int | None = None
       phase_started_at: str | None = None
  2. The GET /api/fleet route populates those fields for each agent.

Test IDs: T480-06 through T480-09.
"""

from __future__ import annotations

import pytest

from tests.ops_console.conftest import inject_mock_services


# ---------------------------------------------------------------------------
# T480-06 / T480-07 — phase_total and phase_started_at keys present
# ---------------------------------------------------------------------------


class TestFleetAgentPhaseFieldsPresent:
    """T480-06 & T480-07: Each agent in GET /api/fleet MUST include phase tracking fields."""

    @pytest.mark.asyncio
    async def test_each_agent_has_phase_total_key(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-06: Every agent object in fleet response MUST contain 'phase_total' key.

        Fails until phase_total is added to FleetAgentSummary and the route populates it.
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

        assert len(data["agents"]) > 0, "No agents in fleet response — cannot verify phase_total"
        for agent in data["agents"]:
            assert "phase_total" in agent, (
                f"Agent '{agent.get('name', '?')}' missing 'phase_total' key — "
                "add `phase_total: int | None = None` to FleetAgentSummary"
            )

    @pytest.mark.asyncio
    async def test_each_agent_has_phase_started_at_key(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-07: Every agent object in fleet response MUST contain 'phase_started_at' key.

        Fails until phase_started_at is added to FleetAgentSummary and the route populates it.
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

        assert len(data["agents"]) > 0, "No agents in fleet response — cannot verify phase_started_at"
        for agent in data["agents"]:
            assert "phase_started_at" in agent, (
                f"Agent '{agent.get('name', '?')}' missing 'phase_started_at' key — "
                "add `phase_started_at: str | None = None` to FleetAgentSummary"
            )


# ---------------------------------------------------------------------------
# T480-08 / T480-09 — phase_total and phase_started_at type validation
# ---------------------------------------------------------------------------


class TestFleetAgentPhaseFieldTypes:
    """T480-08 & T480-09: phase_total must be int-or-null; phase_started_at must be str-or-null."""

    @pytest.mark.asyncio
    async def test_phase_total_is_int_or_null(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-08: phase_total in each agent MUST be an int or null — never a string or bool.

        Fails until phase_total is added to FleetAgentSummary with correct type annotation.
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

        assert len(data["agents"]) > 0, "No agents in fleet response — cannot verify phase_total type"
        for agent in data["agents"]:
            assert "phase_total" in agent, (
                f"Agent '{agent.get('name', '?')}' missing 'phase_total' key"
            )
            value = agent["phase_total"]
            assert value is None or isinstance(value, int), (
                f"phase_total for agent '{agent.get('name', '?')}' must be int or null, "
                f"got {type(value).__name__!r} with value {value!r}"
            )

    @pytest.mark.asyncio
    async def test_phase_started_at_is_str_or_null(
        self,
        client,
        app,
        mock_agent_service,
        mock_cost_service,
        mock_monday_service,
        mock_alert_service,
        mock_loki_client,
    ):
        """T480-09: phase_started_at in each agent MUST be a str (ISO timestamp) or null.

        Fails until phase_started_at is added to FleetAgentSummary with correct type annotation.
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

        assert len(data["agents"]) > 0, "No agents in fleet response — cannot verify phase_started_at type"
        for agent in data["agents"]:
            assert "phase_started_at" in agent, (
                f"Agent '{agent.get('name', '?')}' missing 'phase_started_at' key"
            )
            value = agent["phase_started_at"]
            assert value is None or isinstance(value, str), (
                f"phase_started_at for agent '{agent.get('name', '?')}' must be str or null, "
                f"got {type(value).__name__!r} with value {value!r}"
            )
