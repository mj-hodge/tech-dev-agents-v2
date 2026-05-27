"""API integration tests for dispatch queue endpoints.

STORY-026: Central Dispatch Queue (original)
STORY-028: Updated for PostgreSQL DispatchDBService
STORY-253: Commit-gated dispatch completion
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import asyncpg
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


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
pytestmark = pytest.mark.skipif(not _PG_AVAILABLE, reason="PostgreSQL not reachable")


@pytest_asyncio.fixture
async def db_pool():
    """Connection pool to test database, truncated between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def dispatch_db_service(db_pool) -> DispatchDBService:
    return DispatchDBService(db_pool)


def _inject_dispatch(app, dispatch_db_service):
    """Inject the dispatch DB service into app.state."""
    inject_mock_services(app, dispatch_db_service=dispatch_db_service)


def _reset_dispatch_metrics() -> None:
    """Reset process-local dispatch counters to a known baseline for deterministic tests."""
    from tech_dev_agents.ops_console.routes import dispatch as dispatch_mod

    with dispatch_mod._pipeline_metrics_lock:
        dispatch_mod._pipeline_metrics.clear()
        dispatch_mod._pipeline_metrics.update(
            {
                "claim_attempt_total": 0,
                "claim_success_total": 0,
                "claim_error_total": 0,
                "complete_attempt_total": 0,
                "complete_success_total": 0,
                "complete_error_total": 0,
                "fail_attempt_total": 0,
                "fail_success_total": 0,
                "fail_error_total": 0,
            }
        )


def _enqueue_payload(
    story_id: str = "STORY-094",
    repo: str = "advertising-amazon",
    scope: str = "small",
    prompt: str = "Start Phase 7 for STORY-094",
    enqueued_by: str = "mark",
) -> dict:
    return {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "enqueued_by": enqueued_by,
    }


class TestEnqueueStory:
    """T01-T04: POST /api/dispatch"""

    @pytest.mark.asyncio
    async def test_enqueue_story_success(self, client, app, dispatch_db_service):
        """T01: Enqueue returns 201 with item and queue_depth."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.post("/api/dispatch", json=_enqueue_payload())

        assert resp.status_code == 201
        data = resp.json()
        assert data["item"]["story_id"] == "STORY-094"
        assert data["item"]["status"] == "pending"
        assert data["queue_depth"] == 1

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_returns_409(self, client, app, dispatch_db_service):
        """T02: Duplicate story_id returns 409."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())
        resp = await client.post("/api/dispatch", json=_enqueue_payload())

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_enqueue_queue_full_returns_422(self, client, app, dispatch_db_service):
        """T03: Queue full (50 items) returns 422."""
        _inject_dispatch(app, dispatch_db_service)

        # Fill the queue with 50 items
        for i in range(50):
            payload = _enqueue_payload(story_id=f"STORY-{i:03d}")
            resp = await client.post("/api/dispatch", json=payload)
            assert resp.status_code == 201, f"Failed at item {i}: {resp.text}"

        # 51st should fail
        resp = await client.post(
            "/api/dispatch", json=_enqueue_payload(story_id="STORY-999")
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_enqueue_validates_story_id_format(self, client, app, dispatch_db_service):
        """T04: Invalid story_id pattern returns 422."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch", json=_enqueue_payload(story_id="bad-id")
        )
        assert resp.status_code == 422


class TestListQueue:
    """T10-T12: GET /api/dispatch/queue"""

    @pytest.mark.asyncio
    async def test_list_queue_empty(self, client, app, dispatch_db_service):
        """T10: Empty queue returns zeros."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.get("/api/dispatch/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pending"] == 0
        assert data["total_claimed"] == 0
        assert data["pending"] == []
        assert data["claimed"] == []

    @pytest.mark.asyncio
    async def test_list_queue_with_items(self, client, app, dispatch_db_service):
        """T11: Queue with items returns correct counts."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-001"))
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-002"))

        resp = await client.get("/api/dispatch/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pending"] == 2
        assert len(data["pending"]) == 2

    @pytest.mark.asyncio
    async def test_list_queue_fifo_order(self, client, app, dispatch_db_service):
        """T12: Items appear in FIFO order (first enqueued = first in list)."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-001"))
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-002"))
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-003"))

        resp = await client.get("/api/dispatch/queue")

        data = resp.json()
        ids = [item["story_id"] for item in data["pending"]]
        assert ids == ["STORY-001", "STORY-002", "STORY-003"]


class TestNextStory:
    """T13-T14: GET /api/dispatch/next"""

    @pytest.mark.asyncio
    async def test_next_returns_oldest_pending(self, client, app, dispatch_db_service):
        """T13: Returns the first (oldest) pending item."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-001"))
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-002"))

        resp = await client.get("/api/dispatch/next")

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["story_id"] == "STORY-001"

    @pytest.mark.asyncio
    async def test_next_empty_returns_204(self, client, app, dispatch_db_service):
        """T14: Empty queue returns 204 No Content."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.get("/api/dispatch/next")

        assert resp.status_code == 204


class TestClaimStory:
    """T15-T17: POST /api/dispatch/claim/{story_id}"""

    @pytest.mark.asyncio
    async def test_claim_success(self, client, app, dispatch_db_service):
        """T15: Claiming moves story from pending to claimed."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-050"))

        resp = await client.post(
            "/api/dispatch/claim/STORY-050",
            json={"agent_name": "dan"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["claimed_by"] == "dan"
        assert data["story_id"] == "STORY-050"

        # Verify queue state
        queue_resp = await client.get("/api/dispatch/queue")
        queue_data = queue_resp.json()
        assert queue_data["total_pending"] == 0
        assert queue_data["total_claimed"] == 1

    @pytest.mark.asyncio
    async def test_claim_already_claimed_returns_409(self, client, app, dispatch_db_service):
        """T16: Double-claim returns 409 (AC-9)."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-050"))

        # First claim succeeds
        resp1 = await client.post(
            "/api/dispatch/claim/STORY-050",
            json={"agent_name": "dan"},
        )
        assert resp1.status_code == 200

        # Second claim fails with 409
        resp2 = await client.post(
            "/api/dispatch/claim/STORY-050",
            json={"agent_name": "derrick"},
        )
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_claim_not_found_returns_404(self, client, app, dispatch_db_service):
        """T17: Claiming non-existent story returns 404."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/claim/STORY-999",
            json={"agent_name": "dan"},
        )

        assert resp.status_code == 404


class TestCancelStory:
    """T18-T19, T05: DELETE /api/dispatch/queue/{story_id}"""

    @pytest.mark.asyncio
    async def test_cancel_pending_success(self, client, app, dispatch_db_service):
        """T18: Cancel a pending story removes it from active queue."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-060"))

        resp = await client.delete("/api/dispatch/queue/STORY-060")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True

        # Verify not in active queue
        queue_resp = await client.get("/api/dispatch/queue")
        assert queue_resp.json()["total_pending"] == 0

    @pytest.mark.asyncio
    async def test_cancel_claimed_returns_409(self, client, app, dispatch_db_service):
        """T19: Cannot cancel a claimed story."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-060"))
        await client.post(
            "/api/dispatch/claim/STORY-060",
            json={"agent_name": "dan"},
        )

        resp = await client.delete("/api/dispatch/queue/STORY-060")

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_cancel_not_found_returns_404(self, client, app, dispatch_db_service):
        """T05: Cancel non-existent story returns 404."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.delete("/api/dispatch/queue/STORY-999")

        assert resp.status_code == 404


class TestCompleteStory:
    """T20-T21: POST /api/dispatch/complete/{story_id} (STORY-028)
    T57-T62: commit-sha guard (STORY-253)
    """

    _VALID_COMPLETE_BODY = {"commit_sha": "abc1234def567890abc1234def567890abc12345"}

    @pytest.mark.asyncio
    async def test_complete_success(self, client, app, dispatch_db_service):
        """T20: Complete a claimed story returns 200 with CompleteResponse."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))
        await client.post(
            "/api/dispatch/claim/STORY-070",
            json={"agent_name": "dan"},
        )

        resp = await client.post(
            "/api/dispatch/complete/STORY-070", json=self._VALID_COMPLETE_BODY
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] is True
        assert data["story_id"] == "STORY-070"
        assert data["completed_at"] is not None
        assert data["item"]["status"] == "completed"
        assert data["item"]["commit_sha"] == self._VALID_COMPLETE_BODY["commit_sha"]

    @pytest.mark.asyncio
    async def test_complete_not_claimed_returns_409(self, client, app, dispatch_db_service):
        """T21: Complete a pending (not claimed) story returns 409."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))

        resp = await client.post(
            "/api/dispatch/complete/STORY-070", json=self._VALID_COMPLETE_BODY
        )

        assert resp.status_code == 409

    # --- STORY-253: commit_sha proof-of-work guard ---

    @pytest.mark.asyncio
    async def test_complete_missing_body_returns_422(self, client, app, dispatch_db_service):
        """T57 (STORY-253): POST with no JSON body rejected as 422."""
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))
        await client.post("/api/dispatch/claim/STORY-070", json={"agent_name": "dan"})

        resp = await client.post("/api/dispatch/complete/STORY-070")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_missing_commit_sha_returns_422(
        self, client, app, dispatch_db_service
    ):
        """T58 (STORY-253): body without commit_sha rejected as 422."""
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))
        await client.post("/api/dispatch/claim/STORY-070", json={"agent_name": "dan"})

        resp = await client.post("/api/dispatch/complete/STORY-070", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_malformed_commit_sha_returns_422(
        self, client, app, dispatch_db_service
    ):
        """T59 (STORY-253): non-hex / wrong-length commit_sha rejected."""
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))
        await client.post("/api/dispatch/claim/STORY-070", json={"agent_name": "dan"})

        for bad in ["", "done", "zzzzzzz", "abc", "A" * 41]:
            resp = await client.post(
                "/api/dispatch/complete/STORY-070", json={"commit_sha": bad}
            )
            assert resp.status_code == 422, f"expected 422 for {bad!r}, got {resp.status_code}"

    @pytest.mark.asyncio
    async def test_complete_valid_sha_stored_on_row(
        self, client, app, dispatch_db_service
    ):
        """T60 (STORY-253): valid sha is persisted and returned on the item."""
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))
        await client.post("/api/dispatch/claim/STORY-070", json={"agent_name": "dan"})

        sha = "deadbeef1234567890abcdef1234567890abcdef"
        resp = await client.post(
            "/api/dispatch/complete/STORY-070",
            json={"commit_sha": sha, "pr_number": 42},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["commit_sha"] == sha
        assert data["item"]["pr_number"] == 42

    @pytest.mark.asyncio
    async def test_complete_pr_number_zero_rejected(
        self, client, app, dispatch_db_service
    ):
        """T61 (STORY-253): pr_number must be >= 1."""
        _inject_dispatch(app, dispatch_db_service)
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-070"))
        await client.post("/api/dispatch/claim/STORY-070", json={"agent_name": "dan"})

        resp = await client.post(
            "/api/dispatch/complete/STORY-070",
            json={"commit_sha": "abcdef1234567", "pr_number": 0},
        )
        assert resp.status_code == 422


class TestDispatchHistory:
    """T22-T23: GET /api/dispatch/history (STORY-028)"""

    @pytest.mark.asyncio
    async def test_history_paginated(self, client, app, dispatch_db_service):
        """T22: History returns paginated completed/cancelled items."""
        _inject_dispatch(app, dispatch_db_service)

        # Create completed item
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-001"))
        await client.post("/api/dispatch/claim/STORY-001", json={"agent_name": "dan"})
        await client.post(
            "/api/dispatch/complete/STORY-001",
            json={"commit_sha": "abc1234def567890abc1234def567890abc12345"},
        )

        # Create cancelled item
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-002"))
        await client.delete("/api/dispatch/queue/STORY-002")

        resp = await client.get("/api/dispatch/history")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_history_with_status_filter(self, client, app, dispatch_db_service):
        """T23: History with status filter returns only matching."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-001"))
        await client.post("/api/dispatch/claim/STORY-001", json={"agent_name": "dan"})
        await client.post(
            "/api/dispatch/complete/STORY-001",
            json={"commit_sha": "abc1234def567890abc1234def567890abc12345"},
        )

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-002"))
        await client.delete("/api/dispatch/queue/STORY-002")

        resp = await client.get("/api/dispatch/history?status=completed")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "completed"


class TestDispatchTitle:
    """T30-T33: STORY-034 — title field in dispatch queue."""

    @pytest.mark.asyncio
    async def test_enqueue_with_explicit_title(self, client, app, dispatch_db_service):
        """T30: Enqueue with explicit title persists and returns it."""
        _inject_dispatch(app, dispatch_db_service)

        payload = _enqueue_payload()
        payload["title"] = "Keyword Bids via Decorator"

        resp = await client.post("/api/dispatch", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert data["item"]["title"] == "Keyword Bids via Decorator"

    @pytest.mark.asyncio
    async def test_title_returned_in_queue_listing(self, client, app, dispatch_db_service):
        """T31: Title appears in GET /api/dispatch/queue response."""
        _inject_dispatch(app, dispatch_db_service)

        payload = _enqueue_payload()
        payload["title"] = "Show story title"

        await client.post("/api/dispatch", json=payload)

        resp = await client.get("/api/dispatch/queue")
        data = resp.json()

        assert data["total_pending"] == 1
        assert data["pending"][0]["title"] == "Show story title"

    @pytest.mark.asyncio
    async def test_title_auto_extracted_from_prompt(self, client, app, dispatch_db_service):
        """T32: When title is null, auto-extract from prompt."""
        _inject_dispatch(app, dispatch_db_service)

        payload = _enqueue_payload(
            story_id="STORY-095",
            prompt="STORY-095: Keyword Bids via Decorator. Start Phase 7.",
        )
        # No title field — should auto-extract

        resp = await client.post("/api/dispatch", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert data["item"]["title"] == "Keyword Bids via Decorator"

    @pytest.mark.asyncio
    async def test_title_null_backward_compatible(self, client, app, dispatch_db_service):
        """T33: Omitting title does not break — treated as auto-extracted."""
        _inject_dispatch(app, dispatch_db_service)

        payload = _enqueue_payload()
        # No title key at all

        resp = await client.post("/api/dispatch", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        # title should be auto-extracted or null — not an error
        assert "title" in data["item"]

    @pytest.mark.asyncio
    async def test_title_persists_through_claim(self, client, app, dispatch_db_service):
        """T34: Title is preserved when story is claimed."""
        _inject_dispatch(app, dispatch_db_service)

        payload = _enqueue_payload(story_id="STORY-096")
        payload["title"] = "Fleet Status"

        await client.post("/api/dispatch", json=payload)

        resp = await client.post(
            "/api/dispatch/claim/STORY-096",
            json={"agent_name": "dan"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["title"] == "Fleet Status"

        # Also check queue listing
        queue_resp = await client.get("/api/dispatch/queue")
        queue_data = queue_resp.json()
        assert queue_data["total_claimed"] == 1
        assert queue_data["claimed"][0]["title"] == "Fleet Status"


class TestAgentRegistration:
    """T24: POST /api/agents/register (STORY-028)"""

    @pytest.mark.asyncio
    async def test_register_new_agent(self, client, app, dispatch_db_service):
        """T24: Register a new agent returns is_new=true."""
        _inject_dispatch(app, dispatch_db_service)

        resp = await client.post(
            "/api/agents/register",
            json={"name": "new-agent"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_new"] is True
        assert data["name"] == "new-agent"


class TestAuthRequired:
    """T06, T25: All endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_all_endpoints_require_auth(self, unauthed_client, app, dispatch_db_service):
        """T06/T25: 401 without API key on all dispatch endpoints."""
        _inject_dispatch(app, dispatch_db_service)

        endpoints = [
            ("POST", "/api/dispatch"),
            ("GET", "/api/dispatch/queue"),
            ("GET", "/api/dispatch/metrics"),
            ("GET", "/api/dispatch/next"),
            ("POST", "/api/dispatch/claim/STORY-001"),
            ("DELETE", "/api/dispatch/queue/STORY-001"),
            ("POST", "/api/dispatch/complete/STORY-001"),
            ("GET", "/api/dispatch/history"),
            ("POST", "/api/agents/register"),
        ]

        for method, path in endpoints:
            if method == "POST":
                resp = await unauthed_client.post(path, json={})
            elif method == "GET":
                resp = await unauthed_client.get(path)
            elif method == "DELETE":
                resp = await unauthed_client.delete(path)

            assert resp.status_code == 401, f"{method} {path} should require auth, got {resp.status_code}"


class TestDispatchMetrics:
    """Telemetry endpoint + counter increments for dispatch lifecycle paths."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_expected_keys(self, client, app, dispatch_db_service):
        _inject_dispatch(app, dispatch_db_service)
        _reset_dispatch_metrics()

        resp = await client.get("/api/dispatch/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "generated_at" in data
        assert "counters" in data
        assert data["counters"]["claim_attempt_total"] == 0
        assert data["counters"]["complete_attempt_total"] == 0
        assert data["counters"]["fail_attempt_total"] == 0

    @pytest.mark.asyncio
    async def test_metrics_increment_for_claim_complete_fail(self, client, app, dispatch_db_service):
        _inject_dispatch(app, dispatch_db_service)
        _reset_dispatch_metrics()

        sha = "deadbeef1234567890abcdef1234567890abcdef"

        # Successful claim + complete
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-911"))
        c1 = await client.post("/api/dispatch/claim/STORY-911", json={"agent_name": "dan"})
        assert c1.status_code == 200
        done = await client.post("/api/dispatch/complete/STORY-911", json={"commit_sha": sha})
        assert done.status_code == 200

        # Successful claim + fail
        await client.post("/api/dispatch", json=_enqueue_payload("STORY-912"))
        c2 = await client.post("/api/dispatch/claim/STORY-912", json={"agent_name": "devon"})
        assert c2.status_code == 200
        failed = await client.post("/api/dispatch/fail/STORY-912", json={"exit_code": 1})
        assert failed.status_code == 200

        # One claim error (not found)
        claim_miss = await client.post("/api/dispatch/claim/STORY-999", json={"agent_name": "dan"})
        assert claim_miss.status_code == 404

        # One complete error (not found)
        complete_miss = await client.post("/api/dispatch/complete/STORY-999", json={"commit_sha": sha})
        assert complete_miss.status_code == 404

        # One fail error (not found)
        fail_miss = await client.post("/api/dispatch/fail/STORY-999")
        assert fail_miss.status_code == 404

        m = await client.get("/api/dispatch/metrics")
        assert m.status_code == 200
        counters = m.json()["counters"]
        assert counters["claim_attempt_total"] >= 3
        assert counters["claim_success_total"] >= 2
        assert counters["claim_error_total"] >= 1
        assert counters["complete_attempt_total"] >= 2
        assert counters["complete_success_total"] >= 1
        assert counters["complete_error_total"] >= 1
        assert counters["fail_attempt_total"] >= 2
        assert counters["fail_success_total"] >= 1
        assert counters["fail_error_total"] >= 1


# ---------------------------------------------------------------------------
# STORY-253: Commit-gated dispatch completion
# Phase 7: RED state — tests written before implementation.
# ---------------------------------------------------------------------------

VALID_SHA = "a" * 40  # Valid 40-char lowercase hex SHA


def _mock_github_200():
    """Mock httpx response for a successful GitHub commit lookup."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"sha": VALID_SHA, "commit": {"message": "test"}}
    return resp


def _mock_github_404():
    """Mock httpx response for a GitHub commit not found."""
    resp = MagicMock()
    resp.status_code = 404
    resp.json.return_value = {"message": "Not Found"}
    return resp


def _mock_github_500():
    """Mock httpx response for a GitHub server error."""
    resp = MagicMock()
    resp.status_code = 500
    resp.json.return_value = {"message": "Internal Server Error"}
    return resp


async def _enqueue_claim(client, story_id: str = "STORY-253"):
    """Helper: enqueue and claim a story, returning the story_id."""
    await client.post("/api/dispatch", json=_enqueue_payload(story_id))
    await client.post(
        f"/api/dispatch/claim/{story_id}",
        json={"agent_name": "dan"},
    )
    return story_id


class TestCommitGatedComplete:
    """T30-T38: POST /api/dispatch/complete/{story_id} with commit_sha validation (STORY-253)."""

    @pytest.mark.asyncio
    async def test_complete_with_valid_sha_returns_200(self, client, app, dispatch_db_service):
        """T30: Complete with valid commit_sha + GitHub 200 returns 200 with sha in response."""
        _inject_dispatch(app, dispatch_db_service)
        story_id = await _enqueue_claim(client, "STORY-300")

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.verify_commit_sha",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = await client.post(
                f"/api/dispatch/complete/{story_id}",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] is True
        assert data["item"]["commit_sha"] == VALID_SHA

    @pytest.mark.asyncio
    async def test_complete_missing_commit_sha_returns_422(self, client, app, dispatch_db_service):
        """T31: Complete with missing commit_sha in body returns 422."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-301")

        resp = await client.post(
            "/api/dispatch/complete/STORY-301",
            json={},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_empty_body_returns_422(self, client, app, dispatch_db_service):
        """T32: Complete with no JSON body returns 422."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-302")

        resp = await client.post("/api/dispatch/complete/STORY-302")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_short_sha_returns_422(self, client, app, dispatch_db_service):
        """T33: Complete with too-short SHA returns 422."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-303")

        resp = await client.post(
            "/api/dispatch/complete/STORY-303",
            json={"commit_sha": "abc123"},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_uppercase_sha_returns_422(self, client, app, dispatch_db_service):
        """T34: Complete with uppercase hex SHA returns 422."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-304")

        resp = await client.post(
            "/api/dispatch/complete/STORY-304",
            json={"commit_sha": "A" * 40},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_github_404_returns_422(self, client, app, dispatch_db_service):
        """T35: GitHub returns 404 for SHA -> 422 'commit not found'."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-305")

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.verify_commit_sha",
            new_callable=AsyncMock,
            side_effect=ValueError("commit not found on repo"),
        ):
            resp = await client.post(
                f"/api/dispatch/complete/STORY-305",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 422
        assert "commit not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_complete_github_unreachable_returns_502(self, client, app, dispatch_db_service):
        """T36: GitHub API unreachable -> 502 'unable to verify'."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-306")

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.verify_commit_sha",
            new_callable=AsyncMock,
            side_effect=ConnectionError("unable to verify commit"),
        ):
            resp = await client.post(
                f"/api/dispatch/complete/STORY-306",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 502
        assert "unable to verify" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_complete_no_github_token_returns_502(self, client, app, dispatch_db_service):
        """T37: No github_token in settings -> 502 (fail-closed)."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-307")

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.verify_commit_sha",
            new_callable=AsyncMock,
            side_effect=ConnectionError("unable to verify commit — no GitHub token configured"),
        ):
            resp = await client.post(
                f"/api/dispatch/complete/STORY-307",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_commit_sha_appears_in_history(self, client, app, dispatch_db_service):
        """T38: commit_sha appears in history endpoint after completion."""
        _inject_dispatch(app, dispatch_db_service)
        await _enqueue_claim(client, "STORY-308")

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.verify_commit_sha",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await client.post(
                "/api/dispatch/complete/STORY-308",
                json={"commit_sha": VALID_SHA},
            )

        resp = await client.get("/api/dispatch/history?status=completed")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1
        completed_item = next(
            (i for i in data["items"] if i["story_id"] == "STORY-308"), None
        )
        assert completed_item is not None
        assert completed_item["commit_sha"] == VALID_SHA
