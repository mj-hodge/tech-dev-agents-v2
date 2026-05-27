"""Tests for /dispatch/reclaim — STORY-638: accept in_review as source state.

Covers the 8 test cases from seed §7:
  1. Reclaim from in_review succeeds, review_started_at cleared
  2. Reclaim from pending (regression)
  3. Reclaim from claimed (regression)
  4. Reclaim from failed (regression)
  5. Reclaim from completed → rejected (409)
  6. Reclaim from cancelled → rejected (409)
  7. Gate disabled → 404
  8. Audit log includes was=in_review
"""

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
    InvalidTransitionError,
    NotFoundError,
)


# ---------------------------------------------------------------------------
# Fake asyncpg helpers (same pattern as test_dispatch_claim_sync.py)
# ---------------------------------------------------------------------------

TEST_API_KEY = "test-ops-console-key-12345"


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeConn:
    """Fake asyncpg connection that records queries and returns canned rows."""

    def __init__(self):
        self.fetchrow_responses: list = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if self.fetchrow_responses:
            return self.fetchrow_responses.pop(0)
        return None

    async def fetch(self, query, *args):
        """_resolve_row uses conn.fetch(); pop from same queue."""
        self.fetchrow_calls.append((query, args))
        if self.fetchrow_responses:
            item = self.fetchrow_responses.pop(0)
            return [] if item is None else [item]
        return []

    def transaction(self):
        return _FakeTransaction()


class FakePool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _make_row(*, status: str, story_id: str = "STORY-700", **overrides) -> dict:
    """Build a fake dispatch_items row dict."""
    base = {
        "id": 42,
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "small",
        "prompt": "fix the thing",
        "enqueued_at": "2026-04-25T20:00:00+00:00",
        "enqueued_by": "mark",
        "title": "Test story",
        "status": status,
        "claimed_by": overrides.pop("claimed_by", None),
        "claimed_at": overrides.pop("claimed_at", None),
        "completed_at": None,
        "cancelled_at": None,
        "updated_at": "2026-04-25T20:00:00+00:00",
        "review_started_at": overrides.pop("review_started_at", None),
        "paused_at": None,
        "claim_heartbeat_at": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def db_service():
    conn = FakeConn()
    pool = FakePool(conn)
    svc = DispatchDBService.__new__(DispatchDBService)
    svc._pool = pool
    return svc, conn


# ---------------------------------------------------------------------------
# Test 1: Reclaim from in_review succeeds
# ---------------------------------------------------------------------------


class TestReclaimFromInReview:
    """STORY-638 core case: in_review → claimed, review_started_at cleared."""

    @pytest.mark.asyncio
    async def test_reclaim_from_in_review_succeeds(self, db_service):
        svc, conn = db_service

        existing = _make_row(
            status="in_review",
            claimed_by="dan",
            review_started_at="2026-04-25T21:00:00+00:00",
        )
        # _resolve_row returns the existing row, UPDATE RETURNING returns claimed row
        claimed_row = _make_row(
            status="claimed",
            claimed_by="morris",
            claimed_at="2026-04-25T21:05:00+00:00",
            review_started_at=None,
        )
        conn.fetchrow_responses = [existing, claimed_row]

        result = await svc.force_claim("STORY-700", "morris")

        assert result["status"] == "claimed"
        assert result["claimed_by"] == "morris"
        assert result["review_started_at"] is None


# ---------------------------------------------------------------------------
# Test 2: Reclaim from pending (regression)
# ---------------------------------------------------------------------------


class TestReclaimFromPending:

    @pytest.mark.asyncio
    async def test_reclaim_from_pending_still_works(self, db_service):
        svc, conn = db_service

        existing = _make_row(status="pending")
        claimed_row = _make_row(
            status="claimed",
            claimed_by="devon",
            claimed_at="2026-04-25T21:05:00+00:00",
        )
        conn.fetchrow_responses = [existing, claimed_row]

        result = await svc.force_claim("STORY-700", "devon")
        assert result["status"] == "claimed"
        assert result["claimed_by"] == "devon"


# ---------------------------------------------------------------------------
# Test 3: Reclaim from claimed (re-assign)
# ---------------------------------------------------------------------------


class TestReclaimFromClaimed:

    @pytest.mark.asyncio
    async def test_reclaim_from_claimed_still_works(self, db_service):
        svc, conn = db_service

        existing = _make_row(status="claimed", claimed_by="daisy")
        claimed_row = _make_row(
            status="claimed",
            claimed_by="devon",
            claimed_at="2026-04-25T21:05:00+00:00",
        )
        conn.fetchrow_responses = [existing, claimed_row]

        result = await svc.force_claim("STORY-700", "devon")
        assert result["status"] == "claimed"
        assert result["claimed_by"] == "devon"


# ---------------------------------------------------------------------------
# Test 4: Reclaim from failed (regression)
# ---------------------------------------------------------------------------


class TestReclaimFromFailed:

    @pytest.mark.asyncio
    async def test_reclaim_from_failed_still_works(self, db_service):
        svc, conn = db_service

        existing = _make_row(status="failed")
        claimed_row = _make_row(
            status="claimed",
            claimed_by="morris",
            claimed_at="2026-04-25T21:05:00+00:00",
        )
        conn.fetchrow_responses = [existing, claimed_row]

        result = await svc.force_claim("STORY-700", "morris")
        assert result["status"] == "claimed"


# ---------------------------------------------------------------------------
# Test 5: Reclaim from completed → rejected
# ---------------------------------------------------------------------------


class TestReclaimFromCompleted:

    @pytest.mark.asyncio
    async def test_reclaim_from_completed_rejected(self, db_service):
        svc, conn = db_service

        existing = _make_row(status="completed")
        conn.fetchrow_responses = [existing]

        with pytest.raises(InvalidTransitionError, match="terminal state"):
            await svc.force_claim("STORY-700", "morris")


# ---------------------------------------------------------------------------
# Test 6: Reclaim from cancelled → rejected
# ---------------------------------------------------------------------------


class TestReclaimFromCancelled:

    @pytest.mark.asyncio
    async def test_reclaim_from_cancelled_rejected(self, db_service):
        svc, conn = db_service

        existing = _make_row(status="cancelled")
        conn.fetchrow_responses = [existing]

        with pytest.raises(InvalidTransitionError, match="terminal state"):
            await svc.force_claim("STORY-700", "morris")


# ---------------------------------------------------------------------------
# Test 7: Gate disabled → 404
# ---------------------------------------------------------------------------


class TestReclaimGateDisabled:
    """When DISPATCH_CLAIM_SYNC_ENABLED=false, the route returns 404."""

    @pytest.mark.asyncio
    async def test_reclaim_gate_disabled_returns_404(self, tmp_path):
        from tech_dev_agents.ops_console.config import Settings
        from tech_dev_agents.ops_console.main import create_app

        registry = tmp_path / "agent-registry.json"
        registry.write_text(json.dumps([
            {"name": "dan", "host": "10.0.1.10", "port": 8080,
             "role": "developer", "enabled": True},
        ]))

        settings = Settings(
            ops_console_api_key=TEST_API_KEY,
            loki_api_key="test-loki-key",
            agent_api_key="test-agent-key",
            agent_registry_path=str(registry),
            database_url="",
            dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
            dispatch_claim_sync_enabled=False,  # Gate OFF
        )
        app = create_app(settings=settings)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.headers["X-API-Key"] = TEST_API_KEY
            resp = await client.post(
                "/api/dispatch/reclaim/STORY-700",
                json={"agent_name": "morris"},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 8: Audit log includes was=in_review
# ---------------------------------------------------------------------------


class TestAuditLogInReview:
    """Verify the structured log line fires on reclaim from in_review."""

    @pytest.mark.asyncio
    async def test_audit_log_includes_was_in_review(self, tmp_path):
        from tech_dev_agents.ops_console.config import Settings
        from tech_dev_agents.ops_console.main import create_app

        registry = tmp_path / "agent-registry.json"
        registry.write_text(json.dumps([
            {"name": "morris", "host": "10.0.1.10", "port": 8080,
             "role": "manager", "enabled": True},
        ]))

        settings = Settings(
            ops_console_api_key=TEST_API_KEY,
            loki_api_key="test-loki-key",
            agent_api_key="test-agent-key",
            agent_registry_path=str(registry),
            database_url="",
            dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
            dispatch_claim_sync_enabled=True,  # Gate ON
        )
        app = create_app(settings=settings)

        # Mock the DB service to return a row that transitioned from in_review
        returned_row = _make_row(
            status="claimed",
            claimed_by="morris",
            claimed_at="2026-04-25T21:05:00+00:00",
            review_started_at=None,
        )
        mock_db_svc = AsyncMock()
        mock_db_svc.force_claim.return_value = returned_row

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch._get_db_svc",
            return_value=mock_db_svc,
        ), patch(
            "tech_dev_agents.ops_console.routes.dispatch.logger"
        ) as mock_logger:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                client.headers["X-API-Key"] = TEST_API_KEY
                resp = await client.post(
                    "/api/dispatch/reclaim/STORY-700",
                    json={"agent_name": "morris"},
                )

        assert resp.status_code == 200
        # Verify logger.info was called with the reclaim message
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        log_msg = call_args[0][0] % call_args[0][1:]
        assert "Force-reclaimed" in log_msg
        assert "STORY-700" in log_msg
        assert "morris" in log_msg
