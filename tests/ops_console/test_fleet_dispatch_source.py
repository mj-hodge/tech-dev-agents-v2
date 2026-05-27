"""STORY-737: Fleet overview sourced from dispatch queue DB.

Tests verify that GET /api/fleet reads stories_in_progress and per-agent
current_story from DispatchDBService instead of Monday.com/Loki, and that
Monday.com and Loki are NOT called on the fleet hot path.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.routes.fleet import fleet_overview


# ---------------------------------------------------------------------------
# Helpers — build a fake Request with all required app.state services
# ---------------------------------------------------------------------------


def _make_agent(name: str, enabled: bool = True, role: str = "developer"):
    return SimpleNamespace(name=name, enabled=enabled, role=role)


def _make_health(agent_name: str, active_sessions: int = 0):
    return SimpleNamespace(
        agent_name=agent_name,
        last_activity=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=1000,
        active_sessions=active_sessions,
        error_count=0,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_cost():
    return SimpleNamespace(
        azure_cost_usd=1.0,
        foundry_cost_usd=0.8,
        sdk_cost_usd=0.1,
        openai_cost_usd=0.1,
        total_cost_usd=1.0,
    )


def _build_request(
    agents: list | None = None,
    dispatch_active_count: int = 0,
    dispatch_claimed_map: dict | None = None,
    health_snapshots: list | None = None,
):
    """Build a fake Request object with mocked services.

    dispatch_claimed_map: {agent_name: dict | None} for get_claimed_by results.
    """
    if agents is None:
        agents = [_make_agent("cole"), _make_agent("devon")]
    if dispatch_claimed_map is None:
        dispatch_claimed_map = {}

    agent_service = MagicMock()
    agent_service.get_registry.return_value = agents
    if health_snapshots is None:
        health_snapshots = [_make_health(a.name) for a in agents]
    agent_service.get_all_health = AsyncMock(return_value=health_snapshots)

    cost_service = MagicMock()
    cost_service.get_today_cost = AsyncMock(return_value=_make_cost())
    cost_service.get_fleet_daily_spend = AsyncMock(return_value=10.0)
    cost_service.get_fleet_monthly_spend = AsyncMock(return_value=200.0)

    alert_service = MagicMock()
    alert_service.get_active_anomalies = AsyncMock(return_value=[])

    # Monday.com — should NOT be called on fleet hot path (SC-5)
    monday_service = MagicMock()
    monday_service.get_stories_in_progress = AsyncMock(return_value=99)
    monday_service.get_current_story = AsyncMock(return_value=None)

    # Loki — should NOT be called on fleet hot path (SC-5)
    loki_client = MagicMock()
    loki_client.query_current_work = AsyncMock(return_value=None)
    loki_client.get_agent_queue = AsyncMock(return_value=None)

    # Dispatch DB — the new source of truth (STORY-737)
    dispatch_db = MagicMock()
    dispatch_db.count_active_stories = AsyncMock(return_value=dispatch_active_count)

    async def _get_claimed_by(agent_name: str):
        return dispatch_claimed_map.get(agent_name)

    dispatch_db.get_claimed_by = AsyncMock(side_effect=_get_claimed_by)

    app_state = SimpleNamespace(
        agent_service=agent_service,
        cost_service=cost_service,
        monday_service=monday_service,
        alert_service=alert_service,
        loki_client=loki_client,
        dispatch_db_service=dispatch_db,
    )
    request = MagicMock()
    request.app.state = app_state
    return request


# ---------------------------------------------------------------------------
# T737-04: stories_in_progress from dispatch DB (SC-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stories_in_progress_from_dispatch_db():
    """SC-1: stories_in_progress equals count of active dispatch items."""
    request = _build_request(dispatch_active_count=3)
    response = await fleet_overview(request)
    assert response.stories_in_progress == 3


# ---------------------------------------------------------------------------
# T737-05: stories_in_progress = 0 when dispatch queue empty (SC-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stories_in_progress_zero_when_queue_empty():
    """SC-3: stories_in_progress = 0 when dispatch queue has no active rows,
    regardless of Monday.com state (Monday returns 99 but is not called)."""
    request = _build_request(dispatch_active_count=0)
    response = await fleet_overview(request)
    assert response.stories_in_progress == 0


# ---------------------------------------------------------------------------
# T737-06: Per-agent current_story from dispatch DB (SC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_current_story_from_dispatch():
    """SC-2: Each agent's current_story reflects actual claimed dispatch item."""
    agents = [_make_agent("cole"), _make_agent("devon")]
    claimed_map = {
        "cole": {
            "story_id": "STORY-100",
            "status": "claimed",
            "repo": "tech-dev-agents",
        },
        # devon has no active claim
    }
    request = _build_request(
        agents=agents,
        dispatch_active_count=1,
        dispatch_claimed_map=claimed_map,
    )
    response = await fleet_overview(request)

    agent_map = {a.name: a for a in response.agents}
    assert agent_map["cole"].current_story == "STORY-100"
    assert agent_map["devon"].current_story is None


# ---------------------------------------------------------------------------
# T737-07: Monday.com not called on fleet hot path (SC-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monday_not_called_on_fleet_path():
    """SC-5: monday_service methods are NOT invoked on the fleet hot path."""
    request = _build_request(dispatch_active_count=2)
    await fleet_overview(request)

    monday = request.app.state.monday_service
    monday.get_stories_in_progress.assert_not_called()
    monday.get_current_story.assert_not_called()


# ---------------------------------------------------------------------------
# T737-08: Loki not called on fleet hot path (SC-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loki_not_called_on_fleet_path():
    """SC-5: loki_client methods are NOT invoked on the fleet hot path."""
    request = _build_request(dispatch_active_count=2)
    await fleet_overview(request)

    loki = request.app.state.loki_client
    loki.query_current_work.assert_not_called()
    loki.get_agent_queue.assert_not_called()


# ---------------------------------------------------------------------------
# T737-09: Existing fleet response shape regression (SC-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fleet_response_shape_preserved():
    """SC-6: Fleet response shape is preserved with new data source."""
    agents = [
        _make_agent("cole"),
        _make_agent("devon"),
        _make_agent("ellis"),
    ]
    request = _build_request(
        agents=agents,
        dispatch_active_count=2,
        dispatch_claimed_map={
            "cole": {"story_id": "STORY-100", "status": "claimed"},
        },
    )
    response = await fleet_overview(request)

    # Top-level fields
    assert isinstance(response.total_daily_spend_usd, float)
    assert isinstance(response.total_monthly_spend_usd, float)
    assert isinstance(response.active_agents, int)
    assert isinstance(response.busy_agents, int)
    assert isinstance(response.total_agents, int)
    assert response.total_agents == 3
    assert isinstance(response.online_agents, int)
    assert isinstance(response.idle_agents, int)
    assert isinstance(response.stuck_agents, int)
    assert isinstance(response.offline_agents, int)
    assert isinstance(response.stories_in_progress, int)
    assert response.stories_in_progress == 2
    assert 0.0 <= response.fleet_health_score <= 1.0
    assert isinstance(response.active_alerts, int)
    assert isinstance(response.fetched_at, str)
    assert len(response.agents) == 3

    # Agent summary fields
    for agent in response.agents:
        assert hasattr(agent, "name")
        assert hasattr(agent, "status")
        assert hasattr(agent, "busy")
        assert hasattr(agent, "current_story")
        assert hasattr(agent, "today_cost_usd")
        assert hasattr(agent, "today_foundry_usd")
        assert hasattr(agent, "today_sdk_usd")
        assert hasattr(agent, "today_openai_usd")
        assert hasattr(agent, "queued_stories")
        assert isinstance(agent.queued_stories, list)


# ---------------------------------------------------------------------------
# T737-10: dispatch_db error gracefully degrades stories_in_progress to 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_db_error_gracefully_degrades():
    """stories_in_progress falls back to 0 on dispatch DB error."""
    request = _build_request(dispatch_active_count=0)
    request.app.state.dispatch_db_service.count_active_stories = AsyncMock(
        side_effect=Exception("DB connection lost")
    )
    response = await fleet_overview(request)
    assert response.stories_in_progress == 0


@pytest.mark.asyncio
async def test_stuck_normalized_to_idle_when_no_active_dispatch_work():
    """When dispatch has zero active stories, stale STUCK should render as IDLE."""
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    health_snapshots = [
        SimpleNamespace(
            agent_name="cole",
            status=AgentActivityStatus.STUCK,
            last_activity=stale,
            uptime_seconds=1000,
            active_sessions=0,
            error_count=0,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
    ]
    request = _build_request(
        agents=[_make_agent("cole")],
        dispatch_active_count=0,
        health_snapshots=health_snapshots,
    )

    response = await fleet_overview(request)

    assert response.stories_in_progress == 0
    assert response.stuck_agents == 0
    assert response.idle_agents >= 1
    assert response.agents[0].status.value == "idle"
