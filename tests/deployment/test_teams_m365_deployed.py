"""Unit tests for teams_m365_deployed — MSAL client-credentials auth for VM Teams adapter.

STORY-229: Bot-to-Bot Teams Messaging via App Token
Phase 7: RED state — tests written before implementation.

Test IDs:
  T01: _build_msal_app creates ConfidentialClientApplication with correct params
  T02: _build_msal_app is idempotent (second call reuses existing app)
  T03: _get_token acquires token via MSAL client credentials flow
  T04: _get_token returns cached token when not expired
  T05: _get_token refreshes token when within 60s of expiry
  T06: _get_token raises RuntimeError on MSAL error response
  T07: _graph_get retries on 401 with fresh token
  T08: check_teams_requirements passes when all env vars set and msal importable
  T09: check_teams_requirements fails when env var missing
  T10: _get_chats uses /users/{id}/ not /me/
  T11: _get_messages uses /users/{id}/ not /me/
  T12: send uses /users/{id}/ not /me/
  T13: _set_presence uses /users/{id}/ not /me/
  T14: connect acquires token and starts poll loop
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Stub gateway module — injected before importing the adapter so
# TeamsAdapter inherits from our stub instead of bare `object`.
# ---------------------------------------------------------------------------

class _StubBasePlatformAdapter:
    """Minimal stub for BasePlatformAdapter when hermes is not installed."""

    def __init__(self, config, platform):
        pass

    def build_source(self, **kwargs):
        return kwargs

    async def handle_message(self, event):
        pass

    def _mark_connected(self):
        pass


class _StubSendResult:
    """Minimal stub for SendResult."""
    def __init__(self, success: bool, message_id: str):
        self.success = success
        self.message_id = message_id


class _StubMessageEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _StubMessageType:
    TEXT = "text"


class _StubPlatform:
    TEAMS = "teams"


def _ensure_gateway_stub():
    """Inject a fake gateway.platforms.base into sys.modules if hermes is not installed."""
    if "gateway.platforms.base" in sys.modules:
        return  # real hermes is available
    # Build stub module
    stub = ModuleType("gateway.platforms.base")
    stub.BasePlatformAdapter = _StubBasePlatformAdapter
    stub.MessageEvent = _StubMessageEvent
    stub.MessageType = _StubMessageType
    stub.PlatformConfig = MagicMock
    stub.Platform = _StubPlatform
    stub.SendResult = _StubSendResult
    # Also register parent packages
    for name in ("gateway", "gateway.platforms"):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
    sys.modules["gateway.platforms.base"] = stub


# Inject before any import of the adapter module
_ensure_gateway_stub()

# Force re-import of the adapter so it picks up the stub base class
if "deployment.vm.teams_m365_deployed" in sys.modules:
    del sys.modules["deployment.vm.teams_m365_deployed"]

import deployment.vm.teams_m365_deployed as _mod  # noqa: E402


def _make_adapter(env_overrides: dict | None = None):
    """Create a TeamsAdapter with mocked PlatformConfig and env vars."""
    env = {
        "TEAMS_CLIENT_ID": "test-client-id",
        "TEAMS_CLIENT_SECRET": "test-secret",
        "TEAMS_TENANT_ID": "test-tenant-id",
        "TEAMS_BOT_USER_ID": "bot-user-object-id",
        "TEAMS_POLL_INTERVAL": "5",
    }
    if env_overrides:
        env.update(env_overrides)

    mock_config = MagicMock()

    with patch.dict("os.environ", env, clear=False):
        adapter = _mod.TeamsAdapter(mock_config)

    return adapter


def _mock_msal_result(access_token: str = "mock-token-123", expires_in: int = 3600):
    """Return a successful MSAL token result dict."""
    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": "Bearer",
    }


def _mock_msal_error(error: str = "invalid_client", desc: str = "Bad credentials"):
    """Return a failed MSAL token result dict."""
    return {
        "error": error,
        "error_description": desc,
    }


# ---------------------------------------------------------------------------
# T01-T02: MSAL app construction
# ---------------------------------------------------------------------------

class TestBuildMsalApp:
    """T01-T02: MSAL ConfidentialClientApplication lifecycle."""

    def test_creates_msal_app_with_correct_params(self):
        """T01: _build_msal_app creates ConfidentialClientApplication with tenant, client_id, secret."""
        adapter = _make_adapter()

        with patch.object(_mod, "msal") as mock_msal:
            mock_app = MagicMock()
            mock_msal.ConfidentialClientApplication.return_value = mock_app

            adapter._build_msal_app()

            mock_msal.ConfidentialClientApplication.assert_called_once_with(
                "test-client-id",
                authority="https://login.microsoftonline.com/test-tenant-id",
                client_credential="test-secret",
            )
            assert adapter._msal_app is mock_app

    def test_idempotent_second_call_reuses(self):
        """T02: Second call to _build_msal_app does not create a new MSAL app."""
        adapter = _make_adapter()
        sentinel = MagicMock()
        adapter._msal_app = sentinel

        with patch.object(_mod, "msal") as mock_msal:
            adapter._build_msal_app()
            mock_msal.ConfidentialClientApplication.assert_not_called()
            assert adapter._msal_app is sentinel


# ---------------------------------------------------------------------------
# T03-T06: Token acquisition
# ---------------------------------------------------------------------------

class TestGetToken:
    """T03-T06: MSAL token acquisition and caching."""

    @pytest.mark.asyncio
    async def test_acquires_token_via_client_credentials(self):
        """T03: _get_token calls acquire_token_for_client with Graph scopes."""
        adapter = _make_adapter()
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = _mock_msal_result("fresh-token")
        adapter._msal_app = mock_app

        token = await adapter._get_token()

        assert token == "fresh-token"
        mock_app.acquire_token_for_client.assert_called_once()
        call_args = mock_app.acquire_token_for_client.call_args
        assert call_args[1]["scopes"] == ["https://graph.microsoft.com/.default"]

    @pytest.mark.asyncio
    async def test_returns_cached_token_when_not_expired(self):
        """T04: _get_token returns cached token without calling MSAL when token is valid."""
        adapter = _make_adapter()
        adapter._token = "cached-token"
        adapter._token_expires = time.monotonic() + 300  # 5 minutes from now

        mock_app = MagicMock()
        adapter._msal_app = mock_app

        token = await adapter._get_token()

        assert token == "cached-token"
        mock_app.acquire_token_for_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_refreshes_token_near_expiry(self):
        """T05: _get_token refreshes when token is within 60s of expiry."""
        adapter = _make_adapter()
        adapter._token = "old-token"
        adapter._token_expires = time.monotonic() + 30  # Only 30s left — within 60s buffer

        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = _mock_msal_result("new-token")
        adapter._msal_app = mock_app

        token = await adapter._get_token()

        assert token == "new-token"
        mock_app.acquire_token_for_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_msal_error(self):
        """T06: _get_token raises RuntimeError when MSAL returns error."""
        adapter = _make_adapter()
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = _mock_msal_error()
        adapter._msal_app = mock_app

        with pytest.raises(RuntimeError, match="MSAL token acquisition failed"):
            await adapter._get_token()


# ---------------------------------------------------------------------------
# T07: 401 retry
# ---------------------------------------------------------------------------

class TestGraphRetry:
    """T07: Graph API 401 retry triggers token refresh."""

    @pytest.mark.asyncio
    async def test_retries_on_401(self):
        """T07: _graph_get retries once with a fresh token on 401."""
        adapter = _make_adapter()
        adapter._token = "expired-token"
        adapter._token_expires = time.monotonic() + 300

        # Mock session
        mock_response_401 = AsyncMock()
        mock_response_401.status = 401
        mock_response_401.__aenter__ = AsyncMock(return_value=mock_response_401)
        mock_response_401.__aexit__ = AsyncMock(return_value=False)

        mock_response_200 = AsyncMock()
        mock_response_200.status = 200
        mock_response_200.json = AsyncMock(return_value={"value": ["ok"]})
        mock_response_200.__aenter__ = AsyncMock(return_value=mock_response_200)
        mock_response_200.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=[mock_response_401, mock_response_200])
        adapter._session = mock_session

        # Mock token refresh
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = _mock_msal_result("refreshed-token")
        adapter._msal_app = mock_app

        result = await adapter._graph_get("/users/bot-user-object-id/chats")

        assert result == {"value": ["ok"]}
        # Token should have been refreshed
        assert adapter._token == "refreshed-token"


# ---------------------------------------------------------------------------
# T08-T09: Requirement checks
# ---------------------------------------------------------------------------

class TestRequirements:
    """T08-T09: check_teams_requirements validation."""

    def test_passes_when_all_present(self):
        """T08: Returns empty list when all env vars set and msal importable."""
        env = {
            "TEAMS_CLIENT_ID": "cid",
            "TEAMS_CLIENT_SECRET": "secret",
            "TEAMS_TENANT_ID": "tid",
            "TEAMS_BOT_USER_ID": "uid",
        }
        with patch.dict("os.environ", env, clear=False), \
             patch.object(_mod, "_MSAL_AVAILABLE", True), \
             patch.object(_mod, "_HERMES_BASE_AVAILABLE", True):
            result = _mod.check_teams_requirements()
        assert result == []

    def test_fails_when_env_var_missing(self):
        """T09: Returns error when required env var is missing."""
        env = {
            "TEAMS_CLIENT_ID": "cid",
            # TEAMS_CLIENT_SECRET deliberately missing
            "TEAMS_TENANT_ID": "tid",
            "TEAMS_BOT_USER_ID": "uid",
        }
        # Clear the missing var to ensure it's not set from parent env
        with patch.dict("os.environ", env, clear=False), \
             patch.dict("os.environ", {"TEAMS_CLIENT_SECRET": ""}, clear=False), \
             patch.object(_mod, "_MSAL_AVAILABLE", True), \
             patch.object(_mod, "_HERMES_BASE_AVAILABLE", True):
            result = _mod.check_teams_requirements()
        assert any("TEAMS_CLIENT_SECRET" in msg for msg in result)


# ---------------------------------------------------------------------------
# T10-T13: /users/{id}/ URL construction (no /me/)
# ---------------------------------------------------------------------------

class TestUserPathEndpoints:
    """T10-T13: All Graph calls use /users/{bot_user_id}/, never /me/."""

    @pytest.mark.asyncio
    async def test_get_chats_uses_user_path(self):
        """T10: _get_chats calls /users/{id}/chats, not /me/chats."""
        adapter = _make_adapter()
        adapter._graph_get = AsyncMock(return_value={"value": []})

        await adapter._get_chats()

        call_path = adapter._graph_get.call_args[0][0]
        assert "/users/bot-user-object-id/chats" in call_path
        assert "/me/" not in call_path

    @pytest.mark.asyncio
    async def test_get_messages_uses_user_path(self):
        """T11: _get_messages calls /users/{id}/chats/{chatId}/messages."""
        adapter = _make_adapter()
        adapter._graph_get = AsyncMock(return_value={"value": []})

        await adapter._get_messages("chat-abc-123")

        call_path = adapter._graph_get.call_args[0][0]
        assert "/users/bot-user-object-id/chats/chat-abc-123/messages" in call_path
        assert "/me/" not in call_path

    @pytest.mark.asyncio
    async def test_send_uses_user_path(self):
        """T12: send() posts to /users/{id}/chats/{chatId}/messages."""
        adapter = _make_adapter()
        adapter._graph_post = AsyncMock(return_value={"id": "msg-1"})

        result = await adapter.send("chat-abc-123", "hello world")

        call_path = adapter._graph_post.call_args[0][0]
        assert "/users/bot-user-object-id/chats/chat-abc-123/messages" in call_path
        assert "/me/" not in call_path

    @pytest.mark.asyncio
    async def test_set_presence_uses_user_path(self):
        """T13: _set_presence calls /users/{id}/presence/setPresence."""
        adapter = _make_adapter()
        adapter._graph_post = AsyncMock(return_value={})

        await adapter._set_presence("Available", "Available")

        call_path = adapter._graph_post.call_args[0][0]
        assert "/users/bot-user-object-id/presence/setPresence" in call_path
        assert "/me/" not in call_path


# ---------------------------------------------------------------------------
# T14: Connect flow
# ---------------------------------------------------------------------------

class TestConnectFlow:
    """T14: connect() acquires token and starts poll loop."""

    @pytest.mark.asyncio
    async def test_connect_acquires_token_and_starts_polling(self):
        """T14: connect() calls _get_token(), initializes chats, starts poll task."""
        adapter = _make_adapter()
        adapter._get_token = AsyncMock(return_value="init-token")
        adapter._set_presence = AsyncMock()
        adapter._get_chats = AsyncMock(return_value=[])
        adapter._mark_connected = MagicMock()

        # Mock aiohttp session
        with patch.object(_mod.aiohttp, "ClientSession") as mock_cs:
            mock_cs.return_value = MagicMock()

            # Mock asyncio.ensure_future to capture tasks without running them
            with patch.object(_mod.asyncio, "ensure_future") as mock_future:
                result = await adapter.connect()

        assert result is True
        adapter._get_token.assert_awaited_once()
        adapter._set_presence.assert_awaited()
        adapter._get_chats.assert_awaited_once()
        # Poll loop and presence monitor should be scheduled
        assert mock_future.call_count >= 1  # At least poll_loop
