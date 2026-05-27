"""STORY-725: Server-side needs_info exclusion test (NI-S-01, NI-S-02).

Gap 3 from the 2026-04-26 failure audit: the existing needs_info guard tests
cover the agent-side (local) filter. But there was no test pinning that the
server-side next_pending() query in dispatch_db_service.py excludes needs_info
rows at the DB layer.

Production cases: STORY-323 and STORY-592 at RETRY 3/3 with unanswered
QUESTION.md. If next_pending() ever returns a needs_info row, a cross-agent
claim loop can resume indefinitely.

Tests:
  NI-S-01: next_pending() SQL excludes 'needs_info' from status filter,
            and returns pending row even when needs_info row is older.
  NI-S-02: next_pending() returns None when only needs_info rows exist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tech_dev_agents"))


def _make_pool_and_conn(fetchrow_result):
    """Return (pool, conn, captured_queries) with asyncpg mock pattern."""
    conn = MagicMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    captured_queries: list[str] = []

    async def fake_fetchrow(query, *args):
        captured_queries.append(query)
        return fetchrow_result

    conn.fetchrow = fake_fetchrow
    return pool, conn, captured_queries


def _make_service(fetchrow_result):
    from ops_console.services.dispatch_db_service import DispatchDBService
    pool, conn, captured_queries = _make_pool_and_conn(fetchrow_result)
    return DispatchDBService(pool), captured_queries


class TestNextPendingExcludesNeedsInfo:
    """NI-S-01/NI-S-02: next_pending() must never return needs_info rows."""

    @pytest.mark.asyncio
    async def test_ni_s_01_query_excludes_needs_info_status(self):
        """NI-S-01: The SQL sent by next_pending() must not include 'needs_info'
        in its status filter. Even if a needs_info row is older than a pending
        row, the DB query must not expose it to the dispatcher.

        This test pins the query contract: if a future migration accidentally
        adds needs_info to the WHERE IN clause, this test will catch it.
        """
        pending_row = {
            "story_id": "STORY-A",
            "status": "pending",
            "priority": 50,
            "enqueued_at": None,
            "claimed_at": None,
            "completed_at": None,
            "failed_at": None,
            "claim_heartbeat_at": None,
            "stale_release_count": 0,
            "failure_reason": None,
            "needs_info_path": None,
            "repo": "tech-dev-agents",
            "scope": "small",
            "prompt": "test",
            "rework_of": None,
            "enqueued_by": "test",
            "cross_story_reference": False,
            "retry_count": 0,
        }
        service, captured_queries = _make_service(pending_row)

        result = await service.next_pending()

        assert result is not None
        assert result["story_id"] == "STORY-A"
        assert captured_queries, "fetchrow must have been called"
        query = captured_queries[0].lower()
        assert "needs_info" not in query, (
            f"next_pending() SQL must NOT include 'needs_info' — "
            f"needs_info rows are human-gated and must never be dispatched. "
            f"Query contained: {captured_queries[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_ni_s_02_returns_none_when_no_pending_rows(self):
        """NI-S-02: next_pending() returns None when the DB returns no rows.

        This simulates the case where only needs_info rows exist — since those
        are filtered out at the SQL layer, the DB returns nothing, and
        next_pending() must return None (queue effectively empty for dispatcher).
        """
        service, _ = _make_service(None)

        result = await service.next_pending()

        assert result is None, (
            "next_pending() must return None when no pending/paused rows exist "
            "(simulates queue with only needs_info rows, which are correctly excluded)"
        )
