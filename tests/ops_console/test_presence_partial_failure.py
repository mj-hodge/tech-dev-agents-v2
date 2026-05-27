"""Tests for STORY-628: asyncio.gather partial-failure resilience in GET /api/agents/presence.

RED state (Phase 7):
  On main: route still calls presence_service.get_all_presence() (SSH path).
    Tests set presence_service=None and patch _fetch_agent_summary, so the old
    route raises AttributeError → 500.  Assertions expect 200 → FAIL.
  On STORY-549 branch (PR #99): route uses asyncio.gather(..., return_exceptions=False).
    A single probe exception propagates unhandled → 500.  Assertions expect
    200 with partial results → FAIL.

Phase 8 GREEN criteria:
  Route uses asyncio.gather(..., return_exceptions=True).
  Exception results → AgentPresence(state=OFFLINE, detail=...).
  logger.warning called per failed probe.
  Response always 200 with all agents present (partial data > no data).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.ops_console.models.responses import (
    AgentStatusEnum,
    FleetAgentSummary,
)
from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fleet_summary(
    name: str,
    status: AgentStatusEnum,
    busy: bool = False,
    current_story: str | None = None,
) -> FleetAgentSummary:
    return FleetAgentSummary(
        name=name,
        status=status,
        busy=busy,
        current_story=current_story,
        today_cost_usd=0.0,
    )


def _inject_fleet_services(app, agent_names: list[str]):
    """Inject mock fleet services into app.state for the given agent names.

    Sets presence_service=None so the old SSH route path is dead.
    """
    app.state.presence_service = None

    agent_service = MagicMock()
    records = []
    for name in agent_names:
        rec = MagicMock()
        rec.name = name
        rec.enabled = True
        records.append(rec)
    agent_service.get_registry.return_value = records
    agent_service.get_all_health = AsyncMock(return_value=[])

    cost_service = AsyncMock()
    monday_service = AsyncMock()
    monday_service.get_current_story = AsyncMock(return_value=None)
    loki_client = AsyncMock()
    loki_client.get_agent_queue = AsyncMock(return_value=None)

    inject_mock_services(
        app,
        agent_service=agent_service,
        cost_service=cost_service,
        monday_service=monday_service,
        loki_client=loki_client,
    )


# ---------------------------------------------------------------------------
# T628-01: Partial failure → 200 with all agents present
# ---------------------------------------------------------------------------


class TestPartialFailureResilience:
    """STORY-628: asyncio.gather partial failure returns 200, not 500."""

    @pytest.mark.asyncio
    async def test_partial_failure_returns_200_with_all_agents(self, client, app):
        """T628-01: One probe fails, endpoint still returns 200 with all N agents."""
        agent_names = ["dan", "derrick", "daisy"]
        _inject_fleet_services(app, agent_names)

        # dan and derrick succeed; daisy raises TimeoutError
        summaries_or_errors = [
            _make_fleet_summary("dan", AgentStatusEnum.ONLINE),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
            TimeoutError("probe timed out"),
        ]

        async def _side_effect(*args, **kwargs):
            result = summaries_or_errors.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200, (
            f"Expected 200 with partial results, got {resp.status_code}. "
            "Bug: return_exceptions=False causes 500 on single probe failure."
        )
        data = resp.json()
        returned_names = {a["name"] for a in data["agents"]}
        assert returned_names == {"dan", "derrick", "daisy"}, (
            f"Expected all 3 agents in response, got {returned_names}"
        )

    @pytest.mark.asyncio
    async def test_partial_failure_successful_agents_have_fleet_state(self, client, app):
        """T628-02: Successful probes get their fleet-derived state, not OFFLINE."""
        agent_names = ["dan", "derrick", "daisy"]
        _inject_fleet_services(app, agent_names)

        summaries_or_errors = [
            _make_fleet_summary("dan", AgentStatusEnum.ONLINE),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
            TimeoutError("probe timed out"),
        ]

        async def _side_effect(*args, **kwargs):
            result = summaries_or_errors.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        agents_by_name = {a["name"]: a for a in resp.json()["agents"]}

        # ONLINE maps to "idle" in the STORY-549 _STATUS_TO_PRESENCE mapping
        assert agents_by_name["dan"]["state"] == "idle", (
            f"Expected dan=idle (ONLINE→idle), got {agents_by_name['dan']['state']}"
        )
        assert agents_by_name["derrick"]["state"] == "idle", (
            f"Expected derrick=idle (IDLE→idle), got {agents_by_name['derrick']['state']}"
        )

    @pytest.mark.asyncio
    async def test_partial_failure_failed_agent_has_offline_with_detail(self, client, app):
        """T628-03: Failed probe → state=OFFLINE with detail containing exception info."""
        agent_names = ["dan", "daisy"]
        _inject_fleet_services(app, agent_names)

        summaries_or_errors = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            TimeoutError("connection timed out"),
        ]

        async def _side_effect(*args, **kwargs):
            result = summaries_or_errors.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        agents_by_name = {a["name"]: a for a in resp.json()["agents"]}

        daisy = agents_by_name["daisy"]
        assert daisy["state"] == "offline", (
            f"Failed probe should map to OFFLINE, got {daisy['state']}"
        )
        assert daisy["detail"] is not None and daisy["detail"] != "", (
            "Failed probe must include a non-empty detail string"
        )
        assert "TimeoutError" in daisy["detail"] or "timed out" in daisy["detail"], (
            f"Detail should contain exception info, got: {daisy['detail']!r}"
        )


# ---------------------------------------------------------------------------
# T628-04: All probes fail → 200 with all OFFLINE (fail-open)
# ---------------------------------------------------------------------------


class TestAllProbesFail:
    """STORY-628 AC-6: endpoint fails open — never returns 500."""

    @pytest.mark.asyncio
    async def test_all_probes_fail_returns_200_with_all_offline(self, client, app):
        """T628-04: Every probe raises → 200 with all agents OFFLINE, not 500."""
        agent_names = ["dan", "derrick"]
        _inject_fleet_services(app, agent_names)

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=ConnectionError("network unreachable"),
        ):
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200, (
            f"Expected 200 (fail-open) when all probes fail, got {resp.status_code}. "
            "Bug: return_exceptions=False propagates the exception as 500."
        )
        data = resp.json()
        assert len(data["agents"]) == 2
        for agent in data["agents"]:
            assert agent["state"] == "offline", (
                f"Agent {agent['name']} should be OFFLINE on probe failure, "
                f"got {agent['state']}"
            )
            assert agent["detail"] is not None and agent["detail"] != ""


# ---------------------------------------------------------------------------
# T628-05: Warning logging per failed probe
# ---------------------------------------------------------------------------


class TestProbeExceptionLogging:
    """STORY-628 AC-2: probe exceptions logged at WARNING level."""

    @pytest.mark.asyncio
    async def test_probe_exception_logs_warning_with_agent_name(self, client, app, caplog):
        """T628-05: Each failed probe triggers logger.warning with agent name."""
        agent_names = ["dan", "daisy"]
        _inject_fleet_services(app, agent_names)

        summaries_or_errors = [
            _make_fleet_summary("dan", AgentStatusEnum.ONLINE),
            TimeoutError("probe timed out"),
        ]

        async def _side_effect(*args, **kwargs):
            result = summaries_or_errors.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ), caplog.at_level(logging.WARNING, logger="tech_dev_agents.ops_console.routes.presence"):
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200

        # Find warning log entries from the presence route
        warning_messages = [
            r.message for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert len(warning_messages) >= 1, (
            "Expected at least one WARNING log for the failed probe"
        )

        # The warning should mention the failing agent's name
        combined = " ".join(warning_messages)
        assert "daisy" in combined.lower() or "Daisy" in combined, (
            f"Warning should mention failing agent name 'daisy', got: {combined}"
        )


# ---------------------------------------------------------------------------
# T628-06: Output-variance gate
# ---------------------------------------------------------------------------


class TestOutputVariance:
    """Output-variance gate: different failure patterns → different results."""

    @pytest.mark.asyncio
    async def test_output_varies_with_different_failure_patterns(self, client, app):
        """T628-06: All-succeed vs partial-failure produce different state sets."""
        agent_names = ["dan", "derrick"]
        _inject_fleet_services(app, agent_names)

        # Scenario A: all probes succeed
        summaries_a = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
        ]

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries_a,
        ):
            resp_a = await client.get("/api/agents/presence")

        assert resp_a.status_code == 200, (
            f"Scenario A (all-succeed) should return 200, got {resp_a.status_code}"
        )
        states_a = {a["state"] for a in resp_a.json()["agents"]}

        # Scenario B: one probe fails
        _inject_fleet_services(app, agent_names)  # re-inject fresh mocks

        summaries_b = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            ConnectionError("network down"),
        ]

        async def _side_effect_b(*args, **kwargs):
            result = summaries_b.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=_side_effect_b,
        ):
            resp_b = await client.get("/api/agents/presence")

        assert resp_b.status_code == 200, (
            f"Scenario B (partial failure) should return 200, got {resp_b.status_code}"
        )
        states_b = {a["state"] for a in resp_b.json()["agents"]}

        # The two scenarios should produce different state distributions
        assert states_a != states_b, (
            f"Output variance failed: scenario A states={states_a} "
            f"should differ from scenario B states={states_b}"
        )
