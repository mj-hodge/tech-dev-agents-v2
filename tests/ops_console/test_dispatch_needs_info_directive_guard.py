"""STORY-803 AC-2 Bug 1.2: Server-side 60-second directive-bypass guard.

When an agent has a *_DIRECTIVE.md / OVERRIDE.md / DIRECTIVE.md in its story
folder AND calls /api/dispatch/needs-info within 60 seconds of claim, the
endpoint must return 409 (directive_bypass_attempted) instead of transitioning
the story to needs_info.

This is the server-side safety net for the preamble fix in AC-1. The preamble
tells the agent to do real work; the server enforces it at the API layer.

Tests:
  T-2b (RED): directive_present=True, claimed 5s ago → 409
  T-2c (GREEN): directive_present=False, claimed 5s ago → 200 (no guard fires)
  T-2d (GREEN): directive_present=True, claimed 90s ago → 200 (threshold passed)

T-2b is RED because the guard does not yet exist in needs_info_story().
T-2c and T-2d are regression tests (GREEN before and after Phase 8).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STORY_ID = "STORY-TEST-803"
REPO = "tech-dev-agents"
QUESTION_PATH = f"features/story-test-803/QUESTION.md"


def _needs_info_settings(tmp_path) -> Settings:
    import json as _json
    registry = tmp_path / "agent-registry.json"
    registry.write_text(_json.dumps([
        {"name": "devon", "host": "10.0.1.12", "port": 8080, "role": "developer", "enabled": True},
    ]))
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=str(registry),
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        OPS_DISPATCH_NEEDS_INFO_ENABLED=True,
    )


def _mock_db_svc(
    *,
    claimed_at_offset_s: float = -5.0,
    needs_info_return: dict | None = None,
) -> MagicMock:
    """Build a mock DispatchDBService for needs-info route tests.

    claimed_at_offset_s: seconds relative to now (negative = in the past).
                         -5.0 means claimed 5 seconds ago.
    """
    now = datetime.now(tz=timezone.utc)
    claimed_at = now + timedelta(seconds=claimed_at_offset_s)
    story_row = {
        "id": "test-uuid-803",
        "story_id": STORY_ID,
        "repo": REPO,
        "scope": "small",
        "status": "claimed",
        "claimed_by": "devon",
        "claimed_at": claimed_at,
        "needs_info_path": None,
        "current_phase": 7,
    }
    needs_info_row = needs_info_return or {
        "story_id": STORY_ID,
        "status": "needs_info",
        "needs_info_path": QUESTION_PATH,
    }

    svc = MagicMock()
    svc.get_active_by_story_id = AsyncMock(return_value=story_row)
    svc.needs_info = AsyncMock(return_value=needs_info_row)
    return svc


@pytest_asyncio.fixture
async def needs_info_client(tmp_path):
    """Authenticated httpx client for the needs_info endpoint tests."""
    from tech_dev_agents.ops_console.main import create_app
    settings = _needs_info_settings(tmp_path)
    app = create_app(settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c, app


# ---------------------------------------------------------------------------
# T-2b (RED): directive_present=True, claimed 5s ago → 409
# ---------------------------------------------------------------------------


class TestDirectiveGuardUnder60s:
    """T-2b: directive_present=True + claimed_at 5s ago → 409 directive_bypass_attempted.

    RED: the guard does not exist in needs_info_story() yet. The current code
    calls db_svc.needs_info() unconditionally → 200. After Phase 8 adds the guard,
    this returns 409 and emits directive_bypass_attempted.
    """

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_rejects_under_60s_with_directive(
        self, needs_info_client
    ):
        """T-2b (RED): POST /api/dispatch/needs-info with directive_present=True
        and claimed_at 5s ago must return 409 and NOT transition the story.

        RED reason: no guard in needs_info_story() → returns 200 instead of 409.
        """
        client, app = needs_info_client
        db_svc = _mock_db_svc(claimed_at_offset_s=-5.0)
        inject_mock_services(app, dispatch_db_service=db_svc)

        import time
        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                f"/api/dispatch/needs-info/{STORY_ID}",
                json={
                    "agent": "devon",
                    "question_file_path": QUESTION_PATH,
                    "phase": 7,
                    "directive_present": True,
                    "phase_started_at": time.time() - 4.0,
                },
            )

        assert resp.status_code == 409, (
            f"Expected 409 (directive_bypass_attempted) when directive_present=True "
            f"and only 5s since claim, got {resp.status_code}.\n"
            f"Response: {resp.text[:500]}\n\n"
            "Add the directive-bypass guard to needs_info_story() in dispatch.py:\n"
            "  if directive_present and seconds_since_claim < DIRECTIVE_GUARD_SECONDS:\n"
            "      raise HTTPException(409, 'directive_bypass_attempted: ...')"
        )
        assert "directive_bypass_attempted" in resp.text, (
            f"409 body must include 'directive_bypass_attempted' so the agent "
            f"can identify the reason. Got: {resp.text[:300]}"
        )
        # db_svc.needs_info must NOT have been called (state did not transition)
        db_svc.needs_info.assert_not_called()


# ---------------------------------------------------------------------------
# T-2c (GREEN): directive_present=False, claimed 5s ago → 200 (no guard fires)
# ---------------------------------------------------------------------------


class TestDirectiveGuardNotFiredWithoutDirective:
    """T-2c (GREEN): directive_present=False → guard must not fire regardless of age.

    This is a regression test — the guard is conditional on directive_present.
    Without a directive the endpoint behaves as before STORY-803.
    """

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_allows_under_60s_without_directive(
        self, needs_info_client
    ):
        """T-2c (GREEN): POST /api/dispatch/needs-info with directive_present=False
        and claimed_at 5s ago must return 200 (guard does not fire).

        GREEN before and after Phase 8 — the guard is only active when
        directive_present=True.
        """
        client, app = needs_info_client
        db_svc = _mock_db_svc(claimed_at_offset_s=-5.0)
        inject_mock_services(app, dispatch_db_service=db_svc)

        import time
        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                f"/api/dispatch/needs-info/{STORY_ID}",
                json={
                    "agent": "devon",
                    "question_file_path": QUESTION_PATH,
                    "phase": 7,
                    "directive_present": False,
                    "phase_started_at": time.time() - 4.0,
                },
            )

        assert resp.status_code == 200, (
            f"Expected 200 when directive_present=False (no guard fires), "
            f"got {resp.status_code}.\nResponse: {resp.text[:500]}"
        )
        # Transition must have happened
        db_svc.needs_info.assert_called_once()


# ---------------------------------------------------------------------------
# T-2d (GREEN): directive_present=True, claimed 90s ago → 200 (threshold passed)
# ---------------------------------------------------------------------------


class TestDirectiveGuardAfter60s:
    """T-2d (GREEN): directive_present=True but >60s since claim → guard does NOT fire.

    The guard only fires when claimed_at is recent (< DIRECTIVE_GUARD_SECONDS=60).
    After 60 seconds have elapsed, the endpoint must allow the needs_info transition.

    GREEN: current code (no guard) returns 200. After Phase 8 adds the guard,
    guard checks seconds_since_claim=90 >= 60 → does not fire → still 200.
    """

    @pytest.mark.asyncio
    async def test_needs_info_endpoint_allows_after_60s_with_directive(
        self, needs_info_client
    ):
        """T-2d (GREEN): POST /api/dispatch/needs-info with directive_present=True
        and claimed_at 90s ago must return 200 (past the 60s threshold).
        """
        client, app = needs_info_client
        db_svc = _mock_db_svc(claimed_at_offset_s=-90.0)  # claimed 90s ago
        inject_mock_services(app, dispatch_db_service=db_svc)

        import time
        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                f"/api/dispatch/needs-info/{STORY_ID}",
                json={
                    "agent": "devon",
                    "question_file_path": QUESTION_PATH,
                    "phase": 7,
                    "directive_present": True,
                    "phase_started_at": time.time() - 89.0,
                },
            )

        assert resp.status_code == 200, (
            f"Expected 200 when directive_present=True but 90s since claim "
            f"(past 60s guard threshold), got {resp.status_code}.\n"
            f"Response: {resp.text[:500]}\n\n"
            "Guard must only fire when seconds_since_claim < DIRECTIVE_GUARD_SECONDS (60)."
        )
        db_svc.needs_info.assert_called_once()
