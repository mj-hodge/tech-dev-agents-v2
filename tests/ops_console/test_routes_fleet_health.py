"""Tests for GET /api/health/fleet — T51-T66 (STORY-395).

Fleet health monitoring endpoint: lightweight, unauthenticated, machine-readable.
All tests are in RED state until Phase 8 adds the route to routes/health.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tech_dev_agents.agent_dashboard import AgentHealthSnapshot
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tests.ops_console.conftest import inject_mock_services

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).isoformat()
_LAST_ACTIVITY = "2026-04-18T12:00:00+00:00"


def _snap(
    name: str,
    status: str,
    last_activity: str | None = _NOW,
) -> AgentHealthSnapshot:
    """Build an AgentHealthSnapshot with an explicit status (bypasses classifier)."""
    return AgentHealthSnapshot(
        agent_name=name,
        status=AgentActivityStatus(status),
        last_activity=last_activity,
        uptime_seconds=3600,
        active_sessions=1,
        error_count=0,
        checked_at=_NOW,
    )


def _mock_dispatch(pending: int = 2, claimed: int = 1) -> MagicMock:
    """Build a mock dispatch_db_service with configurable queue counts."""
    pending_items = [{"story_id": f"STORY-{i:03d}", "status": "pending"} for i in range(pending)]
    claimed_items = [{"story_id": f"STORY-C{i:03d}", "status": "claimed"} for i in range(claimed)]

    svc = MagicMock()
    svc.list_queue = AsyncMock(return_value={"pending": pending_items, "claimed": claimed_items})
    svc.pending_count = AsyncMock(return_value=pending)
    return svc


def _inject(app, agent_snapshots=None, pending=2, claimed=1):
    """Inject agent + dispatch mocks into app.state."""
    if agent_snapshots is None:
        agent_snapshots = [
            _snap("dan", "online"),
            _snap("derrick", "idle"),
        ]

    agent_service = MagicMock()
    agent_service.get_all_health = AsyncMock(return_value=agent_snapshots)

    dispatch_service = _mock_dispatch(pending=pending, claimed=claimed)

    inject_mock_services(
        app,
        agent_service=agent_service,
        dispatch_db_service=dispatch_service,
        started_at=datetime.now(timezone.utc),
    )
    return agent_service, dispatch_service


# ---------------------------------------------------------------------------
# T51 — T52: Healthy fleet basic contract
# ---------------------------------------------------------------------------


class TestFleetHealthHappyPath:
    """T51-T52: 200, JSON body, schema structure."""

    @pytest.mark.asyncio
    async def test_healthy_fleet_returns_200_and_json(
        self, client, app
    ):
        """T51: Healthy fleet → HTTP 200, application/json, status=healthy, checked_at present."""
        _inject(app)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")
        data = resp.json()
        assert data["status"] == "healthy"
        assert "checked_at" in data
        # checked_at must be parseable as ISO 8601
        datetime.fromisoformat(data["checked_at"])

    @pytest.mark.asyncio
    async def test_healthy_fleet_response_schema(self, client, app):
        """T52: Response has agents list, queue dict with int counts, and top-level status."""
        _inject(app, pending=0, claimed=0)

        resp = await client.get("/api/health/fleet")
        assert resp.status_code == 200

        data = resp.json()
        assert isinstance(data["agents"], list)
        assert isinstance(data["queue"], dict)
        assert isinstance(data["queue"]["pending"], int)
        assert isinstance(data["queue"]["claimed"], int)
        assert data["status"] in ("healthy", "degraded")


# ---------------------------------------------------------------------------
# T53: No authentication required
# ---------------------------------------------------------------------------


class TestFleetHealthNoAuth:
    """T53: Endpoint is public — no X-API-Key needed."""

    @pytest.mark.asyncio
    async def test_no_auth_required(self, unauthed_client, app):
        """T53: GET /api/health/fleet without X-API-Key header returns 200."""
        _inject(app)

        resp = await unauthed_client.get("/api/health/fleet")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# T54 — T56, T65, T66: Agent entries and queue field correctness
# ---------------------------------------------------------------------------


class TestFleetHealthResponseFields:
    """T54-T56, T65-T66: Field values match injected service data."""

    @pytest.mark.asyncio
    async def test_agent_entries_have_required_fields(self, client, app):
        """T54: Each agent entry has name, status, and last_seen."""
        _inject(app)

        resp = await client.get("/api/health/fleet")
        assert resp.status_code == 200

        for agent in resp.json()["agents"]:
            assert "name" in agent
            assert "status" in agent
            assert agent["status"] in ("online", "idle", "stuck", "offline")
            assert "last_seen" in agent  # may be null

    @pytest.mark.asyncio
    async def test_last_seen_is_null_for_agent_with_no_last_activity(
        self, client, app
    ):
        """T55: Agent with last_activity=None must have last_seen=null in response."""
        snapshots = [
            _snap("dan", "offline", last_activity=None),
        ]
        _inject(app, agent_snapshots=snapshots)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503  # offline agent → degraded
        agents = resp.json()["agents"]
        dan = next(a for a in agents if a["name"] == "dan")
        assert dan["last_seen"] is None

    @pytest.mark.asyncio
    async def test_queue_counts_reflect_dispatch_service(self, client, app):
        """T56: queue.pending and queue.claimed match list_queue() list lengths."""
        _inject(app, pending=3, claimed=1)

        resp = await client.get("/api/health/fleet")
        assert resp.status_code == 200

        queue = resp.json()["queue"]
        assert queue["pending"] == 3
        assert queue["claimed"] == 1

    @pytest.mark.asyncio
    async def test_agent_names_and_statuses_are_correct(self, client, app):
        """T65: Agent name and status are passed through correctly."""
        snapshots = [
            _snap("dan", "online"),
            _snap("derrick", "idle"),
        ]
        _inject(app, agent_snapshots=snapshots)

        resp = await client.get("/api/health/fleet")
        assert resp.status_code == 200

        agents = {a["name"]: a for a in resp.json()["agents"]}
        assert agents["dan"]["status"] == "online"
        assert agents["derrick"]["status"] == "idle"

    @pytest.mark.asyncio
    async def test_last_seen_matches_last_activity(self, client, app):
        """T66: last_seen is the exact last_activity value from the snapshot."""
        snapshots = [
            _snap("dan", "online", last_activity=_LAST_ACTIVITY),
        ]
        _inject(app, agent_snapshots=snapshots)

        resp = await client.get("/api/health/fleet")
        assert resp.status_code == 200

        agents = {a["name"]: a for a in resp.json()["agents"]}
        assert agents["dan"]["last_seen"] == _LAST_ACTIVITY


# ---------------------------------------------------------------------------
# T57 — T61: Degraded conditions
# ---------------------------------------------------------------------------


class TestFleetHealthDegradedConditions:
    """T57-T61: 503 + status=degraded under various degraded conditions."""

    @pytest.mark.asyncio
    async def test_stuck_is_healthy_when_queue_empty(self, client, app):
        """T57: STUCK with no queue work → HTTP 200, status=healthy."""
        snapshots = [
            _snap("dan", "stuck"),
            _snap("derrick", "online"),
        ]
        _inject(app, agent_snapshots=snapshots, pending=0, claimed=0)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
        agents = {a["name"]: a for a in resp.json()["agents"]}
        assert agents["dan"]["status"] == "idle"

    @pytest.mark.asyncio
    async def test_degraded_when_agent_is_stuck_and_queue_has_work(self, client, app):
        """T57b: STUCK + pending/claimed work → HTTP 503, status=degraded."""
        snapshots = [
            _snap("dan", "stuck"),
            _snap("derrick", "online"),
        ]
        _inject(app, agent_snapshots=snapshots, pending=1, claimed=0)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_when_agent_is_offline(self, client, app):
        """T58: Any OFFLINE agent → HTTP 503, status=degraded."""
        snapshots = [
            _snap("dan", "offline"),
            _snap("derrick", "online"),
        ]
        _inject(app, agent_snapshots=snapshots)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_when_queue_pending_exceeds_20(self, client, app):
        """T59: queue.pending > 20 → HTTP 503, status=degraded."""
        _inject(app, pending=21, claimed=0)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_healthy_at_exactly_20_pending(self, client, app):
        """T60: queue.pending == 20 → HTTP 200, status=healthy (boundary: > 20 triggers degraded)."""
        _inject(app, pending=20, claimed=0)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_response_body_is_complete(self, client, app):
        """T61: 503 response still contains agents, queue, and checked_at."""
        snapshots = [_snap("dan", "stuck")]
        _inject(app, agent_snapshots=snapshots)

        resp = await client.get("/api/health/fleet")
        assert resp.status_code == 503

        data = resp.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)
        assert "queue" in data
        assert isinstance(data["queue"], dict)
        assert "checked_at" in data


# ---------------------------------------------------------------------------
# T62 — T64: Error resilience (never return 500)
# ---------------------------------------------------------------------------


class TestFleetHealthErrorResilience:
    """T62-T64: Service failures return structured JSON, never 500."""

    @pytest.mark.asyncio
    async def test_agent_service_failure_returns_empty_agents_and_503(
        self, client, app
    ):
        """T62: AgentService.get_all_health() raises → agents=[], status=degraded, HTTP 503."""
        agent_service = MagicMock()
        agent_service.get_all_health = AsyncMock(side_effect=RuntimeError("connection refused"))

        dispatch_service = _mock_dispatch(pending=2, claimed=0)

        inject_mock_services(
            app,
            agent_service=agent_service,
            dispatch_db_service=dispatch_service,
            started_at=datetime.now(timezone.utc),
        )

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        data = resp.json()
        assert data["agents"] == []
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_dispatch_service_failure_returns_sentinel_and_503(
        self, client, app
    ):
        """T63: dispatch_db_service.list_queue() raises → queue={pending:-1, claimed:-1}, 503."""
        snapshots = [_snap("dan", "online")]
        agent_service = MagicMock()
        agent_service.get_all_health = AsyncMock(return_value=snapshots)

        dispatch_service = MagicMock()
        dispatch_service.list_queue = AsyncMock(side_effect=OSError("disk error"))
        dispatch_service.pending_count = AsyncMock(side_effect=OSError("disk error"))

        inject_mock_services(
            app,
            agent_service=agent_service,
            dispatch_db_service=dispatch_service,
            started_at=datetime.now(timezone.utc),
        )

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        data = resp.json()
        assert data["queue"]["pending"] == -1
        assert data["queue"]["claimed"] == -1
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_both_services_fail_returns_full_degraded_response(
        self, client, app
    ):
        """T64: Both services raise → agents=[], queue sentinel, status=degraded, HTTP 503."""
        agent_service = MagicMock()
        agent_service.get_all_health = AsyncMock(side_effect=Exception("timeout"))

        dispatch_service = MagicMock()
        dispatch_service.list_queue = AsyncMock(side_effect=Exception("db gone"))
        dispatch_service.pending_count = AsyncMock(side_effect=Exception("db gone"))

        inject_mock_services(
            app,
            agent_service=agent_service,
            dispatch_db_service=dispatch_service,
            started_at=datetime.now(timezone.utc),
        )

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        data = resp.json()
        assert data["agents"] == []
        assert data["queue"] == {"pending": -1, "claimed": -1}
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_endpoint_never_returns_500(self, client, app):
        """T62b: Service failures must not propagate as unhandled 500 responses."""
        agent_service = MagicMock()
        agent_service.get_all_health = AsyncMock(side_effect=ValueError("unexpected"))

        dispatch_service = MagicMock()
        dispatch_service.list_queue = AsyncMock(side_effect=ValueError("unexpected"))
        dispatch_service.pending_count = AsyncMock(side_effect=ValueError("unexpected"))

        inject_mock_services(
            app,
            agent_service=agent_service,
            dispatch_db_service=dispatch_service,
            started_at=datetime.now(timezone.utc),
        )

        resp = await client.get("/api/health/fleet")

        # Must return 503 (degraded) not 500 (unhandled exception)
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


class TestFleetHealthEdgeCases:
    """Edge cases: empty fleet, all idle, boundary thresholds."""

    @pytest.mark.asyncio
    async def test_empty_fleet_is_healthy_with_low_queue(self, client, app):
        """Empty agent list + pending ≤ 20 → healthy, HTTP 200."""
        agent_service = MagicMock()
        agent_service.get_all_health = AsyncMock(return_value=[])

        dispatch_service = _mock_dispatch(pending=0, claimed=0)

        inject_mock_services(
            app,
            agent_service=agent_service,
            dispatch_db_service=dispatch_service,
            started_at=datetime.now(timezone.utc),
        )

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"] == []
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_all_idle_agents_is_healthy(self, client, app):
        """All agents IDLE (none ONLINE) → status=healthy, HTTP 200."""
        snapshots = [
            _snap("dan", "idle"),
            _snap("derrick", "idle"),
        ]
        _inject(app, agent_snapshots=snapshots, pending=0)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_pending_boundary_21_is_degraded(self, client, app):
        """pending == 21 (boundary+1) → HTTP 503, degraded."""
        _inject(app, pending=21)

        resp = await client.get("/api/health/fleet")

        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
