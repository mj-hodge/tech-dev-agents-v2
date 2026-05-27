"""STORY-507: Paused Status — API Route Integration Tests (Group E)

Phase 7 — RED-state tests. All tests FAIL until Phase 8 implementation is complete.

Coverage:
  E-01: POST /api/dispatch/pause/{story_id} returns 200 with paused item
  E-02: POST /api/dispatch/pause/{story_id} response schema (status, paused_at, current_phase)
  E-03: POST /api/dispatch/pause/{story_id} returns 404 when story not found
  E-04: POST /api/dispatch/pause/{story_id} returns 409 when story is pending (not claimed)
  E-05: GET /api/dispatch/queue includes paused items
  E-06: GET /api/dispatch/next returns a paused story (when no pending items)

Requires PostgreSQL ops_console_test (same as test_routes_dispatch.py).
Run migrations 001–004 before testing.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


def _pg_is_reachable() -> bool:
    async def _check() -> bool:
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
pytestmark = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="PostgreSQL ops_console_test not reachable"
)


@pytest_asyncio.fixture
async def db_pool():
    """asyncpg pool to test database; truncate between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def dispatch_db_service(db_pool) -> DispatchDBService:
    return DispatchDBService(db_pool)


def _inject_dispatch(app, svc: DispatchDBService) -> None:
    """Inject dispatch DB service into app.state (same pattern as test_routes_dispatch.py)."""
    inject_mock_services(app, dispatch_db_service=svc)


def _enqueue_payload(
    story_id: str = "STORY-507",
    scope: str = "large",
) -> dict:
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": scope,
        "prompt": "Phase 7 route test — STORY-507 resume-aware phase runner",
        "enqueued_by": "hermes",
    }


# ---------------------------------------------------------------------------
# Group E — Pause Endpoint [AC-5, AC-6]
# ---------------------------------------------------------------------------

class TestPauseEndpoint:
    """E-01 through E-04: POST /api/dispatch/pause/{story_id}

    All tests RED: endpoint does not exist yet.
    """

    @pytest.mark.asyncio
    async def test_pause_endpoint_returns_200(self, client, app, dispatch_db_service):
        """E-01: POST /api/dispatch/pause/{story_id} returns 200 on a claimed story."""
        _inject_dispatch(app, dispatch_db_service)

        # Enqueue and claim STORY-507
        enq = await client.post("/api/dispatch", json=_enqueue_payload())
        assert enq.status_code == 201, f"Enqueue failed: {enq.status_code} {enq.text}"

        claim = await client.post(
            "/api/dispatch/claim/STORY-507",
            json={"agent": "daisy"},
        )
        assert claim.status_code == 200, f"Claim failed: {claim.status_code} {claim.text}"

        # Pause — endpoint does not exist yet (RED: will return 404 or 405)
        pause = await client.post(
            "/api/dispatch/pause/STORY-507",
            json={"agent": "daisy", "current_phase": 6, "reason": "sigterm"},
        )
        assert pause.status_code == 200, (
            f"POST /api/dispatch/pause/STORY-507 returned {pause.status_code} "
            f"(expected 200) — endpoint not yet implemented.\n"
            f"Response: {pause.text}\n"
            "Add POST /api/dispatch/pause/{story_id} to routes/dispatch.py (AC-5)"
        )

    @pytest.mark.asyncio
    async def test_pause_endpoint_response_schema(self, client, app, dispatch_db_service):
        """E-02: Pause response must include status='paused', paused_at, current_phase."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload(story_id="STORY-507"))
        await client.post("/api/dispatch/claim/STORY-507", json={"agent": "daisy"})

        pause = await client.post(
            "/api/dispatch/pause/STORY-507",
            json={"agent": "daisy", "current_phase": 6, "reason": "sigterm"},
        )
        assert pause.status_code == 200, (
            f"Pause endpoint returned {pause.status_code} — not yet implemented. "
            f"Response: {pause.text}"
        )
        body = pause.json()

        assert body.get("status") == "paused", (
            f"Response 'status' is {body.get('status')!r}, expected 'paused': {body}"
        )
        assert body.get("paused_at") is not None, (
            f"Response 'paused_at' is None — must be set on pause: {body}"
        )
        assert body.get("current_phase") == 6, (
            f"Response 'current_phase' is {body.get('current_phase')!r}, expected 6: {body}"
        )
        assert body.get("story_id") == "STORY-507", (
            f"Response 'story_id' is {body.get('story_id')!r}: {body}"
        )

    @pytest.mark.asyncio
    async def test_pause_endpoint_404_on_missing_story(self, client, app, dispatch_db_service):
        """E-03: POST /api/dispatch/pause/{story_id} returns 404 when story not in queue."""
        _inject_dispatch(app, dispatch_db_service)

        pause = await client.post(
            "/api/dispatch/pause/STORY-NONEXISTENT",
            json={"agent": "daisy", "reason": "sigterm"},
        )
        # If endpoint doesn't exist → 404 or 405. We want 404 meaning "not found".
        # A 405 means the route path exists but wrong method — also indicates endpoint missing.
        # Either way, test will be GREEN only when the endpoint exists AND returns 404 for unknown.
        assert pause.status_code == 404, (
            f"Expected 404 for unknown story, got {pause.status_code}.\n"
            f"Response: {pause.text}\n"
            "If 404 is from 'route not found' (endpoint missing), add the pause endpoint first."
        )

    @pytest.mark.asyncio
    async def test_pause_endpoint_409_on_pending_story(self, client, app, dispatch_db_service):
        """E-04: POST /api/dispatch/pause/{story_id} returns 409 when story is pending."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload(story_id="STORY-507"))
        # Do NOT claim — story remains pending

        pause = await client.post(
            "/api/dispatch/pause/STORY-507",
            json={"agent": "daisy", "reason": "sigterm"},
        )
        assert pause.status_code == 409, (
            f"Expected 409 when pausing a pending story, got {pause.status_code}.\n"
            f"Response: {pause.text}\n"
            "Only claimed items can be paused — pending items were never started."
        )


class TestQueueIncludesPaused:
    """E-05, E-06: Queue and next endpoints must include paused items."""

    @pytest.mark.asyncio
    async def test_queue_endpoint_includes_paused_items(self, client, app, dispatch_db_service):
        """E-05: GET /api/dispatch/queue includes items with status='paused'."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload(story_id="STORY-507"))
        await client.post("/api/dispatch/claim/STORY-507", json={"agent": "daisy"})

        pause = await client.post(
            "/api/dispatch/pause/STORY-507",
            json={"agent": "daisy", "reason": "rate_limit"},
        )
        if pause.status_code != 200:
            pytest.fail(
                f"Pause endpoint not yet implemented (needed for E-05): "
                f"{pause.status_code} {pause.text}"
            )

        queue = await client.get("/api/dispatch/queue")
        assert queue.status_code == 200, f"Queue returned {queue.status_code}: {queue.text}"

        body = queue.json()
        items = body if isinstance(body, list) else body.get("items", body.get("queue", []))
        story_ids = [item.get("story_id") for item in items]

        assert "STORY-507" in story_ids, (
            f"STORY-507 (paused) not in queue response — "
            f"GET /api/dispatch/queue must include paused items.\n"
            f"Items in response: {story_ids}"
        )
        statuses = [item.get("status") for item in items]
        assert "paused" in statuses, (
            f"No 'paused' status in queue response statuses: {statuses}"
        )

    @pytest.mark.asyncio
    async def test_next_endpoint_returns_paused_story(self, client, app, dispatch_db_service):
        """E-06: GET /api/dispatch/next returns a paused story when queue has no pending items."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload(story_id="STORY-507"))
        await client.post("/api/dispatch/claim/STORY-507", json={"agent": "daisy"})

        pause = await client.post(
            "/api/dispatch/pause/STORY-507",
            json={"agent": "daisy", "reason": "session_cap"},
        )
        if pause.status_code != 200:
            pytest.fail(
                f"Pause endpoint not yet implemented (needed for E-06): "
                f"{pause.status_code} {pause.text}"
            )

        # Queue only has a paused item — next should return it
        next_resp = await client.get(
            "/api/dispatch/next",
            headers={"X-Agent-Name": "derrick"},
        )
        assert next_resp.status_code == 200, (
            f"GET /api/dispatch/next returned {next_resp.status_code} "
            f"(expected 200 with paused story, not 204 empty) — "
            f"next_pending() must include status='paused' items.\n"
            f"Body: {next_resp.text}"
        )
        body = next_resp.json()
        assert body.get("story_id") == "STORY-507", (
            f"Expected STORY-507 (paused), got {body.get('story_id')!r}: {body}"
        )
        assert body.get("status") == "paused", (
            f"Expected status='paused', got {body.get('status')!r}: {body}"
        )

    @pytest.mark.asyncio
    async def test_queue_paused_items_include_current_phase(self, client, app, dispatch_db_service):
        """E-05 extended: Paused items in queue response include current_phase."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload(story_id="STORY-507"))
        await client.post("/api/dispatch/claim/STORY-507", json={"agent": "daisy"})

        pause = await client.post(
            "/api/dispatch/pause/STORY-507",
            json={"agent": "daisy", "current_phase": 7, "reason": "sigterm"},
        )
        if pause.status_code != 200:
            pytest.fail(
                f"Pause endpoint not yet implemented (needed for phase progress test): "
                f"{pause.status_code} {pause.text}"
            )

        queue = await client.get("/api/dispatch/queue")
        assert queue.status_code == 200
        body = queue.json()
        items = body if isinstance(body, list) else body.get("items", body.get("queue", []))
        story_507 = next((i for i in items if i.get("story_id") == "STORY-507"), None)

        assert story_507 is not None, "STORY-507 not found in queue response"
        assert story_507.get("current_phase") == 7, (
            f"current_phase not in queue item or wrong value: {story_507}"
        )
