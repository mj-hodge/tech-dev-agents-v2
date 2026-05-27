"""Tests for DispatchDBService — PostgreSQL-backed dispatch queue.

STORY-028: Dispatch Queue Database Persistence & History
Phase 7: RED state — tests written before implementation.

Requires: PostgreSQL running locally with ops_console_test database.
Run migration first: psql -d ops_console_test -f scripts/migrations/001_dispatch_queue.sql
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    DispatchDBService,
    DuplicateDispatchError,
    InvalidTransitionError,
    NotFoundError,
)

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


def _pg_is_reachable() -> bool:
    """Check if the test PostgreSQL database is reachable."""
    import asyncio as _asyncio

    async def _check():
        try:
            conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return _asyncio.run(_check())
    except Exception:
        return False


_PG_AVAILABLE = _pg_is_reachable()
pytestmark = pytest.mark.skipif(not _PG_AVAILABLE, reason="PostgreSQL not reachable")


@pytest_asyncio.fixture
async def db_pool():
    """Create a connection pool to the test database, truncate tables between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def svc(db_pool) -> DispatchDBService:
    """DispatchDBService backed by the test pool."""
    return DispatchDBService(db_pool)


def _enqueue_kwargs(
    story_id: str = "STORY-094",
    repo: str = "advertising-amazon",
    scope: str = "small",
    prompt: str = "Start Phase 7",
    enqueued_by: str = "mark",
) -> dict:
    return {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "enqueued_by": enqueued_by,
    }


class TestEnqueue:
    """T01-T03: enqueue method."""

    @pytest.mark.asyncio
    async def test_enqueue_inserts_pending_row(self, svc: DispatchDBService, db_pool):
        """T01: enqueue creates a row with status=pending (AC-1)."""
        row = await svc.enqueue(**_enqueue_kwargs())

        assert row["story_id"] == "STORY-094"
        assert row["status"] == "pending"
        assert row["repo"] == "advertising-amazon"
        assert row["enqueued_by"] == "mark"
        assert row["claimed_by"] is None
        assert row["completed_at"] is None
        assert row["cancelled_at"] is None

        # Verify in database
        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM dispatch_items WHERE story_id = 'STORY-094'"
            )
        assert count == 1

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_active_raises(self, svc: DispatchDBService):
        """T02: Duplicate active story_id raises DuplicateDispatchError (AC-9)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-001"))

        with pytest.raises(DuplicateDispatchError):
            await svc.enqueue(**_enqueue_kwargs("STORY-001"))

    @pytest.mark.asyncio
    async def test_enqueue_after_completion_succeeds(self, svc: DispatchDBService):
        """T03: Same story_id can be re-enqueued after completion (AC-9, partial index)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        await svc.complete("STORY-001")

        # Should succeed because partial unique index only covers pending/claimed
        row = await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        assert row["status"] == "pending"


class TestEnqueueTitle:
    """T03b-T03d: STORY-034 — title field in enqueue."""

    @pytest.mark.asyncio
    async def test_enqueue_with_title(self, svc: DispatchDBService):
        """T03b: enqueue with title persists it."""
        row = await svc.enqueue(**_enqueue_kwargs("STORY-034"), title="Dispatch Titles")

        assert row["title"] == "Dispatch Titles"

    @pytest.mark.asyncio
    async def test_enqueue_without_title_defaults_null(self, svc: DispatchDBService):
        """T03c: enqueue without title defaults to NULL."""
        row = await svc.enqueue(**_enqueue_kwargs("STORY-035"))

        assert row.get("title") is None

    @pytest.mark.asyncio
    async def test_title_survives_claim_complete(self, svc: DispatchDBService):
        """T03d: title is preserved through claim and complete lifecycle."""
        await svc.enqueue(**_enqueue_kwargs("STORY-036"), title="Lifecycle Test")
        claimed = await svc.claim("STORY-036", "dan")
        assert claimed["title"] == "Lifecycle Test"

        completed = await svc.complete("STORY-036")
        assert completed["title"] == "Lifecycle Test"


class TestListQueue:
    """T04: list_queue method."""

    @pytest.mark.asyncio
    async def test_list_queue_returns_active_only(self, svc: DispatchDBService):
        """T04: list_queue returns pending and claimed, not terminal (AC-2)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        await svc.enqueue(**_enqueue_kwargs("STORY-002"))
        await svc.claim("STORY-002", "dan")

        # Add a completed item
        await svc.enqueue(**_enqueue_kwargs("STORY-003"))
        await svc.claim("STORY-003", "derrick")
        await svc.complete("STORY-003")

        result = await svc.list_queue()
        assert len(result["pending"]) == 1
        assert len(result["claimed"]) == 1
        assert result["pending"][0]["story_id"] == "STORY-001"
        assert result["claimed"][0]["story_id"] == "STORY-002"


class TestNextPending:
    """T05: next_pending method."""

    @pytest.mark.asyncio
    async def test_next_pending_returns_oldest(self, svc: DispatchDBService):
        """T05a: Returns oldest pending item, FIFO order (AC-3)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        await svc.enqueue(**_enqueue_kwargs("STORY-002"))

        result = await svc.next_pending()
        assert result is not None
        assert result["story_id"] == "STORY-001"

    @pytest.mark.asyncio
    async def test_next_pending_empty_returns_none(self, svc: DispatchDBService):
        """T05b: Returns None when no pending items (AC-3)."""
        result = await svc.next_pending()
        assert result is None


class TestClaim:
    """T06-T07: claim method."""

    @pytest.mark.asyncio
    async def test_claim_transitions_to_claimed(self, svc: DispatchDBService):
        """T06: claim transitions pending→claimed with agent + timestamp (AC-4)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-050"))

        row = await svc.claim("STORY-050", "dan")

        assert row["status"] == "claimed"
        assert row["claimed_by"] == "dan"
        assert row["claimed_at"] is not None

    @pytest.mark.asyncio
    async def test_claim_non_pending_raises(self, svc: DispatchDBService):
        """T07: claim on non-pending story raises appropriate error (AC-4)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-050"))
        await svc.claim("STORY-050", "dan")

        # Second claim should raise
        with pytest.raises((AlreadyClaimedError, NotFoundError)):
            await svc.claim("STORY-050", "derrick")

    @pytest.mark.asyncio
    async def test_claim_not_found_raises(self, svc: DispatchDBService):
        """T07b: claim on non-existent story raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await svc.claim("STORY-999", "dan")


class TestCancel:
    """T08-T09: cancel method."""

    @pytest.mark.asyncio
    async def test_cancel_transitions_to_cancelled(self, svc: DispatchDBService):
        """T08: cancel transitions pending→cancelled with timestamp (AC-5)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-060"))

        row = await svc.cancel("STORY-060")

        assert row["status"] == "cancelled"
        assert row["cancelled_at"] is not None

    @pytest.mark.asyncio
    async def test_cancel_claimed_raises(self, svc: DispatchDBService):
        """T09: cancel on claimed story raises AlreadyClaimedError (AC-5)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-060"))
        await svc.claim("STORY-060", "dan")

        with pytest.raises(AlreadyClaimedError):
            await svc.cancel("STORY-060")


class TestComplete:
    """T10-T11: complete method."""

    @pytest.mark.asyncio
    async def test_complete_transitions_claimed_to_completed(self, svc: DispatchDBService):
        """T10: complete transitions claimed→completed with timestamp (AC-6)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-070"))
        await svc.claim("STORY-070", "dan")

        row = await svc.complete("STORY-070")

        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        assert row["claimed_by"] == "dan"

    @pytest.mark.asyncio
    async def test_complete_non_claimed_raises(self, svc: DispatchDBService):
        """T11: complete on non-claimed story raises error (AC-6)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-070"))

        with pytest.raises(InvalidTransitionError):
            await svc.complete("STORY-070")


class TestHistory:
    """T12-T13: history method."""

    @pytest.mark.asyncio
    async def test_history_returns_terminal_paginated(self, svc: DispatchDBService):
        """T12: history returns completed/cancelled, paginated, ordered desc (AC-7)."""
        # Create completed item
        await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        await svc.complete("STORY-001")

        # Create cancelled item
        await svc.enqueue(**_enqueue_kwargs("STORY-002"))
        await svc.cancel("STORY-002")

        # Create pending item (should NOT appear)
        await svc.enqueue(**_enqueue_kwargs("STORY-003"))

        result = await svc.history(limit=10, offset=0)

        assert result["total"] == 2
        assert len(result["items"]) == 2
        # All items should be terminal
        for item in result["items"]:
            assert item["status"] in ("completed", "cancelled")

    @pytest.mark.asyncio
    async def test_history_with_status_filter(self, svc: DispatchDBService):
        """T13: history with status_filter returns only matching status (AC-7)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        await svc.complete("STORY-001")

        await svc.enqueue(**_enqueue_kwargs("STORY-002"))
        await svc.cancel("STORY-002")

        completed = await svc.history(status_filter="completed")
        assert completed["total"] == 1
        assert completed["items"][0]["story_id"] == "STORY-001"

        cancelled = await svc.history(status_filter="cancelled")
        assert cancelled["total"] == 1
        assert cancelled["items"][0]["story_id"] == "STORY-002"


class TestRecoverStaleClaims:
    """T14-T15: recover_stale_claims method.

    Recovery is keyed off ``updated_at`` (not ``claimed_at``) so that a
    long-running phase which touches ``updated_at`` stays claimed. The
    default timeout is 3600 s — matches Phase 8 Large harness timeout,
    keeps Medium phases (10-30 min) from being falsely recovered and
    double-claimed (STORY-511 2026-04-22 regression).
    """

    @pytest.mark.asyncio
    async def test_recover_stale_claims_moves_old(self, svc: DispatchDBService, db_pool):
        """T14: Stale claims (no updates for >timeout) return to pending (AC-8)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-080"))
        await svc.claim("STORY-080", "dan")

        # Backdate updated_at to make it stale (no phase activity for >timeout)
        async with db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE dispatch_items
                   SET claimed_at = now() - interval '2 hours',
                       updated_at = now() - interval '2 hours'
                   WHERE story_id = 'STORY-080'"""
            )

        recovered = await svc.recover_stale_claims(timeout_seconds=300)

        assert "STORY-080" in recovered

        # Verify it's back to pending
        row = await svc.next_pending()
        assert row is not None
        assert row["story_id"] == "STORY-080"
        assert row["status"] == "pending"
        assert row["claimed_by"] is None

    @pytest.mark.asyncio
    async def test_recover_stale_claims_leaves_fresh(self, svc: DispatchDBService):
        """T15: Fresh claims (<timeout) stay claimed (AC-8)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-081"))
        await svc.claim("STORY-081", "dan")

        recovered = await svc.recover_stale_claims(timeout_seconds=300)

        assert recovered == []

        # Verify still claimed
        queue = await svc.list_queue()
        assert len(queue["claimed"]) == 1

    @pytest.mark.asyncio
    async def test_recover_keys_off_updated_not_claimed(
        self, svc: DispatchDBService, db_pool
    ):
        """STORY-511 regression: a claim that is old by ``claimed_at`` but
        was recently touched (``updated_at`` fresh) must NOT be recovered.

        Reproduces the 2026-04-22 double-claim bug: Medium-scope Phase 6
        legitimately runs 10+ minutes. Phase runner touches updated_at at
        phase_start/phase_end. Recovery must respect that — otherwise a
        second agent polls, sees pending, and claims the same story.
        """
        await svc.enqueue(**_enqueue_kwargs("STORY-082"))
        await svc.claim("STORY-082", "devon")

        # Simulate: claimed 15 minutes ago, but a phase_end event 1 minute
        # ago bumped updated_at. Old-code default timeout was 300 s against
        # claimed_at, so this would have been (wrongly) recovered.
        async with db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE dispatch_items
                   SET claimed_at = now() - interval '15 minutes',
                       updated_at = now() - interval '1 minute'
                   WHERE story_id = 'STORY-082'"""
            )

        recovered = await svc.recover_stale_claims(timeout_seconds=300)

        assert "STORY-082" not in recovered, (
            "claim was touched within timeout but was still recovered — "
            "recovery is keyed off the wrong column"
        )
        queue = await svc.list_queue()
        assert any(r["story_id"] == "STORY-082" for r in queue["claimed"]), (
            "claim should still be owned by devon"
        )

    @pytest.mark.asyncio
    async def test_default_timeout_is_one_hour(self, svc: DispatchDBService, db_pool):
        """A claim 45 minutes old must NOT be recovered at the default
        timeout (3600 s). The previous default (300 s) would have released
        it mid-Medium-phase and let a second agent claim (double-claim)."""
        await svc.enqueue(**_enqueue_kwargs("STORY-083"))
        await svc.claim("STORY-083", "devon")

        async with db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE dispatch_items
                   SET claimed_at = now() - interval '45 minutes',
                       updated_at = now() - interval '45 minutes'
                   WHERE story_id = 'STORY-083'"""
            )

        recovered = await svc.recover_stale_claims()  # default timeout

        assert "STORY-083" not in recovered, (
            "45-minute-old claim recovered at default timeout — default "
            "should be >=3600 s to avoid double-claim on Medium phases"
        )


class TestRegisterAgent:
    """T16-T17: register_agent method."""

    @pytest.mark.asyncio
    async def test_register_new_agent(self, svc: DispatchDBService, db_pool):
        """T16: New agent registers with is_new=True (AC-10)."""
        is_new = await svc.register_agent("dan")

        assert is_new is True

        # Verify in database
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM agents WHERE name = 'dan'")
        assert row is not None
        assert row["registered_via"] == "auto"

    @pytest.mark.asyncio
    async def test_register_existing_agent_updates_last_seen(self, svc: DispatchDBService, db_pool):
        """T17: Existing agent updates last_seen, returns is_new=False (AC-10)."""
        await svc.register_agent("dan")

        # Get initial last_seen
        async with db_pool.acquire() as conn:
            row1 = await conn.fetchrow("SELECT last_seen FROM agents WHERE name = 'dan'")

        # Small delay to ensure timestamp differs
        await asyncio.sleep(0.05)

        is_new = await svc.register_agent("dan")

        assert is_new is False

        async with db_pool.acquire() as conn:
            row2 = await conn.fetchrow("SELECT last_seen FROM agents WHERE name = 'dan'")
        assert row2["last_seen"] >= row1["last_seen"]


class TestPendingCount:
    """T18: pending_count method."""

    @pytest.mark.asyncio
    async def test_pending_count(self, svc: DispatchDBService):
        """T18: pending_count returns correct count (AC-2)."""
        assert await svc.pending_count() == 0

        await svc.enqueue(**_enqueue_kwargs("STORY-001"))
        await svc.enqueue(**_enqueue_kwargs("STORY-002"))
        assert await svc.pending_count() == 2

        await svc.claim("STORY-001", "dan")
        assert await svc.pending_count() == 1


class TestMigrationIdempotent:
    """T30: Migration script runs twice without error (AC-13)."""

    @pytest.mark.asyncio
    async def test_migration_idempotent(self, db_pool):
        """T30: Running migration DDL twice does not error."""
        migration_sql = open("scripts/migrations/001_dispatch_queue.sql").read()

        async with db_pool.acquire() as conn:
            # Run twice — should not raise
            await conn.execute(migration_sql)
            await conn.execute(migration_sql)
