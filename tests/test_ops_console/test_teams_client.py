"""Tests for the Teams Graph API client — STORY-016 v2.

Tests the TeamsClient class that bridges agent names to Microsoft Graph API
chat operations via Teams. All Graph API calls are mocked — no real HTTP.

Tests:
  T1: resolve_chat_id by agent name → email → Graph /me/chats → chat ID
  T2: resolve_chat_id for unknown agent → error, not crash
  T3: send_message calls Graph POST /chats/{chatId}/messages with correct body + auth
  T4: send_message without token returns clear auth error
  T5: read_messages returns parsed list [{sender, timestamp, content}]
  T6: read_messages with count param passes ?$top=N to Graph API
  T7: blocker detection flags "Blocked:" as has_blocker=true
  T8: blocker detection flags "Decision needed:" as has_blocker=true
  T9: blocker detection clean message → has_blocker=false
  T10: send_message refreshes expired token (401 → refresh → retry)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# The TeamsClient does not exist yet — these imports will fail (RED).
from tech_dev_agents.ops_console.clients.teams_client import (
    TeamsClient,
    TeamsAuthError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_REGISTRY = [
    {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True, "email": "dan@gorillacommerce.ai"},
    {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer", "enabled": True, "email": "derrick@gorillacommerce.ai"},
]

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
TEST_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.test-token"
TEST_CHAT_ID = "19:meeting_abc123@thread.v2"


@pytest.fixture
def registry_file(tmp_path):
    """Write test agent registry to temp file."""
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps(TEST_REGISTRY))
    return str(path)


@pytest.fixture
def mock_http():
    """Mocked httpx.AsyncClient for Graph API calls."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def teams_client(registry_file, mock_http):
    """TeamsClient wired to mocked HTTP and test registry."""
    return TeamsClient(
        http_client=mock_http,
        registry_path=registry_file,
        graph_base_url=GRAPH_BASE_URL,
        access_token=TEST_TOKEN,
    )


@pytest.fixture
def teams_client_no_token(registry_file, mock_http):
    """TeamsClient with no access token (simulates missing auth)."""
    return TeamsClient(
        http_client=mock_http,
        registry_path=registry_file,
        graph_base_url=GRAPH_BASE_URL,
        access_token="",
    )


# ---------------------------------------------------------------------------
# T1: resolve_chat_id_by_agent_name
# ---------------------------------------------------------------------------


class TestResolveChatId:
    """Resolve agent name → email → Graph /me/chats → chat ID."""

    @pytest.mark.asyncio
    async def test_resolve_chat_id_by_agent_name(self, teams_client, mock_http):
        """T1: name → email (from registry) → Graph /me/chats → chat ID."""
        # Graph returns a list of chats; one matches the agent's email as a member
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "id": TEST_CHAT_ID,
                    "chatType": "oneOnOne",
                    "members": [
                        {"email": "dan@gorillacommerce.ai"},
                        {"email": "hermes@gorillacommerce.ai"},
                    ],
                },
                {
                    "id": "19:other_chat@thread.v2",
                    "chatType": "oneOnOne",
                    "members": [
                        {"email": "someone@gorillacommerce.ai"},
                    ],
                },
            ]
        }
        mock_http.get.return_value = mock_response

        chat_id = await teams_client.resolve_chat_id("dan")

        assert chat_id == TEST_CHAT_ID
        # Verify Graph API was called with correct auth header
        mock_http.get.assert_called_once()
        call_args = mock_http.get.call_args
        assert "Authorization" in call_args.kwargs.get("headers", {}) or \
               "Authorization" in (call_args[1].get("headers", {}) if len(call_args) > 1 else {})

    @pytest.mark.asyncio
    async def test_resolve_chat_id_unknown_agent(self, teams_client, mock_http):
        """T2: Unknown agent name returns error, not crash."""
        with pytest.raises(Exception) as exc_info:
            await teams_client.resolve_chat_id("unknown_agent_xyz")

        # Should be a descriptive error, not a KeyError or crash
        assert "not found" in str(exc_info.value).lower() or \
               "unknown" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# T3-T4: send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    """Send message via Graph POST /chats/{chatId}/messages."""

    @pytest.mark.asyncio
    async def test_send_message_calls_graph_api(self, teams_client, mock_http):
        """T3: POST /chats/{chatId}/messages with correct body and auth."""
        # Mock resolve_chat_id to return a known chat ID
        teams_client.resolve_chat_id = AsyncMock(return_value=TEST_CHAT_ID)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "msg_001"}
        mock_http.post.return_value = mock_response

        result = await teams_client.send_message("dan", "Please focus on tests")

        assert result is not None
        # Verify Graph API POST call
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert f"/chats/{TEST_CHAT_ID}/messages" in url

        # Verify body contains the message content
        body = call_args.kwargs.get("json") or call_args.kwargs.get("data") or \
               (call_args[1].get("json") if len(call_args) > 1 else None)
        assert body is not None
        assert "Please focus on tests" in json.dumps(body)

    @pytest.mark.asyncio
    async def test_send_message_no_token_returns_auth_error(
        self, teams_client_no_token, mock_http
    ):
        """T4: Missing token returns clear auth error, not 500."""
        with pytest.raises(TeamsAuthError):
            await teams_client_no_token.send_message("dan", "hello")


# ---------------------------------------------------------------------------
# T5-T6: read_messages
# ---------------------------------------------------------------------------


class TestReadMessages:
    """Read messages from Graph GET /chats/{chatId}/messages."""

    @pytest.mark.asyncio
    async def test_read_messages_returns_parsed_list(self, teams_client, mock_http):
        """T5: GET /chats/{chatId}/messages returns [{sender, timestamp, content}]."""
        teams_client.resolve_chat_id = AsyncMock(return_value=TEST_CHAT_ID)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "id": "msg_001",
                    "from": {
                        "user": {"displayName": "Hermes"},
                    },
                    "createdDateTime": "2026-04-01T10:00:00Z",
                    "body": {"content": "Deploy to staging"},
                },
                {
                    "id": "msg_002",
                    "from": {
                        "user": {"displayName": "Dan"},
                    },
                    "createdDateTime": "2026-04-01T10:01:00Z",
                    "body": {"content": "Deploying now"},
                },
            ]
        }
        mock_http.get.return_value = mock_response

        messages = await teams_client.read_messages("dan")

        assert len(messages) == 2
        assert messages[0]["sender"] == "Hermes"
        assert messages[0]["timestamp"] == "2026-04-01T10:00:00Z"
        assert messages[0]["content"] == "Deploy to staging"
        assert messages[1]["sender"] == "Dan"
        assert messages[1]["content"] == "Deploying now"

    @pytest.mark.asyncio
    async def test_read_messages_with_count_param(self, teams_client, mock_http):
        """T6: ?$top=N passed to Graph API when count is specified."""
        teams_client.resolve_chat_id = AsyncMock(return_value=TEST_CHAT_ID)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}
        mock_http.get.return_value = mock_response

        await teams_client.read_messages("dan", count=3)

        # Verify $top=3 was passed in the request
        call_args = mock_http.get.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        params = call_args.kwargs.get("params", {})

        # Either in URL as query param or in params dict
        assert "$top=3" in url or params.get("$top") == 3 or params.get("$top") == "3"


# ---------------------------------------------------------------------------
# T7-T9: blocker detection
# ---------------------------------------------------------------------------


class TestBlockerDetection:
    """Blocker detection in message content."""

    @pytest.mark.asyncio
    async def test_blocker_detection_flags_blocked(self, teams_client):
        """T7: Message containing 'Blocked:' sets has_blocker=true."""
        result = teams_client.detect_blocker("Blocked: Need DB credentials from infra team")
        assert result["has_blocker"] is True

    @pytest.mark.asyncio
    async def test_blocker_detection_flags_decision_needed(self, teams_client):
        """T8: 'Decision needed:' sets has_blocker=true."""
        result = teams_client.detect_blocker("Decision needed: Which auth provider to use?")
        assert result["has_blocker"] is True

    @pytest.mark.asyncio
    async def test_blocker_detection_clean_message(self, teams_client):
        """T9: Normal message sets has_blocker=false."""
        result = teams_client.detect_blocker("Phase 8 implementation is going well, 3 tests passing")
        assert result["has_blocker"] is False


# ---------------------------------------------------------------------------
# T10: token refresh on 401
# ---------------------------------------------------------------------------


class TestTokenRefresh:
    """Token lifecycle management."""

    @pytest.mark.asyncio
    async def test_send_message_refreshes_expired_token(self, teams_client, mock_http):
        """T10: If Graph returns 401, refresh token and retry once."""
        teams_client.resolve_chat_id = AsyncMock(return_value=TEST_CHAT_ID)

        # First call returns 401 (expired token), second succeeds after refresh
        unauthorized_response = MagicMock()
        unauthorized_response.status_code = 401
        unauthorized_response.json.return_value = {"error": {"code": "InvalidAuthenticationToken"}}

        success_response = MagicMock()
        success_response.status_code = 201
        success_response.json.return_value = {"id": "msg_001"}

        mock_http.post.side_effect = [unauthorized_response, success_response]

        # Mock the refresh method
        teams_client.refresh_access_token = AsyncMock(return_value="new-token-456")

        result = await teams_client.send_message("dan", "test message")

        # Should have called refresh and retried
        teams_client.refresh_access_token.assert_called_once()
        assert mock_http.post.call_count == 2
        assert result is not None
