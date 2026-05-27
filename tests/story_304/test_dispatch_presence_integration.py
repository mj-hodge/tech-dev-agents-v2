"""Tests for dispatch route presence push integration (AC-1, AC-2).

STORY-304: Event-Driven Teams Presence
Verifies that dispatch claim/complete/fail trigger push_presence calls.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# T1: claim triggers Busy push (AC-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_triggers_busy_presence_push():
    """T1: Claiming a dispatch item triggers push_presence(Busy)."""
    from tech_dev_agents.ops_console.routes.dispatch import claim_story, ClaimRequest

    mock_db_svc = AsyncMock()
    mock_db_svc.register_agent.return_value = False
    mock_db_svc.claim.return_value = {
        "story_id": "STORY-100",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "test",
        "enqueued_at": "2026-04-15T00:00:00Z",
        "enqueued_by": "mark",
        "title": "Test Story",
        "claimed_by": "dan",
        "claimed_at": "2026-04-15T00:01:00Z",
    }

    mock_request = MagicMock()
    mock_request.app.state.dispatch_db_service = mock_db_svc
    mock_request.app.state.agent_service = MagicMock()
    mock_request.app.state.http_client = AsyncMock()
    mock_request.headers = {}

    with patch(
        "tech_dev_agents.ops_console.routes.dispatch.push_presence", new_callable=AsyncMock
    ) as mock_push:
        body = ClaimRequest(agent_name="dan")
        await claim_story("STORY-100", body, mock_request)

        mock_push.assert_called_once_with(
            agent_name="dan",
            availability="Busy",
            activity="InACall",
            agent_service=mock_request.app.state.agent_service,
            http_client=mock_request.app.state.http_client,
        )


# ---------------------------------------------------------------------------
# T2: complete triggers Available push (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_triggers_available_presence_push():
    """T2: Completing a dispatch item triggers push_presence(Available)."""
    from tech_dev_agents.ops_console.routes.dispatch import complete_story
    from tech_dev_agents.ops_console.models.responses import CompleteRequest

    mock_db_svc = AsyncMock()
    mock_db_svc.get = AsyncMock(return_value={"repo": "test-repo"})
    mock_db_svc.complete.return_value = {
        "story_id": "STORY-100",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "test",
        "enqueued_at": "2026-04-15T00:00:00Z",
        "enqueued_by": "mark",
        "title": "Test Story",
        "claimed_by": "dan",
        "claimed_at": "2026-04-15T00:01:00Z",
        "completed_at": "2026-04-15T01:00:00Z",
    }

    mock_settings = MagicMock()
    mock_settings.github_token = ""

    mock_request = MagicMock()
    mock_request.app.state.dispatch_db_service = mock_db_svc
    mock_request.app.state.agent_service = MagicMock()
    mock_request.app.state.http_client = AsyncMock()
    mock_request.app.state.settings = mock_settings

    body = CompleteRequest(commit_sha="abc1234def5678")

    with patch(
        "tech_dev_agents.ops_console.routes.dispatch.push_presence", new_callable=AsyncMock
    ) as mock_push:
        await complete_story("STORY-100", body, mock_request)

        mock_push.assert_called_once_with(
            agent_name="dan",
            availability="Available",
            activity="Available",
            agent_service=mock_request.app.state.agent_service,
            http_client=mock_request.app.state.http_client,
        )


# ---------------------------------------------------------------------------
# T3: fail triggers Available push (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_triggers_available_presence_push():
    """T3: Failing a dispatch item triggers push_presence(Available)."""
    from tech_dev_agents.ops_console.routes.dispatch import fail_story

    mock_db_svc = AsyncMock()
    mock_db_svc.fail.return_value = {
        "story_id": "STORY-100",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "test",
        "enqueued_at": "2026-04-15T00:00:00Z",
        "enqueued_by": "mark",
        "title": "Test Story",
        "claimed_by": "dan",
        "claimed_at": "2026-04-15T00:01:00Z",
        "completed_at": "2026-04-15T01:00:00Z",
    }

    mock_request = MagicMock()
    mock_request.app.state.dispatch_db_service = mock_db_svc
    mock_request.app.state.agent_service = MagicMock()
    mock_request.app.state.http_client = AsyncMock()
    mock_request.json = AsyncMock(return_value={})

    with patch(
        "tech_dev_agents.ops_console.routes.dispatch.push_presence", new_callable=AsyncMock
    ) as mock_push:
        await fail_story("STORY-100", mock_request)

        mock_push.assert_called_once_with(
            agent_name="dan",
            availability="Available",
            activity="Available",
            agent_service=mock_request.app.state.agent_service,
            http_client=mock_request.app.state.http_client,
        )


# ---------------------------------------------------------------------------
# T4: cancel does NOT trigger presence push (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_does_not_trigger_presence_push():
    """T4: Cancelling a pending dispatch does NOT push presence."""
    from tech_dev_agents.ops_console.routes.dispatch import cancel_story

    mock_db_svc = AsyncMock()
    mock_db_svc.cancel.return_value = None

    mock_request = MagicMock()
    mock_request.app.state.dispatch_db_service = mock_db_svc

    with patch(
        "tech_dev_agents.ops_console.routes.dispatch.push_presence", new_callable=AsyncMock
    ) as mock_push:
        await cancel_story("STORY-100", mock_request)

        # cancel should NOT trigger any presence push
        mock_push.assert_not_called()


# ---------------------------------------------------------------------------
# T5: Presence push failure doesn't break dispatch (AC-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_presence_push_failure_does_not_break_claim():
    """T5: If push_presence fails, claim still succeeds."""
    from tech_dev_agents.ops_console.routes.dispatch import claim_story, ClaimRequest

    mock_db_svc = AsyncMock()
    mock_db_svc.register_agent.return_value = False
    mock_db_svc.claim.return_value = {
        "story_id": "STORY-100",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "test",
        "enqueued_at": "2026-04-15T00:00:00Z",
        "enqueued_by": "mark",
        "title": "Test Story",
        "claimed_by": "dan",
        "claimed_at": "2026-04-15T00:01:00Z",
    }

    mock_request = MagicMock()
    mock_request.app.state.dispatch_db_service = mock_db_svc
    mock_request.app.state.agent_service = MagicMock()
    mock_request.app.state.http_client = AsyncMock()
    mock_request.headers = {}

    with patch(
        "tech_dev_agents.ops_console.routes.dispatch.push_presence",
        new_callable=AsyncMock,
        side_effect=Exception("Network error"),
    ):
        # Should NOT raise — claim completes despite push failure
        body = ClaimRequest(agent_name="dan")
        result = await claim_story("STORY-100", body, mock_request)
        assert result.story_id == "STORY-100"
