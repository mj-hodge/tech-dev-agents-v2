"""Integration tests for GET /api/agents/presence — T426-18 through T426-22.

STORY-426: Original tests for SSH-based presence route.
STORY-628: Updated to match fleet-derived route (STORY-549 rewrite).
  The route no longer calls presence_service.get_all_presence().
  It reads agent_service/cost_service/loki_client directly and derives
  PresenceState from AgentStatusEnum via fleet summaries.

All tests call the real FastAPI app via httpx.AsyncClient using the shared
`client` and `unauthed_client` fixtures from conftest.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tech_dev_agents.ops_console.models.responses import (
    AgentStatusEnum,
    FleetAgentSummary,
    PresenceState,
)
from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)

_VALID_STATES = {"working", "idle", "rate_limited", "offline"}


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


def _inject_fleet(app, summaries: list[FleetAgentSummary]):
    """Inject mock fleet services that return the given summaries.

    The route reads agent_service, cost_service, monday_service, loki_client
    from app.state and fans out via _fetch_agent_summary. We patch that
    function to return canned summaries so tests are deterministic.
    """
    agent_service = MagicMock()
    records = []
    for s in summaries:
        rec = MagicMock()
        rec.name = s.name
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
    return summaries


# ---------------------------------------------------------------------------
# T426-18: 200 + correct schema
# ---------------------------------------------------------------------------


class TestPresenceEndpointHappyPath:
    """T426-18–21: Authenticated requests return 200 with correct JSON schema."""

    @pytest.mark.asyncio
    async def test_returns_200_with_json(self, client, app):
        """T426-18: GET /api/agents/presence returns HTTP 200 with application/json."""
        summaries = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
        ]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_response_schema_top_level_fields(self, client, app):
        """T426-20: Response body contains agents[], cached, and checked_at."""
        summaries = [_make_fleet_summary("dan", AgentStatusEnum.ONLINE)]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)
        assert "cached" in data
        assert isinstance(data["cached"], bool)
        assert "checked_at" in data
        # checked_at must be a parseable ISO-8601 string
        datetime.fromisoformat(data["checked_at"])

    @pytest.mark.asyncio
    async def test_agent_entries_have_required_fields(self, client, app):
        """T426-21: Each agent entry has name, state, checked_at, and detail."""
        summaries = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
        ]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        for agent in resp.json()["agents"]:
            assert "name" in agent, "Missing 'name' in agent entry"
            assert "state" in agent, "Missing 'state' in agent entry"
            assert "checked_at" in agent, "Missing 'checked_at' in agent entry"
            assert "detail" in agent, "Missing 'detail' in agent entry"

    @pytest.mark.asyncio
    async def test_agent_state_values_are_valid(self, client, app):
        """T426-22: state field is one of: working, idle, rate_limited, offline."""
        summaries = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
        ]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        for agent in resp.json()["agents"]:
            assert agent["state"] in _VALID_STATES, (
                f"Unexpected state '{agent['state']}' for agent '{agent['name']}'"
            )

    @pytest.mark.asyncio
    async def test_response_contains_all_agents_from_fleet(self, client, app):
        """T426-20b: All enabled agents appear in the response."""
        summaries = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING),
            _make_fleet_summary("derrick", AgentStatusEnum.IDLE),
            _make_fleet_summary("turing", AgentStatusEnum.OFFLINE),
        ]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        names = {a["name"] for a in resp.json()["agents"]}
        assert names == {"dan", "derrick", "turing"}

    @pytest.mark.asyncio
    async def test_cached_is_always_false(self, client, app):
        """T426-20c: Fleet-derived route always returns cached=False (fleet has its own cache)."""
        summaries = [_make_fleet_summary("dan", AgentStatusEnum.ONLINE)]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        assert resp.json()["cached"] is False

    @pytest.mark.asyncio
    async def test_detail_contains_fleet_status(self, client, app):
        """T426-21b: detail field contains fleet-derived info with status= prefix."""
        summaries = [
            _make_fleet_summary("dan", AgentStatusEnum.WORKING, busy=True, current_story="STORY-100"),
        ]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        dan = next(a for a in resp.json()["agents"] if a["name"] == "dan")
        assert dan["detail"] is not None
        assert "status=" in dan["detail"]

    @pytest.mark.asyncio
    async def test_detail_for_offline_agent(self, client, app):
        """T426-21c: Offline agent has detail containing status=offline."""
        summaries = [_make_fleet_summary("turing", AgentStatusEnum.OFFLINE)]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        turing = next(a for a in resp.json()["agents"] if a["name"] == "turing")
        assert turing["state"] == "offline"
        assert "status=offline" in turing["detail"]


# ---------------------------------------------------------------------------
# T426-19: Authentication required
# ---------------------------------------------------------------------------


class TestPresenceEndpointAuth:
    """T426-19: Endpoint requires authentication — 401 without credentials."""

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, unauthed_client, app):
        """T426-19: GET /api/agents/presence without X-API-Key returns 401."""
        resp = await unauthed_client.get("/api/agents/presence")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_api_key_returns_200(self, client, app):
        """T426-19b: Authenticated request (X-API-Key) returns 200."""
        # No fleet services injected → agent_service is None → empty list
        resp = await client.get("/api/agents/presence")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_api_key_returns_401(self, app):
        """T426-19c: Wrong API key returns 401."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as bad_client:
            bad_client.headers["X-API-Key"] = "this-is-wrong"
            resp = await bad_client.get("/api/agents/presence")

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# T426-22: All four states representable in a single fleet response
# ---------------------------------------------------------------------------


class TestPresenceStateEnumCoverage:
    """T426-22: All four PresenceState values round-trip through the API."""

    @pytest.mark.asyncio
    async def test_all_four_states_present_in_response(self, client, app):
        """T426-22: A fleet with agents in all four states returns all four state values."""
        summaries = [
            _make_fleet_summary("a1", AgentStatusEnum.WORKING),
            _make_fleet_summary("a2", AgentStatusEnum.IDLE),
            _make_fleet_summary("a3", AgentStatusEnum.RATE_LIMITED),
            _make_fleet_summary("a4", AgentStatusEnum.OFFLINE),
        ]
        with patch(
            "tech_dev_agents.ops_console.routes.fleet._fetch_agent_summary",
            new_callable=AsyncMock,
            side_effect=summaries,
        ):
            _inject_fleet(app, summaries)
            resp = await client.get("/api/agents/presence")

        assert resp.status_code == 200
        returned_states = {a["state"] for a in resp.json()["agents"]}
        assert returned_states == {"working", "idle", "rate_limited", "offline"}
