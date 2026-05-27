"""Tests for TeamsClient, blocker detection, and message routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tech_dev_agents.ops_console.teams_client import (
    TeamsClient,
    TeamsClientError,
    TeamsMessage,
    detect_blocker,
)
from tests.ops_console.conftest import (
    TEST_API_KEY,
    inject_mock_services,
)


# --- Constants ---

MOCK_REGISTRY = [
    {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer",
     "enabled": True, "email": "dan@gorillacommerce.ai"},
    {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer",
     "enabled": True, "email": "derrick@gorillacommerce.ai"},
    {"name": "no-email", "host": "10.0.1.12", "port": 8080, "role": "tester",
     "enabled": True},
]

MOCK_CHATS_RESPONSE = {
    "value": [
        {
            "id": "chat-id-dan-123",
            "members": [
                {"email": "dan@gorillacommerce.ai", "displayName": "Dan"},
                {"email": "ops@gorillacommerce.ai", "displayName": "Ops"},
            ],
        },
        {
            "id": "chat-id-derrick-456",
            "members": [
                {"email": "derrick@gorillacommerce.ai", "displayName": "Derrick"},
                {"email": "ops@gorillacommerce.ai", "displayName": "Ops"},
            ],
        },
    ]
}

MOCK_SEND_RESPONSE = {
    "id": "msg-001",
    "from": {"user": {"displayName": "Ops Console"}},
    "createdDateTime": "2026-04-01T10:00:00Z",
    "body": {"content": "Hello Dan"},
}

MOCK_MESSAGES_RESPONSE = {
    "value": [
        {
            "id": "msg-002",
            "from": {"user": {"displayName": "Dan"}},
            "createdDateTime": "2026-04-01T09:55:00Z",
            "body": {"content": "Blocked: waiting on API credentials"},
        },
        {
            "id": "msg-001",
            "from": {"user": {"displayName": "Ops Console"}},
            "createdDateTime": "2026-04-01T09:50:00Z",
            "body": {"content": "How is the story going?"},
        },
    ]
}


# --- Fixtures ---


@pytest.fixture
def mock_http():
    """Mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def teams_client(mock_http):
    """TeamsClient with mocked HTTP client."""
    return TeamsClient(
        graph_api_token="test-token",
        http_client=mock_http,
        agent_registry=MOCK_REGISTRY,
        base_url="https://graph.microsoft.com/v1.0",
    )


@pytest.fixture
def mock_teams_client():
    """Fully mocked TeamsClient for route tests."""
    client = AsyncMock()
    client.send_message.return_value = TeamsMessage(
        sender="Ops", timestamp="2026-04-01T10:00:00Z",
        content="Hello", chat_id="chat-123",
    )
    client.read_messages.return_value = [
        TeamsMessage(
            sender="Dan", timestamp="2026-04-01T09:00:00Z",
            content="Working on it", chat_id="chat-123",
        ),
    ]
    return client


# --- Blocker Detection Tests ---


class TestDetectBlocker:
    def test_blocked_pattern(self):
        assert detect_blocker("Blocked: waiting on credentials") == "blocker"

    def test_decision_needed_pattern(self):
        assert detect_blocker("Decision needed: choose between A and B") == "decision"

    def test_normal_text_returns_none(self):
        assert detect_blocker("Everything is going well") is None

    def test_blocked_multiline(self):
        text = "Update: made progress\nBlocked: need API keys\nWill continue tomorrow"
        assert detect_blocker(text) == "blocker"

    def test_decision_multiline(self):
        text = "Phase 6 complete\nDecision needed: architecture choice"
        assert detect_blocker(text) == "decision"

    def test_blocked_takes_priority_over_decision(self):
        text = "Blocked: something\nDecision needed: something else"
        assert detect_blocker(text) == "blocker"

    def test_partial_match_no_colon(self):
        assert detect_blocker("Blocked by something") is None

    def test_empty_string(self):
        assert detect_blocker("") is None

    def test_case_sensitive(self):
        assert detect_blocker("blocked: something") is None


# --- TeamsClient Unit Tests ---


class TestTeamsClientInit:
    def test_email_lookup(self, teams_client):
        assert teams_client.get_agent_email("dan") == "dan@gorillacommerce.ai"
        assert teams_client.get_agent_email("derrick") == "derrick@gorillacommerce.ai"

    def test_no_email_agent(self, teams_client):
        assert teams_client.get_agent_email("no-email") is None

    def test_unknown_agent(self, teams_client):
        assert teams_client.get_agent_email("unknown") is None


class TestResolveChatId:
    @pytest.mark.asyncio
    async def test_resolves_chat_id(self, teams_client, mock_http):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = MOCK_CHATS_RESPONSE
        mock_http.get.return_value = resp

        chat_id = await teams_client.resolve_chat_id("dan")
        assert chat_id == "chat-id-dan-123"

    @pytest.mark.asyncio
    async def test_caches_chat_id(self, teams_client, mock_http):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = MOCK_CHATS_RESPONSE
        mock_http.get.return_value = resp

        await teams_client.resolve_chat_id("dan")
        await teams_client.resolve_chat_id("dan")
        assert mock_http.get.call_count == 1

    @pytest.mark.asyncio
    async def test_no_email_raises(self, teams_client):
        with pytest.raises(TeamsClientError, match="No email found"):
            await teams_client.resolve_chat_id("no-email")

    @pytest.mark.asyncio
    async def test_graph_api_error(self, teams_client, mock_http):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_http.get.return_value = resp

        with pytest.raises(TeamsClientError, match="failed: 401"):
            await teams_client.resolve_chat_id("dan")

    @pytest.mark.asyncio
    async def test_no_matching_chat(self, teams_client, mock_http):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"value": []}
        mock_http.get.return_value = resp

        with pytest.raises(TeamsClientError, match="No 1:1 chat found"):
            await teams_client.resolve_chat_id("dan")

    @pytest.mark.asyncio
    async def test_http_error_raises(self, teams_client, mock_http):
        mock_http.get.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(TeamsClientError, match="request failed"):
            await teams_client.resolve_chat_id("dan")


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_success(self, teams_client, mock_http):
        chat_resp = MagicMock()
        chat_resp.status_code = 200
        chat_resp.json.return_value = MOCK_CHATS_RESPONSE

        send_resp = MagicMock()
        send_resp.status_code = 201
        send_resp.json.return_value = MOCK_SEND_RESPONSE

        mock_http.get.return_value = chat_resp
        mock_http.post.return_value = send_resp

        msg = await teams_client.send_message("dan", "Hello Dan")
        assert isinstance(msg, TeamsMessage)
        assert msg.content == "Hello Dan"
        assert msg.chat_id == "chat-id-dan-123"
        assert msg.sender == "Ops Console"

    @pytest.mark.asyncio
    async def test_send_graph_error(self, teams_client, mock_http):
        chat_resp = MagicMock()
        chat_resp.status_code = 200
        chat_resp.json.return_value = MOCK_CHATS_RESPONSE

        send_resp = MagicMock()
        send_resp.status_code = 500
        send_resp.text = "Internal Server Error"

        mock_http.get.return_value = chat_resp
        mock_http.post.return_value = send_resp

        with pytest.raises(TeamsClientError, match="send failed"):
            await teams_client.send_message("dan", "Hello")


class TestReadMessages:
    @pytest.mark.asyncio
    async def test_read_success(self, teams_client, mock_http):
        chat_resp = MagicMock()
        chat_resp.status_code = 200
        chat_resp.json.return_value = MOCK_CHATS_RESPONSE

        msgs_resp = MagicMock()
        msgs_resp.status_code = 200
        msgs_resp.json.return_value = MOCK_MESSAGES_RESPONSE

        mock_http.get.side_effect = [chat_resp, msgs_resp]

        messages = await teams_client.read_messages("dan", limit=20)
        assert len(messages) == 2
        assert messages[0].sender == "Dan"
        assert messages[0].content == "Blocked: waiting on API credentials"
        assert messages[1].sender == "Ops Console"

    @pytest.mark.asyncio
    async def test_read_graph_error(self, teams_client, mock_http):
        chat_resp = MagicMock()
        chat_resp.status_code = 200
        chat_resp.json.return_value = MOCK_CHATS_RESPONSE

        msgs_resp = MagicMock()
        msgs_resp.status_code = 403
        msgs_resp.text = "Forbidden"

        mock_http.get.side_effect = [chat_resp, msgs_resp]

        with pytest.raises(TeamsClientError, match="read failed"):
            await teams_client.read_messages("dan")


# --- Message Route Tests ---


class TestMessageRoutes:
    """Test POST /api/agents/{name}/message and GET /api/agents/{name}/messages."""

    @pytest.mark.asyncio
    async def test_send_message(self, client, app, mock_agent_service, mock_teams_client):
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            teams_client=mock_teams_client,
        )
        resp = await client.post(
            "/api/agents/dan/message",
            json={"content": "Hello Dan"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sender"] == "Ops"
        assert data["chat_id"] == "chat-123"

    @pytest.mark.asyncio
    async def test_read_messages(self, client, app, mock_agent_service, mock_teams_client):
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            teams_client=mock_teams_client,
        )
        resp = await client.get("/api/agents/dan/messages?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "dan"
        assert len(data["messages"]) == 1

    @pytest.mark.asyncio
    async def test_send_to_unknown_agent(self, client, app, mock_agent_service, mock_teams_client):
        from tech_dev_agents.agent_dashboard import AgentNotFoundError
        mock_agent_service.get_agent.side_effect = AgentNotFoundError("not found")
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            teams_client=mock_teams_client,
        )
        resp = await client.post(
            "/api/agents/unknown/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_graph_api_502(self, client, app, mock_agent_service, mock_teams_client):
        mock_teams_client.send_message.side_effect = TeamsClientError("Graph API error")
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            teams_client=mock_teams_client,
        )
        resp = await client.post(
            "/api/agents/dan/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 502
