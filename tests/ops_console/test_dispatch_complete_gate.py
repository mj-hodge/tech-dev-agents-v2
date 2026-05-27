"""Tests for commit-gated dispatch completion.

STORY-253: Commit-Gated Dispatch Completion
Phase 7: RED state — tests written before implementation.

The /dispatch/complete/{story_id} endpoint now requires a JSON body with
a valid commit_sha (40-char lowercase hex), validated against the GitHub API.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"

VALID_SHA = "a" * 40  # 40-char lowercase hex
SHORT_SHA = "abcdef1"  # 7-char short SHA
UPPER_SHA = "A" * 40  # uppercase
BAD_HEX_SHA = "g" * 40  # non-hex chars
GITHUB_ORG = "hpi-gorillacommerce"


def _pg_is_reachable() -> bool:
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
        # Ensure commit_sha column exists (migration 003)
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'dispatch_items' AND column_name = 'commit_sha'
                ) THEN
                    ALTER TABLE dispatch_items ADD COLUMN commit_sha VARCHAR(40);
                END IF;
            END
            $$;
        """)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def dispatch_db_service(db_pool) -> DispatchDBService:
    return DispatchDBService(db_pool)


def _inject_dispatch(app, dispatch_db_service, github_token="ghp_test_token_123"):
    """Inject dispatch DB service and mock settings with github_token."""
    inject_mock_services(app, dispatch_db_service=dispatch_db_service)
    # Ensure app.state has settings with github_token
    if not hasattr(app.state, "settings"):
        mock_settings = MagicMock()
        mock_settings.github_token = github_token
        app.state.settings = mock_settings
    else:
        app.state.settings.github_token = github_token


def _enqueue_payload(
    story_id: str = "STORY-253",
    repo: str = "tech-dev-agents",
    scope: str = "small",
    prompt: str = "Start Phase 7 for STORY-253",
) -> dict:
    return {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "enqueued_by": "mark",
    }


async def _setup_claimed_story(client, app, dispatch_db_service, story_id="STORY-253"):
    """Helper: enqueue + claim a story so it's ready for completion."""
    _inject_dispatch(app, dispatch_db_service)
    await client.post("/api/dispatch", json=_enqueue_payload(story_id=story_id))
    await client.post(
        f"/api/dispatch/claim/{story_id}",
        json={"agent_name": "dan"},
    )


class TestCompleteRequiresCommitSha:
    """T01-T06: POST /dispatch/complete/{story_id} requires valid commit_sha."""

    @pytest.mark.asyncio
    async def test_complete_with_valid_sha_returns_200(self, client, app, dispatch_db_service):
        """T01: Complete with valid 40-char hex commit_sha and mocked GitHub 200 returns 200."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.return_value = True
            resp = await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] is True
        assert data["item"]["commit_sha"] == VALID_SHA

    @pytest.mark.asyncio
    async def test_complete_without_body_returns_422(self, client, app, dispatch_db_service):
        """T02: Complete without JSON body returns 422."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        resp = await client.post("/api/dispatch/complete/STORY-253")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_with_empty_sha_returns_422(self, client, app, dispatch_db_service):
        """T03: Complete with empty commit_sha returns 422."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/complete/STORY-253",
            json={"commit_sha": ""},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_with_short_sha_returns_422(self, client, app, dispatch_db_service):
        """T04: Complete with 7-char short SHA returns 422."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/complete/STORY-253",
            json={"commit_sha": SHORT_SHA},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_with_uppercase_sha_returns_422(self, client, app, dispatch_db_service):
        """T05: Complete with uppercase hex SHA returns 422."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/complete/STORY-253",
            json={"commit_sha": UPPER_SHA},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_complete_with_nonhex_sha_returns_422(self, client, app, dispatch_db_service):
        """T06: Complete with non-hex characters returns 422."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        resp = await client.post(
            "/api/dispatch/complete/STORY-253",
            json={"commit_sha": BAD_HEX_SHA},
        )

        assert resp.status_code == 422


class TestGitHubValidation:
    """T07-T09, T15: GitHub API validation (fail-closed)."""

    @pytest.mark.asyncio
    async def test_github_404_returns_422(self, client, app, dispatch_db_service):
        """T07: GitHub returns 404 (commit not in repo) -> 422."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.side_effect = ValueError("commit not found in repo")
            resp = await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 422
        assert "commit" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_github_network_error_returns_502(self, client, app, dispatch_db_service):
        """T08: GitHub network error -> 502 (fail-closed)."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.side_effect = ConnectionError("unable to verify commit")
            resp = await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_github_5xx_returns_502(self, client, app, dispatch_db_service):
        """T09: GitHub 500 -> 502 (fail-closed)."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.side_effect = RuntimeError("GitHub API returned 500")
            resp = await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_missing_github_token_returns_502(self, client, app, dispatch_db_service):
        """T15: No github_token configured -> 502 (fail-closed, not silent pass)."""
        await _setup_claimed_story(client, app, dispatch_db_service)
        # Override settings to have empty github_token
        app.state.settings.github_token = ""

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.side_effect = RuntimeError("GitHub token not configured")
            resp = await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 502


class TestCommitShaStorage:
    """T10, T11, T12: commit_sha persisted and returned."""

    @pytest.mark.asyncio
    async def test_commit_sha_stored_in_db(self, client, app, dispatch_db_service, db_pool):
        """T10: commit_sha is stored in dispatch_items table."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.return_value = True
            await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT commit_sha FROM dispatch_items WHERE story_id = 'STORY-253'"
            )
        assert row is not None
        assert row["commit_sha"] == VALID_SHA

    @pytest.mark.asyncio
    async def test_complete_response_includes_commit_sha(self, client, app, dispatch_db_service):
        """T11: CompleteResponse includes commit_sha field."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.return_value = True
            resp = await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["commit_sha"] == VALID_SHA

    @pytest.mark.asyncio
    async def test_history_includes_commit_sha(self, client, app, dispatch_db_service):
        """T12: DispatchItem in history response includes commit_sha."""
        await _setup_claimed_story(client, app, dispatch_db_service)

        with patch("tech_dev_agents.ops_console.routes.dispatch._verify_commit_on_github") as mock_verify:
            mock_verify.return_value = True
            await client.post(
                "/api/dispatch/complete/STORY-253",
                json={"commit_sha": VALID_SHA},
            )

        resp = await client.get("/api/dispatch/history?status=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["commit_sha"] == VALID_SHA


class TestRegressionExistingEndpoints:
    """T13-T14: Existing endpoints unaffected."""

    @pytest.mark.asyncio
    async def test_fail_endpoint_still_works(self, client, app, dispatch_db_service):
        """T13: POST /dispatch/fail still works without commit_sha."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload("STORY-999"))
        await client.post(
            "/api/dispatch/claim/STORY-999",
            json={"agent_name": "dan"},
        )

        resp = await client.post(
            "/api/dispatch/fail/STORY-999",
            json={"exit_code": 1},
        )

        assert resp.status_code == 200
        assert resp.json()["failed"] is True

    @pytest.mark.asyncio
    async def test_queue_endpoint_still_works(self, client, app, dispatch_db_service):
        """T14: GET /dispatch/queue still works normally."""
        _inject_dispatch(app, dispatch_db_service)

        await client.post("/api/dispatch", json=_enqueue_payload())

        resp = await client.get("/api/dispatch/queue")

        assert resp.status_code == 200
        assert resp.json()["total_pending"] == 1
