"""STORY-532: needs_info dispatch state — service and route tests.

All tests are RED until Phase 8 implementation is complete.

Group A — Service: DispatchDBService.needs_info() / resume_from_needs_info()
  A-01: needs_info() transitions claimed → needs_info; sets needs_info_path
  A-02: needs_info() raises InvalidTransitionError on pending story
  A-03: needs_info() raises InvalidTransitionError on already needs_info story
  A-04: needs_info() raises NotFoundError on unknown story_id
  A-05: next_pending() does NOT return a needs_info story
  A-06: list_queue() returns needs_info stories in a separate 'needs_info' bucket
  A-07: resume_from_needs_info() transitions needs_info → pending; clears path
  A-08: resume_from_needs_info() raises InvalidTransitionError on pending story

Group B — Index: unique active index includes needs_info
  B-01: enqueue() raises DuplicateDispatchError when story is needs_info

Group C — Routes: POST /api/dispatch/needs-info/{id}, POST /api/dispatch/resume/{id}
  C-01: POST /api/dispatch/needs-info/{id} returns 200 on claimed story
  C-02: POST /api/dispatch/needs-info/{id} response schema (status, needs_info_path)
  C-03: POST /api/dispatch/needs-info/{id} returns 404 when story not found
  C-04: POST /api/dispatch/needs-info/{id} returns 409 when story is pending
  C-05: POST /api/dispatch/resume/{id} returns 200 and transitions to pending
  C-06: POST /api/dispatch/resume/{id} returns 404 when story not needs_info
  C-07: GET /api/dispatch/queue includes needs_info bucket
  C-08: GET /api/dispatch/next does NOT return needs_info story (returns 204)

Requires PostgreSQL ops_console_test for Groups A, B, and C (route integration).
Run migrations 001–007 before executing these tests.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
    DuplicateDispatchError,
    InvalidTransitionError,
    NotFoundError,
)

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


# ---------------------------------------------------------------------------
# PostgreSQL availability guard (same pattern as test_507_paused_routes.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_pool():
    """asyncpg pool to test database; truncate dispatch_items between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def dispatch_db_service(db_pool) -> DispatchDBService:
    return DispatchDBService(db_pool)


def _enqueue_payload(story_id: str = "STORY-532", scope: str = "medium") -> dict:
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": scope,
        "prompt": "STORY-532 test prompt — needs_info state",
        "enqueued_by": "hermes",
    }


def _inject(app, svc: DispatchDBService) -> None:
    inject_mock_services(app, dispatch_db_service=svc)


# ---------------------------------------------------------------------------
# Group A — Service-layer
# ---------------------------------------------------------------------------

class TestNeedsInfoService:
    """A-01 through A-08: DispatchDBService.needs_info() and resume_from_needs_info()."""

    @pytest.mark.asyncio
    async def test_needs_info_transitions_claimed_to_needs_info(self, dispatch_db_service):
        """A-01: needs_info() transitions claimed → needs_info and stores the path.

        RED: DispatchDBService.needs_info() method does not exist yet.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")

        row = await svc.needs_info(
            story_id="STORY-532",
            question_file_path="features/story-532/QUESTION.md",
        )

        assert row["status"] == "needs_info", (
            f"Expected status='needs_info' after transition, got {row['status']!r}.\n"
            "Implement DispatchDBService.needs_info() in dispatch_db_service.py (STORY-532)"
        )
        assert row["needs_info_path"] == "features/story-532/QUESTION.md", (
            f"needs_info_path not set: {row.get('needs_info_path')!r}.\n"
            "Add needs_info_path column via migration 007_needs_info_state.sql"
        )
        assert row["story_id"] == "STORY-532"

    @pytest.mark.asyncio
    async def test_needs_info_raises_on_pending_story(self, dispatch_db_service):
        """A-02: needs_info() raises InvalidTransitionError when story is pending (not claimed).

        RED: method does not exist yet.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        # Do NOT claim — story remains pending

        with pytest.raises(InvalidTransitionError, match="pending|not claimed|Only claimed"):
            await svc.needs_info(
                story_id="STORY-532",
                question_file_path="features/story-532/QUESTION.md",
            )

    @pytest.mark.asyncio
    async def test_needs_info_is_idempotent_on_already_needs_info_story(self, dispatch_db_service):
        """A-03 (revised): needs_info() is IDEMPOTENT on already-needs_info stories.

        Tonight's STORY-528 incident (2026-04-24 00:30:32Z): the runner called
        /needs-info on a story already in needs_info and got 409, which drove
        the fallback-to-/fail + auto-retry ghost-claim loop. The endpoint is a
        signal-not-a-transition: "agent still has a question" — no error.

        Expected behavior: second call returns the row (status=needs_info)
        without raising; the stored needs_info_path is updated if it changed.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")
        row1 = await svc.needs_info("STORY-532", "features/story-532/QUESTION.md")
        assert row1["status"] == "needs_info"

        # Second call on already-needs_info with same path: idempotent, no raise.
        row2 = await svc.needs_info(
            story_id="STORY-532",
            question_file_path="features/story-532/QUESTION.md",
        )
        assert row2["status"] == "needs_info", (
            f"Second needs_info() should stay in needs_info, got {row2['status']!r}"
        )
        assert row2["needs_info_path"] == "features/story-532/QUESTION.md"

        # Third call with a DIFFERENT path: should UPDATE the stored path, still no raise.
        row3 = await svc.needs_info(
            story_id="STORY-532",
            question_file_path="features/story-532/QUESTION-v2.md",
        )
        assert row3["status"] == "needs_info"
        assert row3["needs_info_path"] == "features/story-532/QUESTION-v2.md", (
            f"Idempotent call with new path must update needs_info_path, "
            f"got {row3['needs_info_path']!r}"
        )

    @pytest.mark.asyncio
    async def test_needs_info_raises_on_terminal_state(self, dispatch_db_service):
        """A-03b: needs_info() still raises on terminal states (completed/cancelled/failed).

        Idempotency only applies to non-terminal states. A "still has a question"
        signal on a finished story is a bug — surface it as 409.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")
        # Drive the story to 'completed' via the service's complete() path.
        await svc.complete("STORY-532", commit_sha="a" * 40)

        with pytest.raises(InvalidTransitionError):
            await svc.needs_info(
                story_id="STORY-532",
                question_file_path="features/story-532/QUESTION.md",
            )

    @pytest.mark.asyncio
    async def test_needs_info_raises_on_unknown_story(self, dispatch_db_service):
        """A-04: needs_info() raises NotFoundError for unknown story_id.

        RED: method does not exist yet.
        """
        with pytest.raises(NotFoundError):
            await dispatch_db_service.needs_info(
                story_id="STORY-NONEXISTENT",
                question_file_path="features/story-nonexistent/QUESTION.md",
            )

    @pytest.mark.asyncio
    async def test_next_pending_excludes_needs_info(self, dispatch_db_service):
        """A-05: next_pending() does NOT return a needs_info story.

        This is the CRITICAL test — needs_info must be invisible to agents.

        RED: method does not exist yet (and even when it exists, the exclusion
        must be explicit since it is NOT the default behavior for 'paused').
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")
        await svc.needs_info("STORY-532", "features/story-532/QUESTION.md")

        # Queue now has only one story, in needs_info state.
        result = await svc.next_pending()

        assert result is None, (
            f"next_pending() returned {result!r} — expected None.\n"
            "needs_info stories MUST be invisible to the agent poller.\n"
            "Ensure next_pending() WHERE clause is: status IN ('pending', 'paused')\n"
            "and 'needs_info' is deliberately absent."
        )

    @pytest.mark.asyncio
    async def test_list_queue_includes_needs_info_bucket(self, dispatch_db_service):
        """A-06: list_queue() returns needs_info stories in a 'needs_info' bucket.

        RED: method does not exist yet; 'needs_info' bucket not returned.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")
        await svc.needs_info("STORY-532", "features/story-532/QUESTION.md")

        queue = await svc.list_queue()

        assert "needs_info" in queue, (
            f"list_queue() response missing 'needs_info' key. Keys: {list(queue.keys())}\n"
            "Add needs_info bucket to list_queue() in dispatch_db_service.py (STORY-532)"
        )
        needs_info_items = queue["needs_info"]
        assert len(needs_info_items) == 1, (
            f"Expected 1 needs_info item, got {len(needs_info_items)}: {needs_info_items}"
        )
        assert needs_info_items[0]["story_id"] == "STORY-532"
        assert needs_info_items[0]["status"] == "needs_info"

    @pytest.mark.asyncio
    async def test_resume_from_needs_info_transitions_to_pending(self, dispatch_db_service):
        """A-07: resume_from_needs_info() transitions needs_info → pending.

        2026-04-24 (resume-deletes-question-md fix): needs_info_path is now
        PRESERVED on resume so that the next /claim response can signal the
        phase runner to delete+commit QUESTION.md. Previously the path was
        NULLed on resume, which meant the phase runner had no way to know it
        was resuming a needs_info story and the stale QUESTION.md re-fired the
        needs_info loop forever.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")
        await svc.needs_info("STORY-532", "features/story-532/QUESTION.md")

        row = await svc.resume_from_needs_info("STORY-532")

        assert row["status"] == "pending", (
            f"Expected status='pending' after resume, got {row['status']!r}.\n"
            "Implement DispatchDBService.resume_from_needs_info() (STORY-532)"
        )
        # resume-deletes-question-md: preserve the path — the next /claim
        # uses its presence as the resume signal.
        assert row.get("needs_info_path") == "features/story-532/QUESTION.md", (
            f"needs_info_path should be preserved on resume so the next /claim "
            f"can signal phase runner to delete+commit QUESTION.md. "
            f"Got {row.get('needs_info_path')!r}."
        )

        # Confirm story is now claimable via next_pending
        next_item = await svc.next_pending()
        assert next_item is not None, "Story should be claimable after resume."
        assert next_item["story_id"] == "STORY-532"

    @pytest.mark.asyncio
    async def test_resume_raises_on_non_needs_info_story(self, dispatch_db_service):
        """A-08: resume_from_needs_info() raises InvalidTransitionError on pending story.

        RED: method does not exist yet.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        # Story is still pending — not needs_info

        with pytest.raises((InvalidTransitionError, NotFoundError)):
            await svc.resume_from_needs_info("STORY-532")


# ---------------------------------------------------------------------------
# Group B — Unique index blocks duplicate enqueue
# ---------------------------------------------------------------------------

class TestNeedsInfoUniqueIndex:
    """B-01: uq_story_active_idx includes needs_info — prevents duplicate enqueue."""

    @pytest.mark.asyncio
    async def test_enqueue_raises_when_story_is_needs_info(self, dispatch_db_service):
        """B-01: enqueue() raises DuplicateDispatchError when same story is needs_info.

        RED: migration 007 hasn't added 'needs_info' to the unique index yet.
        """
        svc = dispatch_db_service
        await svc.enqueue(**_enqueue_payload())
        await svc.claim("STORY-532", agent_name="devon")
        await svc.needs_info("STORY-532", "features/story-532/QUESTION.md")

        with pytest.raises(DuplicateDispatchError, match="STORY-532|already|duplicate"):
            await svc.enqueue(**_enqueue_payload())


# ---------------------------------------------------------------------------
# Group C — Routes
# ---------------------------------------------------------------------------

class TestNeedsInfoRoutes:
    """C-01 through C-08: POST /api/dispatch/needs-info/{id} and /resume/{id}."""

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_returns_200_on_claimed_story(
        self, client, app, dispatch_db_service
    ):
        """C-01: POST /api/dispatch/needs-info/{id} returns 200 on a claimed story.

        RED: endpoint does not exist yet (will return 404 or 405).
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        await client.post("/api/dispatch/claim/STORY-532", json={"agent": "devon"})

        resp = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={
                "agent": "devon",
                "question_file_path": "features/story-532/QUESTION.md",
                "phase": 1,
            },
        )
        assert resp.status_code == 200, (
            f"POST /api/dispatch/needs-info/STORY-532 returned {resp.status_code} "
            f"(expected 200).\nResponse: {resp.text}\n"
            "Add POST /api/dispatch/needs-info/{story_id} to routes/dispatch.py (STORY-532)"
        )

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_response_schema(
        self, client, app, dispatch_db_service
    ):
        """C-02: /needs-info response must include status='needs_info' and needs_info_path.

        RED: endpoint does not exist yet.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        await client.post("/api/dispatch/claim/STORY-532", json={"agent": "devon"})

        resp = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={
                "agent": "devon",
                "question_file_path": "features/story-532/QUESTION.md",
                "phase": 1,
            },
        )
        assert resp.status_code == 200, (
            f"Endpoint not implemented: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        assert body.get("status") == "needs_info", (
            f"Expected status='needs_info', got {body.get('status')!r}: {body}"
        )
        assert body.get("needs_info_path") == "features/story-532/QUESTION.md", (
            f"needs_info_path not in response or wrong value: {body}"
        )
        assert body.get("story_id") == "STORY-532", (
            f"story_id missing from response: {body}"
        )

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_404_on_missing_story(
        self, client, app, dispatch_db_service
    ):
        """C-03: /needs-info returns 404 when story not found.

        RED: endpoint does not exist yet (any 404/405 would be misleading).
        """
        _inject(app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/needs-info/STORY-NONEXISTENT",
            json={"agent": "devon", "question_file_path": "features/x/QUESTION.md"},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown story, got {resp.status_code}.\n"
            f"Response: {resp.text}\n"
            "If 404 is because the endpoint route is missing (not story not found), "
            "implement the endpoint first."
        )

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_200_on_pending_story(
        self, client, app, dispatch_db_service
    ):
        """C-04 (revised): /needs-info returns 200 on a pending story (idempotent).

        A pending story with a QUESTION.md signal should also enter needs_info.
        The signal semantics are "agent blocked, needs human input" — the
        current queue state (pending vs claimed) doesn't change that.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        # Do NOT claim — story remains pending

        resp = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={"agent": "devon", "question_file_path": "features/story-532/QUESTION.md"},
        )
        assert resp.status_code == 200, (
            f"Expected 200 for pending story + needs_info signal (idempotent), "
            f"got {resp.status_code}.\nResponse: {resp.text}"
        )
        body = resp.json()
        assert body["status"] == "needs_info"

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_idempotent_on_already_needs_info(
        self, client, app, dispatch_db_service
    ):
        """C-04b (NEW): POST /needs-info twice on same story returns 200 both times.

        Reproduces the STORY-528 incident (2026-04-24 00:30:32Z): the runner
        retried /needs-info on an already-needs_info story and got 409, which
        cascaded into auto-retry + ghost-claim. With this fix, both calls are
        200 and the second call updates needs_info_path if it changed.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        await client.post("/api/dispatch/claim/STORY-532", json={"agent": "devon"})

        # First call — claimed → needs_info
        resp1 = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={
                "agent": "devon",
                "question_file_path": "features/story-532/QUESTION.md",
                "phase": 2,
            },
        )
        assert resp1.status_code == 200, f"First call failed: {resp1.status_code} {resp1.text}"

        # Second call — already needs_info, DIFFERENT path — must still return 200
        resp2 = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={
                "agent": "devon",
                "question_file_path": "features/story-532/QUESTION-v2.md",
                "phase": 4,
            },
        )
        assert resp2.status_code == 200, (
            f"Second /needs-info call returned {resp2.status_code} (expected 200 — "
            f"idempotent). This 409-from-reissue is the STORY-528 trigger.\n"
            f"Response: {resp2.text}"
        )
        body = resp2.json()
        assert body["status"] == "needs_info"
        assert body["needs_info_path"] == "features/story-532/QUESTION-v2.md", (
            f"Second call must update needs_info_path to the new value, "
            f"got {body['needs_info_path']!r}"
        )

    @pytest.mark.asyncio
    async def test_resume_endpoint_returns_200_and_transitions_to_pending(
        self, client, app, dispatch_db_service
    ):
        """C-05: POST /api/dispatch/resume/{id} returns 200 and moves story to pending.

        RED: endpoint does not exist yet.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        await client.post("/api/dispatch/claim/STORY-532", json={"agent": "devon"})
        await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={"agent": "devon", "question_file_path": "features/story-532/QUESTION.md"},
        )

        resp = await client.post("/api/dispatch/resume/STORY-532")
        assert resp.status_code == 200, (
            f"POST /api/dispatch/resume/STORY-532 returned {resp.status_code} "
            f"(expected 200).\nResponse: {resp.text}\n"
            "Add POST /api/dispatch/resume/{story_id} to routes/dispatch.py (STORY-532)"
        )
        body = resp.json()
        assert body.get("status") == "pending", (
            f"Expected status='pending' after resume, got {body.get('status')!r}: {body}"
        )

    @pytest.mark.asyncio
    async def test_resume_endpoint_404_on_non_needs_info_story(
        self, client, app, dispatch_db_service
    ):
        """C-06: POST /api/dispatch/resume/{id} returns 404 when story is not needs_info.

        RED: endpoint does not exist yet.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        # Story is pending, not needs_info

        resp = await client.post("/api/dispatch/resume/STORY-532")
        assert resp.status_code in (404, 409), (
            f"Expected 404 or 409 for non-needs_info story, got {resp.status_code}.\n"
            f"Response: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_queue_endpoint_includes_needs_info_bucket(
        self, client, app, dispatch_db_service
    ):
        """C-07: GET /api/dispatch/queue response includes needs_info list.

        RED: DispatchQueueResponse does not have needs_info field yet.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        await client.post("/api/dispatch/claim/STORY-532", json={"agent": "devon"})
        ni = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={"agent": "devon", "question_file_path": "features/story-532/QUESTION.md"},
        )
        if ni.status_code != 200:
            pytest.fail(
                f"/needs-info endpoint not implemented (needed for C-07): "
                f"{ni.status_code} {ni.text}"
            )

        queue = await client.get("/api/dispatch/queue")
        assert queue.status_code == 200, f"Queue returned {queue.status_code}: {queue.text}"

        body = queue.json()
        assert "needs_info" in body, (
            f"'needs_info' key missing from /api/dispatch/queue response.\n"
            f"Keys in response: {list(body.keys())}\n"
            "Add needs_info: list[DispatchItem] = [] to DispatchQueueResponse (STORY-532)"
        )
        needs_info_items = body["needs_info"]
        assert len(needs_info_items) == 1, (
            f"Expected 1 item in needs_info bucket, got {len(needs_info_items)}"
        )
        assert needs_info_items[0]["story_id"] == "STORY-532"
        assert needs_info_items[0]["status"] == "needs_info"

    @pytest.mark.asyncio
    async def test_next_endpoint_skips_needs_info_story(
        self, client, app, dispatch_db_service
    ):
        """C-08: GET /api/dispatch/next returns 204 when only story is needs_info.

        Contrast with paused (STORY-507): paused stories ARE returned by /next.
        needs_info stories are NOT — human gate required.

        RED: exclusion not implemented yet.
        """
        _inject(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        await client.post("/api/dispatch/claim/STORY-532", json={"agent": "devon"})
        ni = await client.post(
            "/api/dispatch/needs-info/STORY-532",
            json={"agent": "devon", "question_file_path": "features/story-532/QUESTION.md"},
        )
        if ni.status_code != 200:
            pytest.fail(
                f"/needs-info endpoint not implemented (needed for C-08): "
                f"{ni.status_code} {ni.text}"
            )

        # The queue now has only one story — in needs_info state.
        # /next should return 204 (nothing available for agents).
        next_resp = await client.get(
            "/api/dispatch/next",
            headers={"X-Agent-Name": "derrick"},
        )
        assert next_resp.status_code == 204, (
            f"GET /api/dispatch/next returned {next_resp.status_code} "
            f"(expected 204 — queue appears empty to agents when only needs_info exists).\n"
            f"Body: {next_resp.text}\n"
            "needs_info stories must be invisible to agents. "
            "Ensure next_pending() WHERE clause does NOT include 'needs_info'."
        )
