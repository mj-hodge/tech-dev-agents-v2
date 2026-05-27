"""Tests for STORY-545: Graceful AmbiguousStoryError handling in complete().

STORY-545 fixes two defects in PR #85 (STORY-531):
  1. Migration 007 filename collision → renumber to 008
  2. complete() raises AmbiguousStoryError when repo=None and multiple active
     rows match → should catch, log warning, pick most recently claimed row.

RED state: These tests fail because the fix hasn't been implemented yet.
- AmbiguousStoryError doesn't exist on main
- complete() has no repo= parameter on main
- Migration 008 file doesn't exist yet
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Group A: Migration File Correctness
# ---------------------------------------------------------------------------

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "migrations"


class TestMigrationFileCorrectness:
    """Verify migration 007 collision is resolved by renumbering to 008."""

    def test_migration_008_composite_key_exists(self):
        """Migration 008 must exist after the renumber from 007."""
        target = MIGRATIONS_DIR / "008_composite_key_story_repo.sql"
        assert target.exists(), (
            f"Expected {target} to exist — STORY-531 migration must be "
            f"renumbered from 007 to 008 to avoid collision with "
            f"007_needs_info_state.sql"
        )

    def test_migration_007_composite_key_does_not_exist(self):
        """The old 007_composite_key_story_repo.sql must NOT exist."""
        colliding = MIGRATIONS_DIR / "007_composite_key_story_repo.sql"
        assert not colliding.exists(), (
            f"Found {colliding} — this collides with 007_needs_info_state.sql. "
            f"Rename to 008_composite_key_story_repo.sql."
        )


# ---------------------------------------------------------------------------
# Group B + C: complete() Graceful Ambiguity Handling
# ---------------------------------------------------------------------------

# Import AmbiguousStoryError — this will fail on main (class doesn't exist yet)
# but will succeed once the STORY-531 branch code is rebased in.
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
    NotFoundError,
    InvalidTransitionError,
)

# AmbiguousStoryError is introduced by STORY-531 — import it; will ImportError
# on main before the fix is applied.
try:
    from tech_dev_agents.ops_console.services.dispatch_db_service import (
        AmbiguousStoryError,
    )
except ImportError:
    # Define a placeholder so tests can be collected even on main.
    # They will fail with meaningful assertion errors, not import errors.
    class AmbiguousStoryError(Exception):  # type: ignore[no-redef]
        def __init__(self, story_id: str, candidate_repos: list[str]) -> None:
            self.story_id = story_id
            self.candidate_repos = candidate_repos
            super().__init__(f"{story_id} ambiguous across {candidate_repos}")


# -- Fake asyncpg infrastructure (matches test_dispatch_claim_sync.py pattern) --


def _make_row(overrides: dict | None = None) -> dict:
    """Build a fake dispatch_items row dict."""
    base = {
        "id": 1,
        "story_id": "STORY-100",
        "repo": "tech-dev-agents",
        "status": "claimed",
        "enqueued_at": datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        "claimed_at": datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc),
        "completed_at": None,
        "cancelled_at": None,
        "updated_at": datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc),
        "review_started_at": None,
        "paused_at": None,
        "commit_sha": None,
        "pr_number": None,
        "enqueued_by": "mark",
        "claimed_by": "devon",
        "prompt": "do the thing",
        "scope": "small",
        "priority": 50,
        "title": "Test story",
        "retry_count": 0,
        "needs_info_path": None,
    }
    if overrides:
        base.update(overrides)
    return base


class FakeRecord(dict):
    """Dict that also supports attribute-style access (like asyncpg.Record)."""
    def __getitem__(self, key):
        return super().__getitem__(key)


def _to_record(d: dict) -> FakeRecord:
    rec = FakeRecord(d)
    return rec


class FakeConn:
    """Fake asyncpg connection for unit tests."""

    def __init__(self):
        self.fetchrow_responses: list = []
        self.fetch_responses: list = []
        self.execute_responses: list = []
        self._queries: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args):
        self._queries.append((query, args))
        if self.fetchrow_responses:
            return self.fetchrow_responses.pop(0)
        return None

    async def fetch(self, query: str, *args):
        self._queries.append((query, args))
        if self.fetch_responses:
            return self.fetch_responses.pop(0)
        return []

    async def fetchval(self, query: str, *args):
        return None

    async def execute(self, query: str, *args):
        self._queries.append((query, args))

    def transaction(self):
        return _FakeTxn()


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakePool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def db_service():
    """Create a DispatchDBService with a fake pool (no real PG needed)."""
    conn = FakeConn()
    pool = FakePool(conn)
    svc = DispatchDBService.__new__(DispatchDBService)
    svc._pool = pool
    return svc, conn


# ---------------------------------------------------------------------------
# Group B: complete() graceful ambiguity handling
# ---------------------------------------------------------------------------


class TestCompleteAmbiguousGracefulHandling:
    """STORY-545: complete() must catch AmbiguousStoryError, log, and pick
    the most recently claimed record instead of raising."""

    @pytest.mark.asyncio
    async def test_complete_ambiguous_no_repo_picks_most_recently_claimed(
        self, db_service
    ):
        """When repo=None and multiple active rows exist for the same story_id,
        complete() should NOT raise AmbiguousStoryError. Instead it should
        pick the row with the most recent claimed_at and complete it.
        """
        svc, conn = db_service

        # Two rows for the same story_id in different repos
        row_old = _to_record(_make_row({
            "id": 1,
            "repo": "repo-alpha",
            "claimed_at": datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
            "status": "claimed",
        }))
        row_recent = _to_record(_make_row({
            "id": 2,
            "repo": "repo-beta",
            "claimed_at": datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
            "status": "claimed",
        }))

        # After the fix: complete() calls _resolve_row which raises
        # AmbiguousStoryError; complete() catches it, queries again with
        # ORDER BY claimed_at DESC NULLS LAST, picks row_recent, updates it.
        completed_row = _to_record(_make_row({
            "id": 2,
            "repo": "repo-beta",
            "status": "completed",
            "completed_at": datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            "commit_sha": "abc123",
        }))

        # The _resolve_row helper will do a SELECT and find 2 rows → raises
        # AmbiguousStoryError. The fallback does a fetchrow (SELECT most recent)
        # then another fetchrow (UPDATE to complete it).
        conn.fetch_responses = [[row_old, row_recent]]           # _resolve_row SELECT
        conn.fetchrow_responses = [row_recent, completed_row]    # fallback SELECT, UPDATE

        result = await svc.complete(
            "STORY-100", commit_sha="abc123", repo=None,
        )

        assert result is not None
        assert result["id"] == 2
        assert result["repo"] == "repo-beta"
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_ambiguous_no_repo_logs_warning(
        self, db_service, caplog
    ):
        """When ambiguity is resolved by fallback, a warning must be logged."""
        svc, conn = db_service

        row_old = _to_record(_make_row({
            "id": 1, "repo": "repo-alpha",
            "claimed_at": datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        }))
        row_recent = _to_record(_make_row({
            "id": 2, "repo": "repo-beta",
            "claimed_at": datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        }))
        completed_row = _to_record(_make_row({
            "id": 2, "repo": "repo-beta", "status": "completed",
            "completed_at": datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        }))

        conn.fetch_responses = [[row_old, row_recent]]
        conn.fetchrow_responses = [row_recent, completed_row]

        with caplog.at_level(logging.WARNING):
            await svc.complete("STORY-100", repo=None)

        # Must log a warning mentioning the story and repos
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("STORY-100" in msg for msg in warning_messages), (
            f"Expected a warning log mentioning 'STORY-100', got: {warning_messages}"
        )

    @pytest.mark.asyncio
    async def test_complete_single_row_no_repo_no_warning(
        self, db_service, caplog
    ):
        """When repo=None and only one active row exists, complete() returns
        normally with no warning — backward compatibility with pre-531 callers.
        """
        svc, conn = db_service

        single_row = _to_record(_make_row({
            "id": 1, "repo": "tech-dev-agents", "status": "claimed",
        }))
        completed_row = _to_record(_make_row({
            "id": 1, "repo": "tech-dev-agents", "status": "completed",
            "completed_at": datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            "commit_sha": "def456",
        }))

        # _resolve_row finds exactly 1 row → returns it (no ambiguity)
        conn.fetch_responses = [[single_row]]
        conn.fetchrow_responses = [completed_row]

        with caplog.at_level(logging.WARNING):
            result = await svc.complete(
                "STORY-100", commit_sha="def456", repo=None,
            )

        assert result is not None
        assert result["status"] == "completed"

        # No AmbiguousStoryError warning should be logged
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        ambiguous_warnings = [m for m in warning_messages if "ambiguous" in m.lower() or "multiple" in m.lower()]
        assert len(ambiguous_warnings) == 0, (
            f"Expected no ambiguity warnings for single-row case, got: {ambiguous_warnings}"
        )

    @pytest.mark.asyncio
    async def test_complete_with_explicit_repo_exact_match(
        self, db_service
    ):
        """When repo is provided explicitly, complete() uses exact (story_id, repo)
        match — no ambiguity fallback needed.
        """
        svc, conn = db_service

        target_row = _to_record(_make_row({
            "id": 3, "repo": "repo-alpha", "status": "claimed",
        }))
        completed_row = _to_record(_make_row({
            "id": 3, "repo": "repo-alpha", "status": "completed",
            "completed_at": datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            "commit_sha": "ghi789",
        }))

        # _resolve_row with repo="repo-alpha" → exact match → single row
        conn.fetch_responses = [[target_row]]
        conn.fetchrow_responses = [completed_row]

        result = await svc.complete(
            "STORY-100", commit_sha="ghi789", repo="repo-alpha",
        )

        assert result is not None
        assert result["id"] == 3
        assert result["repo"] == "repo-alpha"
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Group C: Error Observability (Gate 10)
# ---------------------------------------------------------------------------


class TestCompleteAmbiguousWarningContext:
    """Gate 10: Verify the warning log includes actionable context."""

    @pytest.mark.asyncio
    async def test_complete_ambiguous_warning_includes_context(
        self, db_service, caplog
    ):
        """The warning log must include both the story_id AND the candidate
        repos so operators can diagnose the ambiguity.
        """
        svc, conn = db_service

        row_a = _to_record(_make_row({
            "id": 1, "repo": "repo-alpha",
            "claimed_at": datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        }))
        row_b = _to_record(_make_row({
            "id": 2, "repo": "repo-beta",
            "claimed_at": datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        }))
        completed_row = _to_record(_make_row({
            "id": 2, "repo": "repo-beta", "status": "completed",
            "completed_at": datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        }))

        conn.fetch_responses = [[row_a, row_b]]
        conn.fetchrow_responses = [row_b, completed_row]

        with caplog.at_level(logging.WARNING):
            await svc.complete("STORY-100", repo=None)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        combined = " ".join(warning_messages)

        # Must mention story_id
        assert "STORY-100" in combined, (
            f"Warning must mention story_id. Got: {combined}"
        )
        # Must mention at least one candidate repo
        assert "repo-alpha" in combined or "repo-beta" in combined, (
            f"Warning must mention candidate repos. Got: {combined}"
        )
