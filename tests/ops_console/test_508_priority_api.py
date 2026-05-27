"""STORY-508 AC-14: Priority API tests — PriorityRequest/PriorityResponse models,
DispatchItem.priority field, DB set_priority() / next_pending() ordering,
and the POST /api/dispatch/priority route.

Phase 7 — RED state.

RED reasons:
- PriorityRequest and PriorityResponse do not yet exist in responses.py
- DispatchItem has no priority field
- DispatchDBService has no set_priority() method
- next_pending() does not yet consider priority
- POST /api/dispatch/priority route does not yet exist
- scripts/migrations/006_dispatch_priority.sql does not yet exist

All tests pass after Phase 8 implementation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"
REPO_ROOT = Path(__file__).parent.parent.parent


def _pg_is_reachable() -> bool:
    """Check if the test PostgreSQL database is reachable."""

    async def _check():
        try:
            conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_check())
    except Exception:
        return False


_PG_AVAILABLE = _pg_is_reachable()
_pg_skip = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="PostgreSQL ops_console_test not reachable"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_pool():
    """Connection pool to test database, truncated between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def dispatch_db_service(db_pool):
    """DispatchDBService backed by the test pool."""
    from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

    return DispatchDBService(db_pool)


def _inject_dispatch(app, dispatch_db_service):
    inject_mock_services(app, dispatch_db_service=dispatch_db_service)


def _enqueue_payload(
    story_id: str = "STORY-508",
    repo: str = "tech-dev-agents",
    scope: str = "medium",
    prompt: str = "Start Phase 8 for STORY-508",
    enqueued_by: str = "mark",
) -> dict:
    return {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "enqueued_by": enqueued_by,
    }


# ---------------------------------------------------------------------------
# AC-14: Model tests (no PG required)
# ---------------------------------------------------------------------------


class TestPriorityModels:
    """AC-14: PriorityRequest and PriorityResponse Pydantic models."""

    def test_priority_request_importable(self):
        """AC-14: PriorityRequest must be importable from responses.py.

        RED: Class does not yet exist.
        GREEN after: Add PriorityRequest(BaseModel) with story_id: str, priority: int to responses.py.
        """
        try:
            from tech_dev_agents.ops_console.models.responses import PriorityRequest  # noqa: F401
        except ImportError as exc:
            pytest.fail(
                f"Cannot import PriorityRequest from responses.py: {exc}. "
                "AC-14 requires adding:\n"
                "  class PriorityRequest(BaseModel):\n"
                "      story_id: str\n"
                "      priority: int = Field(..., ge=0, le=100)"
            )

    def test_priority_response_importable(self):
        """AC-14: PriorityResponse must be importable from responses.py.

        RED: Class does not yet exist.
        GREEN after: Add PriorityResponse(BaseModel) to responses.py.
        """
        try:
            from tech_dev_agents.ops_console.models.responses import PriorityResponse  # noqa: F401
        except ImportError as exc:
            pytest.fail(
                f"Cannot import PriorityResponse from responses.py: {exc}. "
                "AC-14 requires adding:\n"
                "  class PriorityResponse(BaseModel):\n"
                "      story_id: str\n"
                "      priority: int\n"
                "      previous_priority: int\n"
                "      status: str"
            )

    def test_priority_request_has_story_id_field(self):
        """AC-14: PriorityRequest must have story_id (str) field."""
        try:
            from tech_dev_agents.ops_console.models.responses import PriorityRequest
        except ImportError:
            pytest.skip("PriorityRequest not yet importable")

        req = PriorityRequest(story_id="STORY-508", priority=50)
        assert req.story_id == "STORY-508", (
            "PriorityRequest.story_id field is missing or not set correctly. "
            "Expected: story_id: str"
        )

    def test_priority_request_has_priority_field(self):
        """AC-14: PriorityRequest.priority must be int in range 0-100."""
        try:
            from tech_dev_agents.ops_console.models.responses import PriorityRequest
        except ImportError:
            pytest.skip("PriorityRequest not yet importable")

        req = PriorityRequest(story_id="STORY-508", priority=75)
        assert req.priority == 75, (
            "PriorityRequest.priority field is missing or not set correctly. "
            "Expected: priority: int = Field(..., ge=0, le=100)"
        )

    def test_priority_request_rejects_negative_priority(self):
        """AC-14: PriorityRequest must reject priority < 0 (validation error)."""
        try:
            from pydantic import ValidationError
            from tech_dev_agents.ops_console.models.responses import PriorityRequest
        except ImportError:
            pytest.skip("PriorityRequest not yet importable")

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PriorityRequest(story_id="STORY-508", priority=-1)

    def test_priority_request_rejects_priority_over_100(self):
        """AC-14: PriorityRequest must reject priority > 100 (validation error)."""
        try:
            from pydantic import ValidationError
            from tech_dev_agents.ops_console.models.responses import PriorityRequest
        except ImportError:
            pytest.skip("PriorityRequest not yet importable")

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PriorityRequest(story_id="STORY-508", priority=101)

    def test_priority_response_has_required_fields(self):
        """AC-14: PriorityResponse must have story_id, priority, previous_priority, status."""
        try:
            from tech_dev_agents.ops_console.models.responses import PriorityResponse
        except ImportError:
            pytest.skip("PriorityResponse not yet importable")

        resp = PriorityResponse(
            story_id="STORY-508",
            priority=75,
            previous_priority=0,
            status="updated",
        )
        assert resp.story_id == "STORY-508"
        assert resp.priority == 75
        assert resp.previous_priority == 0
        assert resp.status == "updated"


# ---------------------------------------------------------------------------
# AC-14: Migration file tests (no PG required)
# ---------------------------------------------------------------------------


class TestPriorityMigration:
    """AC-14: Migration file 006_dispatch_priority.sql must exist and be correct."""

    MIGRATION_FILE = REPO_ROOT / "scripts" / "migrations" / "006_dispatch_priority.sql"

    def test_migration_file_exists(self):
        """AC-14: scripts/migrations/006_dispatch_priority.sql must exist.

        RED: File not yet created.
        GREEN after: Create the migration file.
        """
        assert self.MIGRATION_FILE.exists(), (
            f"Migration file not found at {self.MIGRATION_FILE}. "
            "AC-14 requires creating scripts/migrations/006_dispatch_priority.sql "
            "containing: ALTER TABLE dispatch_items ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;"
        )

    def test_migration_contains_add_column_priority(self):
        """AC-14: Migration must contain ADD COLUMN priority on dispatch_items.

        RED: File not yet created.
        """
        if not self.MIGRATION_FILE.exists():
            pytest.skip("006_dispatch_priority.sql not yet created")
        content = self.MIGRATION_FILE.read_text().upper()
        assert "ADD COLUMN" in content and "PRIORITY" in content, (
            "006_dispatch_priority.sql does not contain ADD COLUMN priority. "
            "Expected: ALTER TABLE dispatch_items ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;"
        )

    def test_migration_adds_index_for_ordering(self):
        """AC-14: Migration should add an index to support priority ordering in next_pending().

        An index on (status, priority DESC, enqueued_at ASC) ensures efficient ordering.
        RED: File not yet created.
        """
        if not self.MIGRATION_FILE.exists():
            pytest.skip("006_dispatch_priority.sql not yet created")
        content = self.MIGRATION_FILE.read_text().upper()
        has_index = "CREATE INDEX" in content or "INDEX" in content
        assert has_index, (
            "006_dispatch_priority.sql should create an index to support priority ordering. "
            "Suggested: CREATE INDEX idx_dispatch_priority ON dispatch_items(status, priority DESC, enqueued_at ASC);"
        )


# ---------------------------------------------------------------------------
# AC-14: DB service tests (PG required)
# ---------------------------------------------------------------------------


class TestDispatchPriorityDB:
    """AC-14: DispatchDBService.set_priority() and next_pending() priority ordering."""

    @_pg_skip
    @pytest.mark.asyncio
    async def test_dispatch_item_has_priority_field(self, client, app, dispatch_db_service):
        """AC-14: DispatchItem returned by enqueue must have a priority field defaulting to 0.

        RED: priority field not yet added to DispatchItem / DB schema.
        GREEN after: Add priority column to dispatch_items table and expose in row dict.
        """
        _inject_dispatch(app, dispatch_db_service)
        resp = await client.post("/api/dispatch", json=_enqueue_payload("STORY-P01"))
        assert resp.status_code == 201, f"Enqueue failed: {resp.text}"
        data = resp.json()
        assert "priority" in data["item"], (
            "DispatchItem in enqueue response has no 'priority' field. "
            "AC-14 requires adding priority INTEGER NOT NULL DEFAULT 0 to dispatch_items table "
            "and including it in the _row_to_dict() conversion."
        )
        assert data["item"]["priority"] == 0, (
            f"Expected priority=0 (default), got {data['item']['priority']}. "
            "The default priority for newly enqueued stories is 0 (normal priority)."
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_set_priority_updates_pending_story(self, dispatch_db_service):
        """AC-14: set_priority() must update priority on a pending story and return the updated row.

        RED: set_priority() method does not yet exist on DispatchDBService.
        GREEN after: Implement set_priority(story_id, priority) in dispatch_db_service.py.
        """
        # Enqueue a story directly via DB service
        await dispatch_db_service.enqueue(
            story_id="STORY-P10",
            repo="tech-dev-agents",
            scope="small",
            prompt="test",
            enqueued_by="mark",
        )
        if not hasattr(dispatch_db_service, "set_priority"):
            pytest.fail(
                "DispatchDBService has no set_priority() method. "
                "AC-14 requires implementing:\n"
                "  async def set_priority(self, story_id: str, priority: int) -> dict:\n"
                "      Raises NotFoundError if story not found.\n"
                "      Raises InvalidTransitionError if story is claimed/completed/cancelled.\n"
                "      Returns updated row dict."
            )
        result = await dispatch_db_service.set_priority("STORY-P10", 80)
        assert result["story_id"] == "STORY-P10", (
            "set_priority() should return the updated row dict with story_id."
        )
        assert result["priority"] == 80, (
            f"set_priority() returned priority={result.get('priority')}, expected 80. "
            "The method must UPDATE dispatch_items SET priority = $2 WHERE story_id = $1."
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_set_priority_raises_not_found_for_unknown_story(self, dispatch_db_service):
        """AC-14: set_priority() must raise NotFoundError for an unknown story_id.

        RED: Method does not yet exist.
        """
        if not hasattr(dispatch_db_service, "set_priority"):
            pytest.skip("set_priority() not yet implemented")

        from tech_dev_agents.ops_console.services.dispatch_db_service import NotFoundError

        with pytest.raises(NotFoundError):
            await dispatch_db_service.set_priority("STORY-XXXX", 50)

    @_pg_skip
    @pytest.mark.asyncio
    async def test_set_priority_raises_invalid_transition_for_claimed_story(
        self, dispatch_db_service
    ):
        """AC-14: set_priority() must raise InvalidTransitionError for a claimed story.

        Rationale: Priority only makes sense for pending stories in the queue.
        A claimed story is already being worked on — reordering it in the queue is meaningless.

        RED: Method does not yet exist.
        """
        if not hasattr(dispatch_db_service, "set_priority"):
            pytest.skip("set_priority() not yet implemented")

        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            InvalidTransitionError,
        )

        await dispatch_db_service.enqueue(
            story_id="STORY-P11",
            repo="tech-dev-agents",
            scope="small",
            prompt="test",
            enqueued_by="mark",
        )
        await dispatch_db_service.claim("STORY-P11", "dan")

        with pytest.raises(InvalidTransitionError):
            await dispatch_db_service.set_priority("STORY-P11", 90)

    @_pg_skip
    @pytest.mark.asyncio
    async def test_next_pending_returns_higher_priority_first(self, dispatch_db_service):
        """AC-14: next_pending() must return the highest-priority pending item first.

        RED: next_pending() currently uses FIFO (ORDER BY enqueued_at ASC) only.
        GREEN after: Modify next_pending() to ORDER BY priority DESC, enqueued_at ASC.

        Scenario: STORY-LOW enqueued first (priority=0), STORY-HIGH enqueued second (priority=90).
        next_pending() must return STORY-HIGH.
        """
        if not hasattr(dispatch_db_service, "set_priority"):
            pytest.skip("set_priority() not yet implemented")

        # Enqueue low-priority story first
        await dispatch_db_service.enqueue(
            story_id="STORY-LOW",
            repo="tech-dev-agents",
            scope="small",
            prompt="low priority",
            enqueued_by="mark",
        )
        # Enqueue high-priority story second
        await dispatch_db_service.enqueue(
            story_id="STORY-HIGH",
            repo="tech-dev-agents",
            scope="small",
            prompt="high priority",
            enqueued_by="mark",
        )
        # Bump STORY-HIGH's priority
        await dispatch_db_service.set_priority("STORY-HIGH", 90)

        result = await dispatch_db_service.next_pending()
        assert result is not None, "next_pending() returned None with 2 pending stories"
        assert result["story_id"] == "STORY-HIGH", (
            f"next_pending() returned {result['story_id']!r} instead of 'STORY-HIGH'. "
            "AC-14 requires changing next_pending() to ORDER BY priority DESC, enqueued_at ASC "
            "so that higher-priority items are dequeued first."
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_next_pending_fifo_for_equal_priority(self, dispatch_db_service):
        """AC-14: next_pending() must use FIFO ordering for stories with equal priority.

        When two stories have the same priority, the one enqueued first should be returned first.

        RED: This test may pass today (FIFO is current behavior), but serves as a regression
        guard to ensure the FIFO tie-break is preserved after the priority sort is added.
        """
        await dispatch_db_service.enqueue(
            story_id="STORY-F01",
            repo="tech-dev-agents",
            scope="small",
            prompt="first",
            enqueued_by="mark",
        )
        await dispatch_db_service.enqueue(
            story_id="STORY-F02",
            repo="tech-dev-agents",
            scope="small",
            prompt="second",
            enqueued_by="mark",
        )

        result = await dispatch_db_service.next_pending()
        assert result is not None
        assert result["story_id"] == "STORY-F01", (
            f"next_pending() returned {result['story_id']!r} instead of 'STORY-F01' (first enqueued). "
            "FIFO tie-break must be preserved when priorities are equal. "
            "Ensure ORDER BY priority DESC, enqueued_at ASC."
        )


# ---------------------------------------------------------------------------
# AC-14: Route tests (use app/client fixtures from conftest)
# ---------------------------------------------------------------------------


class TestPriorityRoute:
    """AC-14: POST /api/dispatch/priority route."""

    @_pg_skip
    @pytest.mark.asyncio
    async def test_set_priority_returns_200_with_priority_response(
        self, client, app, dispatch_db_service
    ):
        """AC-14: POST /api/dispatch/priority with valid payload returns 200 and PriorityResponse.

        RED: Endpoint does not yet exist (returns 404).
        GREEN after: Add POST /api/dispatch/priority route to dispatch.py.
        """
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-PR01"))

        resp = await client.post(
            "/api/dispatch/priority",
            json={"story_id": "STORY-PR01", "priority": 80},
        )

        assert resp.status_code == 200, (
            f"POST /api/dispatch/priority returned {resp.status_code}, expected 200. "
            "AC-14 requires adding this route to dispatch.py. "
            f"Response body: {resp.text}"
        )
        data = resp.json()
        assert data["story_id"] == "STORY-PR01", (
            f"Response story_id={data.get('story_id')!r}, expected 'STORY-PR01'."
        )
        assert data["priority"] == 80, (
            f"Response priority={data.get('priority')}, expected 80."
        )
        assert "previous_priority" in data, (
            "PriorityResponse must include 'previous_priority' field."
        )
        assert data["previous_priority"] == 0, (
            f"Expected previous_priority=0 (default), got {data.get('previous_priority')}."
        )
        assert "status" in data, "PriorityResponse must include 'status' field."

    @_pg_skip
    @pytest.mark.asyncio
    async def test_set_priority_unknown_story_returns_404(
        self, client, app, dispatch_db_service
    ):
        """AC-14: POST /api/dispatch/priority for unknown story_id returns 404.

        RED: Endpoint does not yet exist.
        """
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/priority",
            json={"story_id": "STORY-UNKNOWN", "priority": 50},
        )

        assert resp.status_code == 404, (
            f"Expected 404 for unknown story_id, got {resp.status_code}. "
            "The route must raise HTTPException(404) when set_priority() raises NotFoundError."
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_set_priority_claimed_story_returns_422(
        self, client, app, dispatch_db_service
    ):
        """AC-14: POST /api/dispatch/priority for a claimed story returns 422.

        RED: Endpoint does not yet exist.
        """
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-PR02"))
        await client.post("/api/dispatch/claim/STORY-PR02", json={"agent_name": "dan"})

        resp = await client.post(
            "/api/dispatch/priority",
            json={"story_id": "STORY-PR02", "priority": 90},
        )

        assert resp.status_code == 422, (
            f"Expected 422 for claimed story, got {resp.status_code}. "
            "The route must raise HTTPException(422) when set_priority() raises InvalidTransitionError."
        )

    @pytest.mark.asyncio
    async def test_set_priority_negative_value_returns_422(self, client, app):
        """AC-14: POST /api/dispatch/priority with priority=-1 returns 422 (Pydantic validation).

        This test does NOT require PG — Pydantic validates before the route handler runs.
        RED: Endpoint does not yet exist (returns 404 currently).
        GREEN after: Add route with PriorityRequest body validation.
        """
        resp = await client.post(
            "/api/dispatch/priority",
            json={"story_id": "STORY-PR03", "priority": -1},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for priority=-1 (out of range), got {resp.status_code}. "
            "PriorityRequest must use Field(..., ge=0, le=100) so FastAPI auto-validates."
        )

    @pytest.mark.asyncio
    async def test_set_priority_over_100_returns_422(self, client, app):
        """AC-14: POST /api/dispatch/priority with priority=101 returns 422 (Pydantic validation).

        RED: Endpoint does not yet exist (returns 404 currently).
        """
        resp = await client.post(
            "/api/dispatch/priority",
            json={"story_id": "STORY-PR04", "priority": 101},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for priority=101 (out of range), got {resp.status_code}. "
            "PriorityRequest must use Field(..., ge=0, le=100)."
        )

    @pytest.mark.asyncio
    async def test_set_priority_requires_auth(self, unauthed_client):
        """AC-14: POST /api/dispatch/priority requires API key authentication.

        The endpoint must be under the auth dependency like all other dispatch routes.
        """
        resp = await unauthed_client.post(
            "/api/dispatch/priority",
            json={"story_id": "STORY-PR05", "priority": 50},
        )
        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated request, got {resp.status_code}. "
            "The priority route must be protected by require_auth dependency."
        )
