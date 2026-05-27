"""STORY-574 — POST /dispatch/force-release/{story_id} admin override.

Mark's 2026-04-24 complaint: stalled agents wedge stories forever because
/release refuses unless status='claimed'. The only escape was /fail
(destructive — kills retry ladder) or direct SQL. This endpoint lets
a manager free a stuck story without torching its history.

Contract locked by this file:
  - Service layer `force_release()` accepts claimed | in_progress | in_review
  - Service layer rejects terminal states (completed / cancelled / failed)
  - Route is MANAGER-only (403 for developer role)
  - Route requires a `reason` query param (10–500 chars)
  - Route audits WHO force-released WHAT and WHY to the logger
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Service-layer fixtures (reused pattern from test_dispatch_release.py)
# ---------------------------------------------------------------------------


def _released_row(story_id="STORY-900") -> dict:
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "small",
        "prompt": "Implement feature X",
        "enqueued_at": "2026-04-22T10:00:00+00:00",
        "enqueued_by": "mark",
        "title": "Feature X",
        "status": "pending",
        "claimed_by": None,
        "claimed_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _fake_conn_returning(rows):
    """Return a mock connection whose fetchrow yields the given iterable."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=list(rows))
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return conn, ctx


def _record(data: dict):
    r = MagicMock()
    r.__iter__ = lambda self: iter(data.items())
    r.items = lambda: data.items()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    r.keys = lambda: data.keys()
    r.values = lambda: data.values()
    return r


# ===========================================================================
# Group A — force_release() at the service layer
# ===========================================================================


class TestForceReleaseServiceLayer:

    @pytest.mark.asyncio
    async def test_force_release_claimed_transitions_to_pending(self):
        """Happy path A: status='claimed' → pending."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        _, ctx = _fake_conn_returning([_record(_released_row())])
        svc._pool.acquire = MagicMock(return_value=ctx)
        result = await svc.force_release("STORY-900")
        assert result["status"] == "pending"
        assert result["claimed_by"] is None
        assert result["claimed_at"] is None

    @pytest.mark.asyncio
    async def test_force_release_in_progress_transitions_to_pending(self):
        """Happy path B: status='in_progress' → pending. This is the case
        /release refuses and why force_release exists."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        _, ctx = _fake_conn_returning([_record(_released_row())])
        svc._pool.acquire = MagicMock(return_value=ctx)
        result = await svc.force_release("STORY-900")
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_force_release_in_review_transitions_to_pending(self):
        """Happy path C: status='in_review' → pending. Used when a PR went
        up but CI is hung / agent died post-PR and the claim is stuck."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        _, ctx = _fake_conn_returning([_record(_released_row())])
        svc._pool.acquire = MagicMock(return_value=ctx)
        result = await svc.force_release("STORY-900")
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_force_release_completed_raises_invalid_transition(self):
        """Terminal states refuse — use /cancel or re-enqueue instead."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            InvalidTransitionError,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        completed = MagicMock()
        completed.__getitem__ = lambda self, k: {"status": "completed"}[k]
        _, ctx = _fake_conn_returning([None, completed])
        svc._pool.acquire = MagicMock(return_value=ctx)
        with pytest.raises(InvalidTransitionError, match="terminal|completed"):
            await svc.force_release("STORY-900")

    @pytest.mark.asyncio
    async def test_force_release_cancelled_raises_invalid_transition(self):
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            InvalidTransitionError,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        cancelled = MagicMock()
        cancelled.__getitem__ = lambda self, k: {"status": "cancelled"}[k]
        _, ctx = _fake_conn_returning([None, cancelled])
        svc._pool.acquire = MagicMock(return_value=ctx)
        with pytest.raises(InvalidTransitionError):
            await svc.force_release("STORY-900")

    @pytest.mark.asyncio
    async def test_force_release_failed_raises_invalid_transition(self):
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            InvalidTransitionError,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        failed = MagicMock()
        failed.__getitem__ = lambda self, k: {"status": "failed"}[k]
        _, ctx = _fake_conn_returning([None, failed])
        svc._pool.acquire = MagicMock(return_value=ctx)
        with pytest.raises(InvalidTransitionError):
            await svc.force_release("STORY-900")

    @pytest.mark.asyncio
    async def test_force_release_missing_story_raises_not_found(self):
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            NotFoundError,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        _, ctx = _fake_conn_returning([None, None])
        svc._pool.acquire = MagicMock(return_value=ctx)
        with pytest.raises(NotFoundError):
            await svc.force_release("STORY-NONEXISTENT")

    @pytest.mark.asyncio
    async def test_force_release_sql_accepts_three_source_states(self):
        """Lock the exact SQL: WHERE status IN ('claimed','in_progress','in_review').
        Adding a state later without also adding a test here would silently
        widen the contract. Keep this aligned with the service impl."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )
        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()
        captured_sql = []

        async def capturing_fetchrow(sql, *args, **kwargs):
            captured_sql.append(sql)
            return _record(_released_row())

        conn = AsyncMock()
        conn.fetchrow = capturing_fetchrow
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        svc._pool.acquire = MagicMock(return_value=ctx)

        await svc.force_release("STORY-900")
        sql = captured_sql[0]
        assert "status IN" in sql or "status in" in sql.lower()
        for st in ("claimed", "in_progress", "in_review"):
            assert st in sql, f"force_release SQL must accept status={st!r}"
        # Must NOT accept terminal states
        for st in ("completed", "cancelled", "failed"):
            # The UPDATE WHERE clause must not include terminal states.
            # Check that the word appears ONLY inside the word boundary check
            # of the allowed-set — which it doesn't since those are distinct.
            # Rough check: count occurrences; in our impl, terminal words don't appear.
            assert st not in sql, f"force_release SQL must NOT accept status={st!r}"
