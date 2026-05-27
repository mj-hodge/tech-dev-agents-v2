"""STORY-1010: PR-number gate at transition service layer.

SC-3: New dispatches blocked at in_review without pr_number.
SC-4: Legacy in-flight dispatches still use backfill sweeper.
SC-5: set-pr-number endpoint is atomic and idempotent.

RED reasons (until Phase 8):
  - pr_number enforcement gate not added to transition()
  - set-pr-number endpoint does not exist
  - OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT env var not read
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.services.dispatch_v2_service import (
    DispatchV2Service,
    InvalidEventDataError,
    PrNumberConflictError,
)


# ---------------------------------------------------------------------------
# Helpers — mock asyncpg.Connection that works with _BareConnContext path
# ---------------------------------------------------------------------------

def _rec(data: dict) -> MagicMock:
    """Create a mock asyncpg Record."""
    r = MagicMock()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    for k, v in data.items():
        setattr(r, k, v)
    return r


def _make_mock_conn(
    *,
    lease_token: str = "valid-token",
    current_state: str = "leased",
    pr_number: int | None = None,
    created_at: datetime | None = None,
    repo: str = "test-repo",
) -> MagicMock:
    """Build a mock asyncpg.Connection.

    Since DispatchV2Service._is_pool() checks isinstance(resource, asyncpg.Pool),
    and MagicMock is not a Pool, the service uses _BareConnContext which wraps
    a bare connection as an async CM. So our mock IS the connection.
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc)

    conn = MagicMock(spec=asyncpg.Connection)

    # transaction context manager
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    # Build fetchrow responses based on query content
    async def mock_fetchrow(query, *args):
        q = query.lower()
        if "dispatch_leases" in q:
            return _rec({"lease_token": lease_token})
        elif "dispatch_state_current" in q:
            return _rec({"state": current_state})
        elif "dispatch_jobs" in q and "pr_number" in q:
            return _rec({
                "repo": repo,
                "pr_number": pr_number,
                "created_at": created_at,
            })
        elif "dispatch_v2_events" in q:
            return _rec({"event_id": 42})
        return None

    conn.fetchrow = mock_fetchrow
    conn.execute = AsyncMock()
    return conn


def _make_service(conn: MagicMock) -> DispatchV2Service:
    """Create a DispatchV2Service wrapping a mock connection (bare-conn path)."""
    svc = DispatchV2Service(conn)
    return svc


# ---------------------------------------------------------------------------
# A: SC-3 — new dispatch blocked without pr_number
# ---------------------------------------------------------------------------


class TestNewDispatchBlockedWithoutPrNumber:
    """SC-3: A dispatch enqueued AFTER the rollout timestamp that attempts
    transition -> in_review without pr_number returns 422."""

    ROLLOUT = "2026-05-18T00:00:00Z"

    @pytest.mark.asyncio
    async def test_new_dispatch_blocked_without_pr_number(self):
        """Job created AFTER rollout with no pr_number → InvalidEventDataError."""
        created_at = datetime(2026, 5, 19, tzinfo=timezone.utc)  # after rollout
        conn = _make_mock_conn(
            current_state="leased",
            pr_number=None,
            created_at=created_at,
        )
        svc = _make_service(conn)

        with patch.dict(os.environ, {"OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT": self.ROLLOUT}):
            with pytest.raises(InvalidEventDataError, match="in_review requires pr_number"):
                await svc.transition(
                    job_id=str(uuid.uuid4()),
                    lease_token="valid-token",
                    event_type="submitted",
                    event_data={},
                )

    @pytest.mark.asyncio
    async def test_new_dispatch_allowed_with_pr_number(self):
        """Job created AFTER rollout with pr_number set → transition succeeds."""
        created_at = datetime(2026, 5, 19, tzinfo=timezone.utc)
        conn = _make_mock_conn(
            current_state="leased",
            pr_number=123,
            created_at=created_at,
        )
        svc = _make_service(conn)

        with patch.dict(os.environ, {"OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT": self.ROLLOUT}):
            event_id = await svc.transition(
                job_id=str(uuid.uuid4()),
                lease_token="valid-token",
                event_type="submitted",
                event_data={"pr_number": 123},
            )
            assert event_id == 42


# ---------------------------------------------------------------------------
# B: SC-4 — legacy in-flight dispatches still use backfill sweeper
# ---------------------------------------------------------------------------


class TestLegacyDispatchUsesBackfillSweeper:
    """SC-4: A dispatch enqueued BEFORE the rollout is NOT rejected."""

    ROLLOUT = "2026-05-18T00:00:00Z"

    @pytest.mark.asyncio
    async def test_legacy_dispatch_uses_backfill_sweeper(self):
        """Job created BEFORE rollout with no pr_number → transition allowed
        (the backfill sweeper handles it later)."""
        created_at = datetime(2026, 5, 1, tzinfo=timezone.utc)  # before rollout
        conn = _make_mock_conn(
            current_state="leased",
            pr_number=None,
            created_at=created_at,
        )
        svc = _make_service(conn)

        with patch.dict(os.environ, {"OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT": self.ROLLOUT}):
            # Should NOT raise — legacy job allowed through
            event_id = await svc.transition(
                job_id=str(uuid.uuid4()),
                lease_token="valid-token",
                event_type="submitted",
                event_data={},
            )
            assert event_id == 42


# ---------------------------------------------------------------------------
# C: SC-5 — set-pr-number endpoint idempotent + conflict
# ---------------------------------------------------------------------------


class TestSetPrNumber:
    """SC-5: set_pr_number is atomic, idempotent, and conflict-safe."""

    def _make_set_pr_conn(self, existing_pr: int | None) -> MagicMock:
        """Mock connection for set_pr_number tests."""
        conn = MagicMock(spec=asyncpg.Connection)

        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=txn)

        conn.fetchrow = AsyncMock(return_value=_rec({
            "job_id": "test-id",
            "pr_number": existing_pr,
        }))
        conn.execute = AsyncMock()
        return conn

    @pytest.mark.asyncio
    async def test_set_pr_number_first_call_succeeds(self):
        """First call to set pr_number succeeds (200)."""
        conn = self._make_set_pr_conn(existing_pr=None)
        svc = _make_service(conn)

        result = await svc.set_pr_number(job_id="test-id", pr_number=123)
        assert result["status"] == "ok"
        assert result["pr_number"] == 123

    @pytest.mark.asyncio
    async def test_set_pr_number_idempotent(self):
        """Second call with same value returns ok (idempotent)."""
        conn = self._make_set_pr_conn(existing_pr=123)
        svc = _make_service(conn)

        result = await svc.set_pr_number(job_id="test-id", pr_number=123)
        assert result["status"] == "ok"
        assert result["idempotent"] is True

    @pytest.mark.asyncio
    async def test_set_pr_number_conflict_returns_409(self):
        """Call with conflicting value raises PrNumberConflictError."""
        conn = self._make_set_pr_conn(existing_pr=123)
        svc = _make_service(conn)

        with pytest.raises(PrNumberConflictError):
            await svc.set_pr_number(job_id="test-id", pr_number=456)

    @pytest.mark.asyncio
    async def test_set_pr_number_uses_for_update(self):
        """G04 — race-condition guard: set_pr_number SELECT must include FOR UPDATE.

        Without FOR UPDATE, two concurrent callers under READ COMMITTED isolation
        can both observe pr_number IS NULL in their snapshot, bypass the conflict
        check, and race to write — the second silently overwrites the first.
        FOR UPDATE acquires a row-level lock so the second caller blocks until
        the first transaction commits, then reads the committed value and correctly
        takes the idempotent or conflict path.

        This test asserts the locking invariant at the source-code level by
        inspecting the SQL string passed to conn.fetchrow. (R5 / N2 invariant.)
        """
        conn = self._make_set_pr_conn(existing_pr=None)
        svc = _make_service(conn)

        await svc.set_pr_number(job_id="test-id", pr_number=42)

        # Collect all SQL strings passed to fetchrow
        fetchrow_calls = conn.fetchrow.call_args_list
        fetchrow_sqls = [str(c.args[0]) if c.args else "" for c in fetchrow_calls]
        assert any(
            "FOR UPDATE" in sql.upper() for sql in fetchrow_sqls
        ), (
            "set_pr_number() SELECT must include FOR UPDATE to prevent TOCTOU race. "
            f"Observed fetchrow calls: {fetchrow_sqls}"
        )
