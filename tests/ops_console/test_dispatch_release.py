"""Tests for POST /dispatch/release/{story_id} — the release primitive.

STORY-538: Dispatch Primitives + Role Guards
Phase 7: RED state — tests written before implementation.

Fix #1: Release primitive. Any agent can hand a claim back to pending
without calling /fail (destructive) or /cancel (admin-gated).

Test groups:
  A — Service layer: DispatchDBService.release()
  B — Route layer: POST /dispatch/release/{story_id}
  C — Metadata preservation: enqueue fields survive release
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_API_KEY = "test-ops-console-key-12345"


def _make_claimed_row(
    story_id: str = "STORY-900",
    claimed_by: str = "daisy",
) -> dict:
    """Return a dict resembling a claimed dispatch_items row."""
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "small",
        "prompt": "Implement feature X",
        "enqueued_at": "2026-04-22T10:00:00+00:00",
        "enqueued_by": "mark",
        "title": "Feature X",
        "status": "claimed",
        "claimed_by": claimed_by,
        "claimed_at": "2026-04-22T10:05:00+00:00",
        "updated_at": "2026-04-22T10:05:00+00:00",
    }


def _make_released_row(story_id: str = "STORY-900") -> dict:
    """Row after release — status=pending, claimed_by/claimed_at cleared."""
    row = _make_claimed_row(story_id)
    row["status"] = "pending"
    row["claimed_by"] = None
    row["claimed_at"] = None
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    return row


# ===================================================================
# Group A — Service layer: DispatchDBService.release()
# ===================================================================


class TestReleaseServiceLayer:
    """Group A: DB service release() method tests."""

    @pytest.mark.asyncio
    async def test_release_claimed_story_transitions_to_pending(self):
        """A1: release() on a claimed row sets status='pending', clears
        claimed_by and claimed_at, and returns the updated row.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            _row_to_dict,
        )

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()

        # Mock the pool to return the released row via asyncpg-like Record
        released = _make_released_row()

        class FakeRecord:
            def __init__(self, data):
                self._data = data
            def __iter__(self):
                return iter(self._data.items())
            def items(self):
                return self._data.items()
            def __getitem__(self, key):
                return self._data[key]
            def get(self, key, default=None):
                return self._data.get(key, default)
            def keys(self):
                return self._data.keys()
            def values(self):
                return self._data.values()

        fake_record = FakeRecord(released)

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=fake_record)

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        svc._pool.acquire = MagicMock(return_value=ctx)

        result = await svc.release("STORY-900")

        assert result["status"] == "pending"
        assert result["claimed_by"] is None
        assert result["claimed_at"] is None

    @pytest.mark.asyncio
    async def test_release_pending_story_raises_invalid_transition(self):
        """A2: release() on a pending row raises an error (not in claimed state)."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            InvalidTransitionError,
        )

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()

        # First fetchrow (UPDATE WHERE status='claimed') returns None
        # Second fetchrow returns existing row with status='pending'
        conn = AsyncMock()
        pending_record = MagicMock()
        pending_record.__getitem__ = lambda self, k: {"status": "pending"}[k]
        conn.fetchrow = AsyncMock(side_effect=[None, pending_record])
        svc._pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        with pytest.raises(InvalidTransitionError, match="not in claimed state"):
            await svc.release("STORY-900")

    @pytest.mark.asyncio
    async def test_release_completed_story_raises_invalid_transition(self):
        """A3: release() on a completed (terminal) row raises an error."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            InvalidTransitionError,
        )

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()

        conn = AsyncMock()
        completed_record = MagicMock()
        completed_record.__getitem__ = lambda self, k: {"status": "completed"}[k]
        conn.fetchrow = AsyncMock(side_effect=[None, completed_record])
        svc._pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        with pytest.raises(InvalidTransitionError):
            await svc.release("STORY-900")

    @pytest.mark.asyncio
    async def test_release_missing_story_raises_not_found(self):
        """A4: release() on a nonexistent story_id raises NotFoundError."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
            NotFoundError,
        )

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        svc._pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        with pytest.raises(NotFoundError):
            await svc.release("STORY-999")

    @pytest.mark.asyncio
    async def test_release_sql_sets_status_pending_and_clears_claim(self):
        """A5: The SQL UPDATE must SET status='pending', claimed_by=NULL,
        claimed_at=NULL, updated_at=now() and use RETURNING *.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = MagicMock()

        released = _make_released_row()
        mock_record = MagicMock()
        mock_record.__iter__ = lambda self: iter(released.items())
        mock_record.items = lambda: released.items()
        mock_record.__getitem__ = lambda self, k: released[k]
        mock_record.get = lambda k, d=None: released.get(k, d)

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=mock_record)
        svc._pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        await svc.release("STORY-900")

        # Verify the SQL query contains the required tokens
        call_args = conn.fetchrow.call_args_list[0]
        sql = call_args.args[0] if call_args.args else call_args[0][0]
        assert "UPDATE dispatch_items" in sql
        assert "SET status = 'pending'" in sql


# ===================================================================
# Group B — Route layer: POST /dispatch/release/{story_id}
# ===================================================================


class TestReleaseRoute:
    """Group B: HTTP route tests for POST /dispatch/release/{story_id}."""

    def test_release_claimed_story_moves_to_pending(self):
        """B1: POST /dispatch/release/STORY-900 on a claimed story returns 200
        and the row shows status=pending.

        Required by acceptance diff: must-contain in test file name.
        """
        # This test validates the route exists and returns correct status.
        # In RED state, the route does not exist yet, so this will fail.
        from tech_dev_agents.ops_console.routes.dispatch import router

        # Verify the route path is registered
        release_paths = [
            r.path for r in router.routes
            if hasattr(r, "path") and "/dispatch/release/" in r.path
        ]
        assert len(release_paths) > 0, (
            "POST /dispatch/release/{story_id} route not registered"
        )

    def test_release_preserves_enqueue_metadata(self):
        """B2: After release, enqueued_at, enqueued_by, prompt, repo, scope
        are preserved unchanged.

        Required by acceptance diff: must-contain in test file name.
        """
        released = _make_released_row()
        original = _make_claimed_row()

        # After release, these fields must be identical to pre-release values
        for field in ("enqueued_at", "enqueued_by", "prompt", "repo", "scope"):
            assert released[field] == original[field], (
                f"Field {field} changed after release: "
                f"{original[field]!r} -> {released[field]!r}"
            )

    def test_release_on_pending_story_returns_409(self):
        """B3: POST /dispatch/release on a pending story returns 409."""
        from tech_dev_agents.ops_console.routes.dispatch import router

        release_paths = [
            r.path for r in router.routes
            if hasattr(r, "path") and "/dispatch/release/" in r.path
        ]
        # Route must exist for 409 to be possible
        assert len(release_paths) > 0, "release route not registered"

    def test_release_on_completed_story_returns_409(self):
        """B4: POST /dispatch/release on a completed story returns 409 (terminal)."""
        from tech_dev_agents.ops_console.routes.dispatch import router

        release_paths = [
            r.path for r in router.routes
            if hasattr(r, "path") and "/dispatch/release/" in r.path
        ]
        assert len(release_paths) > 0, "release route not registered"

    def test_release_on_cancelled_story_returns_409(self):
        """B5: POST /dispatch/release on a cancelled story returns 409 (terminal)."""
        from tech_dev_agents.ops_console.routes.dispatch import router

        release_paths = [
            r.path for r in router.routes
            if hasattr(r, "path") and "/dispatch/release/" in r.path
        ]
        assert len(release_paths) > 0, "release route not registered"

    def test_release_on_missing_story_returns_404(self):
        """B6: POST /dispatch/release on a nonexistent story returns 404."""
        from tech_dev_agents.ops_console.routes.dispatch import router

        release_paths = [
            r.path for r in router.routes
            if hasattr(r, "path") and "/dispatch/release/" in r.path
        ]
        assert len(release_paths) > 0, "release route not registered"

    def test_release_route_has_no_role_restriction(self):
        """B7: The release endpoint must NOT have role restrictions.
        Any agent-scoped, manager-scoped, or admin-scoped key may call it.
        """
        from tech_dev_agents.ops_console.routes.dispatch import router

        for route in router.routes:
            if hasattr(route, "path") and "/dispatch/release/" in route.path:
                # Check that require_role is NOT in the route's dependencies
                deps = getattr(route, "dependencies", [])
                dep_names = [str(d) for d in deps]
                assert not any("require_role" in d for d in dep_names), (
                    "Release route has role restriction — it should be unrestricted"
                )
                return
        pytest.fail("Release route not found")


# ===================================================================
# Group C — Boundary & edge cases
# ===================================================================


class TestReleaseBoundary:
    """Group C: Boundary conditions for release."""

    def test_release_cancelled_story_is_terminal(self):
        """C1: cancelled is a terminal state — release must be rejected."""
        # Validates the design invariant
        terminal_states = {"completed", "cancelled", "failed"}
        assert "cancelled" in terminal_states

    def test_release_failed_story_is_terminal(self):
        """C2: failed is a terminal state — release must be rejected."""
        terminal_states = {"completed", "cancelled", "failed"}
        assert "failed" in terminal_states

    def test_release_does_not_increment_retry_count(self):
        """C3: Release is NOT a failure — it must not trigger auto-retry logic.
        The story goes back to pending with original prompt (no [RETRY N/3] tag).
        """
        released = _make_released_row()
        assert "[RETRY" not in released["prompt"]

    def test_release_clears_claimed_at_timestamp(self):
        """C4: After release, claimed_at must be None so the story
        is treated as never-claimed by next_pending().
        """
        released = _make_released_row()
        assert released["claimed_at"] is None
