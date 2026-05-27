"""Tests for STORY-494: Dispatch Queue Claim/Status Sync.

AC1: Queue status matches agent reality (claimed during retry)
AC2: Completion guard retries SHA verification
AC3: Re-enqueue blocked when story being worked on
AC4: Fleet-vigilance can detect/fix queue mismatches

All route tests use mocked dispatch_db_service — no PostgreSQL required.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock

from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app
from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STORY_ID = "STORY-494"


def _make_settings(*, claim_sync_enabled: bool, tmp_path) -> Settings:
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=str(tmp_path / "agents.json"),
        azure_subscription_id=None,
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dispatch_claim_sync_enabled=claim_sync_enabled,
    )


def _make_claimed_item(story_id: str = STORY_ID, agent: str = "dan") -> dict:
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "small",
        "prompt": "Implement STORY-494",
        "enqueued_at": "2026-04-21T00:00:00+00:00",
        "enqueued_by": "mark",
        "status": "claimed",
        "claimed_by": agent,
        "claimed_at": "2026-04-21T00:01:00+00:00",
        "title": "Dispatch Claim Sync",
    }


@pytest.fixture
def tmp_path_local(tmp_path):
    return tmp_path


@pytest.fixture
def app_flag_off(tmp_path):
    settings = _make_settings(claim_sync_enabled=False, tmp_path=tmp_path)
    return create_app(settings=settings)


@pytest.fixture
def app_flag_on(tmp_path):
    settings = _make_settings(claim_sync_enabled=True, tmp_path=tmp_path)
    return create_app(settings=settings)


@pytest_asyncio.fixture
async def client_flag_off(app_flag_off):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_flag_off), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


@pytest_asyncio.fixture
async def client_flag_on(app_flag_on):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_flag_on), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


# ---------------------------------------------------------------------------
# Feature Flag Tests (AC1, AC3, AC4)
# ---------------------------------------------------------------------------


class TestReclaimFeatureFlag:
    """T494-FF-01: Endpoint returns 404 when DISPATCH_CLAIM_SYNC_ENABLED=false."""

    @pytest.mark.asyncio
    async def test_reclaim_returns_404_when_flag_off(self, client_flag_off):
        """T494-FF-01: POST /api/dispatch/reclaim/* returns 404 when flag is off."""
        resp = await client_flag_off.post(
            f"/api/dispatch/reclaim/{STORY_ID}",
            json={"agent_name": "dan"},
        )
        assert resp.status_code == 404, (
            f"Expected 404 when DISPATCH_CLAIM_SYNC_ENABLED=False, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Reclaim Endpoint Tests (flag on)
# ---------------------------------------------------------------------------


class TestReclaimEndpoint:
    """T494-02 through T494-06: POST /api/dispatch/reclaim/{story_id}"""

    @pytest.mark.asyncio
    async def test_reclaim_pending_story(self, app_flag_on, client_flag_on):
        """T494-02: Reclaim a pending story → 200, story transitions to claimed."""
        mock_svc = AsyncMock()
        mock_svc.force_claim.return_value = _make_claimed_item()
        inject_mock_services(app_flag_on, dispatch_db_service=mock_svc)

        resp = await client_flag_on.post(
            f"/api/dispatch/reclaim/{STORY_ID}",
            json={"agent_name": "dan"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["story_id"] == STORY_ID
        assert data["reclaimed"] is True
        assert data["reclaimed_by"] == "dan"
        # STORY-531: route now threads repo= kwarg; no ?repo= in request → repo=None
        mock_svc.force_claim.assert_awaited_once_with(STORY_ID, "dan", repo=None)

    @pytest.mark.asyncio
    async def test_reclaim_failed_story(self, app_flag_on, client_flag_on):
        """T494-03: Reclaim a failed story (after auto-retry re-enqueue) → 200 claimed."""
        mock_svc = AsyncMock()
        mock_svc.force_claim.return_value = _make_claimed_item(agent="hermes")
        inject_mock_services(app_flag_on, dispatch_db_service=mock_svc)

        resp = await client_flag_on.post(
            f"/api/dispatch/reclaim/{STORY_ID}",
            json={"agent_name": "hermes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reclaimed_by"] == "hermes"

    @pytest.mark.asyncio
    async def test_reclaim_story_claimed_by_other_agent(self, app_flag_on, client_flag_on):
        """T494-04: Force-reclaim story that is claimed by different agent (Morris mismatch fix)."""
        mock_svc = AsyncMock()
        mock_svc.force_claim.return_value = _make_claimed_item(agent="morris")
        inject_mock_services(app_flag_on, dispatch_db_service=mock_svc)

        resp = await client_flag_on.post(
            f"/api/dispatch/reclaim/{STORY_ID}",
            json={"agent_name": "morris"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reclaimed_by"] == "morris"

    @pytest.mark.asyncio
    async def test_reclaim_nonexistent_story_returns_404(self, app_flag_on, client_flag_on):
        """T494-05: Reclaim a non-existent story → 404."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import NotFoundError
        mock_svc = AsyncMock()
        mock_svc.force_claim.side_effect = NotFoundError("STORY-999 not found")
        inject_mock_services(app_flag_on, dispatch_db_service=mock_svc)

        resp = await client_flag_on.post(
            "/api/dispatch/reclaim/STORY-999",
            json={"agent_name": "dan"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reclaim_completed_story_returns_409(self, app_flag_on, client_flag_on):
        """T494-06: Reclaim a completed story → 409 (terminal, cannot re-open)."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import InvalidTransitionError
        mock_svc = AsyncMock()
        mock_svc.force_claim.side_effect = InvalidTransitionError(
            "STORY-494 is in terminal state 'completed' — cannot reclaim"
        )
        inject_mock_services(app_flag_on, dispatch_db_service=mock_svc)

        resp = await client_flag_on.post(
            f"/api/dispatch/reclaim/{STORY_ID}",
            json={"agent_name": "dan"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_reclaim_requires_auth(self, app_flag_on):
        """Reclaim endpoint requires API key."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_flag_on), base_url="http://test"
        ) as c:
            resp = await c.post(
                f"/api/dispatch/reclaim/{STORY_ID}",
                json={"agent_name": "dan"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SHA Retry Tests (AC2)
# ---------------------------------------------------------------------------


class TestGithubCommitRetry:
    """T494-SHA-01 through T494-SHA-03: SHA verification retries on transient failures."""

    @pytest.mark.asyncio
    async def test_sha_retry_succeeds_on_second_attempt(self):
        """T494-SHA-01: SHA verifies on 2nd attempt (GitHub propagation delay)."""
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        mock_http = AsyncMock()
        resp_404 = MagicMock(status_code=404)
        resp_200 = MagicMock(status_code=200)
        mock_http.get = AsyncMock(side_effect=[resp_404, resp_200])

        result = await _github_commit_exists(
            mock_http, "test-token", "test-repo", "abc1234567",
            max_retries=3, retry_delay_seconds=0.001,
        )

        assert result is True
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_sha_retry_exhausted_returns_false(self):
        """T494-SHA-02: All retries exhausted → returns False."""
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        mock_http = AsyncMock()
        resp_503 = MagicMock(status_code=503)
        mock_http.get = AsyncMock(return_value=resp_503)

        result = await _github_commit_exists(
            mock_http, "test-token", "test-repo", "abc1234567",
            max_retries=3, retry_delay_seconds=0.001,
        )

        assert result is False
        assert mock_http.get.call_count == 3

    @pytest.mark.asyncio
    async def test_sha_succeeds_immediately(self):
        """T494-SHA-03: SHA verifies on first attempt → single call."""
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        mock_http = AsyncMock()
        resp_200 = MagicMock(status_code=200)
        mock_http.get = AsyncMock(return_value=resp_200)

        result = await _github_commit_exists(
            mock_http, "test-token", "test-repo", "abc1234567",
            max_retries=3, retry_delay_seconds=0.001,
        )

        assert result is True
        assert mock_http.get.call_count == 1
