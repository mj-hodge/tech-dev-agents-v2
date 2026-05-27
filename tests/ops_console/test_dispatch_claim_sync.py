"""Tests for dispatch queue claim/completion sync issues.

Based on analysis of 200 dispatch history entries showing 7 failure modes
that caused massive token waste (2026-04-18 through 2026-04-21):

1. Cross-agent completion: Agent A completes story claimed by Agent B → rejected
2. Re-enqueue of completed stories: Completed story gets re-enqueued and re-worked
3. Idempotent completion: Same story completed twice should not error
4. Fail after complete: Agent fails a story another agent already completed
5. Story ID mutation: Duplicate check forces new IDs for retries
6. Retry re-enqueue while active: Auto-retry creates pending entry while agent still works
7. Completion from pending: Agent completes work on unclaimed/pending story
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
    AlreadyClaimedError,
    DuplicateDispatchError,
    InvalidTransitionError,
    NotFoundError,
)


class _FakeTransaction:
    """No-op async context manager for conn.transaction()."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeConn:
    """Fake asyncpg connection with controllable responses.

    fetchrow_responses is a shared queue used by both fetchrow() and fetch():
      - fetchrow() pops the next item and returns it (including None).
      - fetch()    pops the next item; None → [] (no rows), row → [row].

    This models the new _resolve_row() helper (STORY-531) which uses
    conn.fetch() to support multi-row disambiguation, while enqueue() still
    calls conn.fetchrow() directly.
    """
    def __init__(self):
        self.fetchrow_responses = []
        self.fetchval_responses = []
        self.execute_called = []
        self._last_query = ""

    async def fetchrow(self, query, *args):
        self._last_query = query
        if self.fetchrow_responses:
            return self.fetchrow_responses.pop(0)
        return None

    async def fetchval(self, query, *args):
        if self.fetchval_responses:
            return self.fetchval_responses.pop(0)
        return None

    async def execute(self, query, *args):
        self.execute_called.append((query, args))

    async def fetch(self, query, *args):
        """Return a list; None sentinel in queue → [] (zero rows found)."""
        self._last_query = query
        if self.fetchrow_responses:
            item = self.fetchrow_responses.pop(0)
            return [] if item is None else [item]
        return []

    def transaction(self):
        return _FakeTransaction()


class FakePool:
    """Fake asyncpg pool that returns FakeConn."""
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def db_service():
    conn = FakeConn()
    pool = FakePool(conn)
    svc = DispatchDBService.__new__(DispatchDBService)
    svc._pool = pool
    return svc, conn


# --- Test 1: Cross-agent completion ---

class TestCrossAgentCompletion:
    """Agent A completes a story that Agent B claimed.

    This was the #1 cause of duplicate work: Daisy completed STORY-267
    but Devon had it claimed. The completion was rejected with 409,
    the story stayed in the queue, and both agents re-did the work.
    """

    @pytest.mark.asyncio
    async def test_complete_accepts_any_agent(self, db_service):
        """Completion should succeed regardless of which agent claimed it."""
        svc, conn = db_service
        row = {
            "story_id": "STORY-267", "status": "completed",
            "claimed_by": "devon", "completed_at": "2026-04-21T01:31:54",
            "commit_sha": "abc123", "pr_number": None,
            "repo": "advertising-amazon", "scope": "medium",
            "prompt": "test", "enqueued_at": "2026-04-21T00:00:00",
            "enqueued_by": "mark", "title": "test",
            "cancelled_at": None, "updated_at": "2026-04-21T01:31:54",
        }
        # None → first _resolve_row (active-state filter) misses (status=completed is not active).
        # row  → second _resolve_row (any-state fallback) finds the completed record.
        conn.fetchrow_responses = [None, row]
        result = await svc.complete("STORY-267", commit_sha="abc123")
        assert result["status"] == "completed"


# --- Test 2: Idempotent completion ---

class TestIdempotentCompletion:
    """Completing an already-completed story should return success, not error.

    When both agents finish the same story, the second completion should
    be a no-op, not trigger a retry loop.
    """

    @pytest.mark.asyncio
    async def test_complete_already_completed_returns_success(self, db_service):
        """Second completion of same story returns the existing record."""
        svc, conn = db_service
        row = {
            "story_id": "STORY-267", "status": "completed",
            "claimed_by": "daisy", "completed_at": "2026-04-21T01:31:54",
            "commit_sha": "abc123", "pr_number": None,
            "repo": "advertising-amazon", "scope": "medium",
            "prompt": "test", "enqueued_at": "2026-04-21T00:00:00",
            "enqueued_by": "mark", "title": "test",
            "cancelled_at": None, "updated_at": "2026-04-21T01:31:54",
        }
        conn.fetchrow_responses = [None, row]  # UPDATE misses, SELECT finds completed
        result = await svc.complete("STORY-267", commit_sha="def456")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_not_found_raises(self, db_service):
        """Completing a non-existent story still raises NotFoundError."""
        svc, conn = db_service
        conn.fetchrow_responses = [None, None]  # UPDATE misses, SELECT misses
        with pytest.raises(NotFoundError):
            await svc.complete("STORY-999", commit_sha="abc123")


# --- Test 3: Fail after complete ---

class TestFailAfterComplete:
    """When Agent B tries to fail a story Agent A already completed,
    it should return the completed record, not error.

    Pattern: Agent A completes STORY-494. Agent B was also working on it,
    fails (timeout), calls fail(). The fail should be a no-op.
    """

    @pytest.mark.asyncio
    async def test_fail_completed_story_returns_completed(self, db_service):
        """Failing an already-completed story returns the completed record."""
        svc, conn = db_service
        conn.fetchval_responses = ["completed"]
        conn.fetchrow_responses = [{
            "story_id": "STORY-494", "status": "completed",
            "claimed_by": "daisy", "completed_at": "2026-04-21T10:00:00",
            "commit_sha": "abc123", "pr_number": 69,
            "repo": "tech-dev-agents", "scope": "small",
            "prompt": "test", "enqueued_at": "2026-04-21T00:00:00",
            "enqueued_by": "mark", "title": "test",
            "cancelled_at": None, "updated_at": "2026-04-21T10:00:00",
        }]
        result = await svc.fail("STORY-494")
        assert result["status"] == "completed"


# --- Test 4: Re-enqueue of terminal stories ---

class TestReEnqueueTerminal:
    """Re-enqueueing a failed/completed story should replace it, not reject.

    This caused the STORY-480 → 481 → 486 → ... → 492 chain where each
    retry needed a new ID because the duplicate check blocked re-enqueue.
    """

    @pytest.mark.asyncio
    async def test_reenqueue_failed_story_succeeds(self, db_service):
        """A failed story can be re-enqueued with the same ID."""
        svc, conn = db_service
        conn.fetchrow_responses = [
            {"status": "failed"},  # SELECT existing
            {  # INSERT result
                "story_id": "STORY-480", "status": "pending",
                "repo": "tech-dev-agents", "scope": "medium",
                "prompt": "Dashboard fix", "enqueued_at": "2026-04-21T10:00:00",
                "enqueued_by": "mark", "title": "Dashboard",
                "claimed_by": None, "claimed_at": None,
                "completed_at": None, "cancelled_at": None,
                "updated_at": "2026-04-21T10:00:00",
                "commit_sha": None, "pr_number": None,
            },
        ]
        result = await svc.enqueue(
            story_id="STORY-480", repo="tech-dev-agents",
            scope="medium", prompt="Dashboard fix",
        )
        assert result["status"] == "pending"
        assert len(conn.execute_called) == 1  # DELETE was called

    @pytest.mark.asyncio
    async def test_reenqueue_active_story_blocked(self, db_service):
        """A pending or claimed story cannot be re-enqueued."""
        svc, conn = db_service
        conn.fetchrow_responses = [{"status": "claimed"}]
        with pytest.raises(DuplicateDispatchError):
            await svc.enqueue(
                story_id="STORY-267", repo="advertising-amazon",
                scope="medium", prompt="test",
            )


# --- Test 5: Completion from pending status ---

class TestCompleteFromPending:
    """An agent may complete a story that's in 'pending' status.

    This happens when: claim succeeds, work runs, another agent's fail()
    released the claim back to pending, but the first agent finishes.
    """

    @pytest.mark.asyncio
    async def test_complete_pending_story_succeeds(self, db_service):
        """A pending story can be completed directly."""
        svc, conn = db_service
        _common = {
            "story_id": "STORY-400", "repo": "advertising-amazon", "scope": "small",
            "prompt": "test", "enqueued_at": "2026-04-21T00:00:00",
            "enqueued_by": "mark", "title": "test",
            "claimed_by": None, "cancelled_at": None,
        }
        # fetch() → pending row found in active-state _resolve_row → drives UPDATE path.
        # fetchrow() → UPDATE RETURNING * returns the completed row.
        conn.fetchrow_responses = [
            {"id": 1, "status": "pending", "commit_sha": None, "pr_number": None,
             "completed_at": None, "updated_at": "2026-04-21T09:00:00", **_common},
            {"id": 1, "status": "completed", "commit_sha": "abc123", "pr_number": None,
             "completed_at": "2026-04-21T10:00:00", "updated_at": "2026-04-21T10:00:00", **_common},
        ]
        result = await svc.complete("STORY-400", commit_sha="abc123")
        assert result["status"] == "completed"


# --- Test 6: Fail from pending status ---

class TestFailFromPending:
    """An agent may fail a story that's in 'pending' status.

    Same pattern as Test 5 but for failures.
    """

    @pytest.mark.asyncio
    async def test_fail_pending_story_succeeds(self, db_service):
        """A pending story can be failed."""
        svc, conn = db_service
        _common = {
            "story_id": "STORY-400", "repo": "advertising-amazon", "scope": "small",
            "prompt": "test", "enqueued_at": "2026-04-21T00:00:00",
            "enqueued_by": "mark", "title": "test",
            "claimed_by": None, "cancelled_at": None,
            "commit_sha": None, "pr_number": None,
        }
        # fetch() → pending row found by _resolve_row → fail() takes the UPDATE path.
        # fetchrow() → UPDATE RETURNING * returns the failed row.
        conn.fetchrow_responses = [
            {"id": 1, "status": "pending", "completed_at": None,
             "updated_at": "2026-04-21T09:00:00", **_common},
            {"id": 1, "status": "failed", "completed_at": "2026-04-21T10:00:00",
             "updated_at": "2026-04-21T10:00:00", **_common},
        ]
        result = await svc.fail("STORY-400")
        assert result["status"] == "failed"


# --- Test 7: Locally completed skip in poller ---

class TestLocallyCompletedSkip:
    """The dispatch poller should never re-claim a story it already completed.

    Daisy completed STORY-267 three times because the completion API returned
    409, the story stayed pending, and she re-claimed it on the next poll.
    """

    def test_locally_completed_prevents_reclaim(self):
        """Stories in _LOCALLY_COMPLETED are skipped in poll_once."""
        from unittest.mock import MagicMock, patch
        from deployment.hermes.dispatch_poller import poll_once, _LOCALLY_COMPLETED

        mock_session = MagicMock()

        next_resp = MagicMock()
        next_resp.status_code = 200
        next_resp.json.return_value = {
            "item": {
                "story_id": "STORY-267",
                "repo": "advertising-amazon",
                "scope": "medium",
                "prompt": "test",
                "enqueued_at": "2026-04-21T01:00:00+00:00",
                "enqueued_by": "mark",
            },
            "queue_depth": 1,
        }

        empty_resp = MagicMock()
        empty_resp.status_code = 204

        mock_session.get.side_effect = [next_resp, empty_resp]

        _LOCALLY_COMPLETED.add("STORY-267")
        try:
            with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
                 patch("deployment.hermes.dispatch_poller.start_story") as mock_start:
                result = poll_once(
                    session=mock_session,
                    base_url="http://ops:8000",
                    api_key="test-key",
                    agent_name="daisy",
                    workspace="/home/hermes/workspace",
                )
            mock_start.assert_not_called()
        finally:
            _LOCALLY_COMPLETED.discard("STORY-267")

    def test_successful_completion_marks_locally_completed(self):
        """After run_sdlc_phases succeeds, story is in _LOCALLY_COMPLETED."""
        import sys
        from deployment.hermes import sdlc_phase_runner
        from deployment.hermes.dispatch_poller import start_story, _LOCALLY_COMPLETED

        sys.modules["sdlc_phase_runner"] = sdlc_phase_runner

        _LOCALLY_COMPLETED.discard("STORY-TEST-LC")
        try:
            with patch.object(sdlc_phase_runner, "run_sdlc_phases") as mock_runner, \
                 patch.dict("os.environ", {"OPS_CONSOLE_URL": "http://ops:8000", "OPS_CONSOLE_API_KEY": "key"}), \
                 patch("deployment.hermes.dispatch_poller._report_complete", return_value=200):
                mock_runner.return_value = (True, "abc123def456")
                start_story(
                    story_id="STORY-TEST-LC",
                    repo="test-repo",
                    scope="small",
                    prompt="test",
                    workspace="/home/hermes/workspace",
                )
                import time; time.sleep(0.5)

            assert "STORY-TEST-LC" in _LOCALLY_COMPLETED
        finally:
            _LOCALLY_COMPLETED.discard("STORY-TEST-LC")

    def test_failed_story_not_marked_locally_completed(self):
        """Failed stories should NOT be in _LOCALLY_COMPLETED."""
        import sys
        from deployment.hermes import sdlc_phase_runner
        from deployment.hermes.dispatch_poller import start_story, _LOCALLY_COMPLETED

        sys.modules["sdlc_phase_runner"] = sdlc_phase_runner

        _LOCALLY_COMPLETED.discard("STORY-TEST-FAIL")
        try:
            with patch.object(sdlc_phase_runner, "run_sdlc_phases") as mock_runner, \
                 patch.dict("os.environ", {"OPS_CONSOLE_URL": "", "OPS_CONSOLE_API_KEY": ""}):
                mock_runner.return_value = (False, None)
                start_story(
                    story_id="STORY-TEST-FAIL",
                    repo="test-repo",
                    scope="small",
                    prompt="test",
                    workspace="/home/hermes/workspace",
                )
                import time; time.sleep(0.5)

            assert "STORY-TEST-FAIL" not in _LOCALLY_COMPLETED
        finally:
            _LOCALLY_COMPLETED.discard("STORY-TEST-FAIL")
