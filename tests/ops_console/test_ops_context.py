"""Tests for ContextService and context route."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.ops_console.context_service import ContextService
from tech_dev_agents.ops_console.models.responses import StoryInfo
from tech_dev_agents.ops_console.teams_client import (
    TeamsClient,
    TeamsClientError,
    TeamsMessage,
)
from tests.ops_console.conftest import inject_mock_services


# --- Fixtures ---


@pytest.fixture
def mock_teams_client():
    """Mock TeamsClient with sensible defaults."""
    client = AsyncMock(spec=TeamsClient)
    client.get_agent_email.return_value = "dan@gorillacommerce.ai"
    client.read_messages.return_value = [
        TeamsMessage(
            sender="Dan",
            timestamp="2026-04-01T09:55:00Z",
            content="Blocked: waiting on API keys",
            chat_id="chat-123",
        ),
    ]
    return client


@pytest.fixture
def mock_monday_svc():
    """Mock MondayService for context tests."""
    service = AsyncMock()
    service.get_current_story.return_value = StoryInfo(
        item_id=12345,
        name="STORY-016: Teams Integration",
        phase="Phase 8: Implementation",
        status="In Progress",
        group="In Progress",
    )
    return service


@pytest.fixture
def mock_loki():
    """Mock LokiClient."""
    return AsyncMock()


@pytest.fixture
def context_service(mock_teams_client, mock_monday_svc, mock_loki):
    """ContextService with all dependencies mocked."""
    return ContextService(
        teams_client=mock_teams_client,
        monday_service=mock_monday_svc,
        loki_client=mock_loki,
    )


# --- ContextService Unit Tests ---


class TestTeamsChatLink:
    def test_returns_deep_link(self, context_service):
        link = context_service.teams_chat_link("dan")
        assert link == "https://teams.microsoft.com/l/chat/0/0?users=dan@gorillacommerce.ai"

    def test_returns_none_for_no_email(self, context_service, mock_teams_client):
        mock_teams_client.get_agent_email.return_value = None
        assert context_service.teams_chat_link("unknown") is None


class TestCurrentStory:
    @pytest.mark.asyncio
    async def test_returns_story_dict(self, context_service):
        story = await context_service.current_story("dan")
        assert story is not None
        assert story["name"] == "STORY-016: Teams Integration"
        assert story["phase"] == "Phase 8: Implementation"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_story(self, context_service, mock_monday_svc):
        mock_monday_svc.get_current_story.return_value = None
        story = await context_service.current_story("dan")
        assert story is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, context_service, mock_monday_svc):
        mock_monday_svc.get_current_story.side_effect = Exception("Monday down")
        story = await context_service.current_story("dan")
        assert story is None


class TestLastCommits:
    @pytest.mark.asyncio
    async def test_returns_empty_stub(self, context_service):
        commits = await context_service.last_commits("dan", n=5)
        assert commits == []


class TestLastMessage:
    @pytest.mark.asyncio
    async def test_returns_last_message(self, context_service):
        msg = await context_service.last_message("dan")
        assert msg is not None
        assert msg["sender"] == "Dan"
        assert "Blocked" in msg["content"]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_messages(self, context_service, mock_teams_client):
        mock_teams_client.read_messages.return_value = []
        msg = await context_service.last_message("dan")
        assert msg is None

    @pytest.mark.asyncio
    async def test_returns_none_on_teams_error(self, context_service, mock_teams_client):
        mock_teams_client.read_messages.side_effect = TeamsClientError("fail")
        msg = await context_service.last_message("dan")
        assert msg is None


class TestBlockerStatus:
    @pytest.mark.asyncio
    async def test_detects_blocker(self, context_service):
        status = await context_service.blocker_status("dan")
        assert status == "blocker"

    @pytest.mark.asyncio
    async def test_detects_decision(self, context_service, mock_teams_client):
        mock_teams_client.read_messages.return_value = [
            TeamsMessage(
                sender="Dan", timestamp="2026-04-01T09:55:00Z",
                content="Decision needed: architecture choice",
                chat_id="chat-123",
            ),
        ]
        status = await context_service.blocker_status("dan")
        assert status == "decision"

    @pytest.mark.asyncio
    async def test_returns_none_for_normal_text(self, context_service, mock_teams_client):
        mock_teams_client.read_messages.return_value = [
            TeamsMessage(
                sender="Dan", timestamp="2026-04-01T09:55:00Z",
                content="Everything going well",
                chat_id="chat-123",
            ),
        ]
        status = await context_service.blocker_status("dan")
        assert status is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, context_service, mock_teams_client):
        mock_teams_client.read_messages.side_effect = TeamsClientError("fail")
        status = await context_service.blocker_status("dan")
        assert status is None


class TestGetFullContext:
    @pytest.mark.asyncio
    async def test_aggregates_all_sources(self, context_service):
        ctx = await context_service.get_full_context("dan")
        assert ctx["agent_name"] == "dan"
        assert ctx["teams_chat_link"] is not None
        assert "gorillacommerce.ai" in ctx["teams_chat_link"]
        assert ctx["current_story"] is not None
        assert ctx["current_story"]["name"] == "STORY-016: Teams Integration"
        assert ctx["last_commits"] == []
        assert ctx["last_message"] is not None
        assert ctx["blocker_status"] == "blocker"

    @pytest.mark.asyncio
    async def test_handles_partial_failures(self, context_service, mock_monday_svc):
        mock_monday_svc.get_current_story.side_effect = Exception("Monday down")
        ctx = await context_service.get_full_context("dan")
        assert ctx["current_story"] is None
        assert ctx["last_message"] is not None
        assert ctx["blocker_status"] == "blocker"


# --- Context Route Tests ---


class TestContextRoute:
    @pytest.mark.asyncio
    async def test_get_context(self, client, app, mock_agent_service):
        mock_ctx = AsyncMock()
        mock_ctx.get_full_context.return_value = {
            "agent_name": "dan",
            "teams_chat_link": "https://teams.microsoft.com/l/chat/0/0?users=dan@test.com",
            "current_story": {"name": "STORY-016", "phase": "Phase 8"},
            "last_commits": [],
            "last_message": {"sender": "Dan", "content": "Working"},
            "blocker_status": None,
        }
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            context_service=mock_ctx,
        )
        resp = await client.get("/api/agents/dan/context")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "dan"
        assert data["teams_chat_link"] is not None
        assert data["blocker_status"] is None

    @pytest.mark.asyncio
    async def test_context_unknown_agent(self, client, app, mock_agent_service):
        from tech_dev_agents.agent_dashboard import AgentNotFoundError
        mock_agent_service.get_agent.side_effect = AgentNotFoundError("not found")
        mock_ctx = AsyncMock()
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            context_service=mock_ctx,
        )
        resp = await client.get("/api/agents/unknown/context")
        assert resp.status_code == 404
