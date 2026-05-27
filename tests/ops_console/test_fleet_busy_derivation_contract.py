"""STORY-574 — /fleet endpoint MUST derive `busy` + `status` from the
dispatch DB, not just the health probe.

Mark's recurring complaint (2026-04-24): *"agents have claimed stories
but aren't showing busy. I've asked so many times about this, we've
done so many tickets."* Root cause: prior fixes patched the AgentCard
render side but never locked the API data source. This test exists so
any future refactor that regresses `_fetch_agent_summary` back to
health-only will FAIL CI loudly.

Contract (what this test locks):
  - If `dispatch_db.has_active_claim(agent) == True` AND health probe
    shows no active SDK process, the /fleet response for that agent
    MUST still report `busy=True` and `status="working"`.
  - If DB says no claim AND health says SDK running, busy=True,
    status=WORKING (legacy health-probe-only path — still works).
  - If DB says no claim AND health says idle, busy=False, status=IDLE.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tech_dev_agents.ops_console.models.responses import AgentStatusEnum
from tech_dev_agents.ops_console.routes.fleet import _fetch_agent_summary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_agent(name="dan", enabled=True):
    a = MagicMock()
    a.name = name
    a.enabled = enabled
    return a


def _make_health(status_str="idle", active_sessions=0):
    h = MagicMock()
    h.status = MagicMock()
    h.status.value = status_str
    h.active_sessions = active_sessions
    return h


def _make_dispatch_db(has_claim: bool, raises: Exception | None = None):
    db = MagicMock()
    if raises is not None:
        db.has_active_claim = AsyncMock(side_effect=raises)
    else:
        db.has_active_claim = AsyncMock(return_value=has_claim)
    return db


def _noop_services():
    cost = MagicMock()
    cost.get_today_cost = AsyncMock(return_value=MagicMock(
        foundry_cost_usd=0.0, sdk_cost_usd=0.0, openai_cost_usd=0.0
    ))
    monday = MagicMock()
    monday.get_current_story = AsyncMock(return_value=None)
    loki = MagicMock()
    loki.query_current_work = AsyncMock(return_value=None)
    loki.get_agent_queue = AsyncMock(return_value={"queued": []})
    loki.query_agent_quota = AsyncMock(return_value=None)
    return cost, monday, loki


# ---------------------------------------------------------------------------
# The contract: DB claim must flip busy/status regardless of health probe
# ---------------------------------------------------------------------------


class TestFleetBusyDerivedFromDispatchDB:
    """The test Mark has been asking for since STORY-496/541 — locks the
    full chain from DB row to API response."""

    @pytest.mark.asyncio
    async def test_db_claim_true_health_idle_yields_busy_and_working(self):
        """The failing case Mark keeps describing: DB says claimed, health
        hasn't caught up yet → card should STILL say busy/working."""
        cost, monday, loki = _noop_services()
        dispatch_db = _make_dispatch_db(has_claim=True)
        summary = await _fetch_agent_summary(
            _make_agent("dan"),
            _make_health(status_str="idle", active_sessions=0),
            cost, monday, loki,
            dispatch_db=dispatch_db,
        )
        assert summary.busy is True, (
            "DB has an active claim for this agent; fleet MUST report busy=True "
            "regardless of whether the SDK process is visible in the health probe yet. "
            "This is the exact Mark complaint we're locking — do not loosen."
        )
        assert summary.status == AgentStatusEnum.WORKING, (
            f"With a DB claim, status must upgrade from IDLE to WORKING; got {summary.status!r}"
        )

    @pytest.mark.asyncio
    async def test_no_db_claim_health_working_keeps_legacy_path(self):
        """Regression guard: the pre-2026-04-24 health-probe-only path must
        still work when the DB has no claim (e.g., ad-hoc SDK session for
        a manager skill, no dispatch row)."""
        cost, monday, loki = _noop_services()
        dispatch_db = _make_dispatch_db(has_claim=False)
        summary = await _fetch_agent_summary(
            _make_agent("morris"),
            _make_health(status_str="working", active_sessions=1),
            cost, monday, loki,
            dispatch_db=dispatch_db,
        )
        assert summary.busy is True
        assert summary.status == AgentStatusEnum.WORKING

    @pytest.mark.asyncio
    async def test_no_db_claim_health_idle_yields_idle(self):
        cost, monday, loki = _noop_services()
        dispatch_db = _make_dispatch_db(has_claim=False)
        summary = await _fetch_agent_summary(
            _make_agent("daisy"),
            _make_health(status_str="idle", active_sessions=0),
            cost, monday, loki,
            dispatch_db=dispatch_db,
        )
        assert summary.busy is False
        assert summary.status == AgentStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_dispatch_db_None_falls_back_to_health_probe(self):
        """Backward compat: if the app.state has no dispatch_db_service
        (early boot, test harness), we fall back to the health probe
        alone. Must not crash."""
        cost, monday, loki = _noop_services()
        summary = await _fetch_agent_summary(
            _make_agent("dan"),
            _make_health(status_str="working", active_sessions=2),
            cost, monday, loki,
            dispatch_db=None,
        )
        assert summary.busy is True
        assert summary.status == AgentStatusEnum.WORKING

    @pytest.mark.asyncio
    async def test_dispatch_db_raises_falls_back_gracefully(self):
        """Defensive: a DB failure during has_active_claim must not take
        out the /fleet endpoint. Degrade to health-probe only."""
        cost, monday, loki = _noop_services()
        dispatch_db = _make_dispatch_db(has_claim=False, raises=RuntimeError("db down"))
        # Health says idle, DB errored — should NOT crash, should return idle
        summary = await _fetch_agent_summary(
            _make_agent("dan"),
            _make_health(status_str="idle", active_sessions=0),
            cost, monday, loki,
            dispatch_db=dispatch_db,
        )
        assert summary.busy is False
        assert summary.status == AgentStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_has_active_claim_called_with_agent_name_keyword(self):
        """Lock the function-call contract: has_active_claim takes
        agent_name as a kwarg. The signature in dispatch_db_service.py is
        `async def has_active_claim(self, agent_name: str) -> bool`.
        Positional call would work but the kwarg form makes grep/refactor
        safer."""
        cost, monday, loki = _noop_services()
        dispatch_db = _make_dispatch_db(has_claim=True)
        await _fetch_agent_summary(
            _make_agent("derrick"),
            _make_health("idle", 0),
            cost, monday, loki,
            dispatch_db=dispatch_db,
        )
        dispatch_db.has_active_claim.assert_awaited_once_with(agent_name="derrick")

    @pytest.mark.asyncio
    async def test_db_claim_does_not_override_rate_limited_or_paused(self):
        """Status precedence: if the agent is RATE_LIMITED or STOPPED or
        UNREACHABLE, a DB claim should NOT upgrade those to WORKING —
        those are higher-severity states that need operator attention."""
        cost, monday, loki = _noop_services()
        dispatch_db = _make_dispatch_db(has_claim=True)
        # rate_limited status
        summary = await _fetch_agent_summary(
            _make_agent("devon"),
            _make_health(status_str="rate_limited", active_sessions=0),
            cost, monday, loki,
            dispatch_db=dispatch_db,
        )
        assert summary.status == AgentStatusEnum.RATE_LIMITED, (
            "RATE_LIMITED must NOT be masked by a DB claim — operator needs to see it"
        )
        # busy still True since DB has claim
        assert summary.busy is True
