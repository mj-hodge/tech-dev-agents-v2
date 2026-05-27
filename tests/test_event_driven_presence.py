"""Tests for STORY-304: Event-Driven Teams Presence.

12 tests covering AC-1 through AC-6.

Two presence paths:
  1. Dev agents: ops-console pushes Busy/Available on dispatch transitions
     via POST /internal/presence on the agent's health server.
  2. Morris (manager): heartbeat decides locally based on SDK + inbox activity.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# T01: POST /internal/presence sets Teams presence via Graph API
# ---------------------------------------------------------------------------


class TestInternalPresenceSetsTeamsPresence:
    """AC-1: The /internal/presence endpoint calls Graph API to set presence."""

    @patch.dict(os.environ, {
        "OPS_CONSOLE_API_KEY": "test-key-123",
        "GRAPH_ACCESS_TOKEN": "fake-graph-token",
        "GRAPH_USER_ID": "fake-user-id",
    })
    @patch("presence_manager._set_presence", return_value=True)
    def test_sets_presence_via_graph(self, mock_set_presence):
        from deployment.hermes.presence_endpoint import handle_presence_request

        status, body = handle_presence_request(
            headers={"X-API-Key": "test-key-123"},
            body=json.dumps({"availability": "Busy", "activity": "InACall"}),
        )

        assert status == 200
        assert body["availability"] == "Busy"
        assert body["activity"] == "InACall"
        mock_set_presence.assert_called_once_with("Busy", "InACall")


# ---------------------------------------------------------------------------
# T02: POST /internal/presence rejects missing/invalid API key
# ---------------------------------------------------------------------------


class TestInternalPresenceRejectsInvalidAuth:
    """AC-1: Endpoint requires valid OPS_CONSOLE_API_KEY."""

    @patch.dict(os.environ, {"OPS_CONSOLE_API_KEY": "test-key-123"})
    def test_rejects_missing_api_key(self):
        from deployment.hermes.presence_endpoint import handle_presence_request

        status, body = handle_presence_request(
            headers={},
            body=json.dumps({"availability": "Busy", "activity": "InACall"}),
        )
        assert status == 401

    @patch.dict(os.environ, {"OPS_CONSOLE_API_KEY": "test-key-123"})
    def test_rejects_wrong_api_key(self):
        from deployment.hermes.presence_endpoint import handle_presence_request

        status, body = handle_presence_request(
            headers={"X-API-Key": "wrong-key"},
            body=json.dumps({"availability": "Busy", "activity": "InACall"}),
        )
        assert status == 401


# ---------------------------------------------------------------------------
# T03: POST /internal/presence rejects malformed body
# ---------------------------------------------------------------------------


class TestInternalPresenceRejectsMalformedBody:
    """AC-1: Endpoint validates request body shape."""

    @patch.dict(os.environ, {"OPS_CONSOLE_API_KEY": "test-key-123"})
    def test_rejects_missing_fields(self):
        from deployment.hermes.presence_endpoint import handle_presence_request

        status, body = handle_presence_request(
            headers={"X-API-Key": "test-key-123"},
            body=json.dumps({"availability": "Busy"}),  # missing activity
        )
        assert status == 400

    @patch.dict(os.environ, {"OPS_CONSOLE_API_KEY": "test-key-123"})
    def test_rejects_invalid_json(self):
        from deployment.hermes.presence_endpoint import handle_presence_request

        status, body = handle_presence_request(
            headers={"X-API-Key": "test-key-123"},
            body="not json",
        )
        assert status == 400


# ---------------------------------------------------------------------------
# T04: Dispatch claim triggers Busy push to agent gateway
# ---------------------------------------------------------------------------


class TestDispatchClaimPushesBusy:
    """AC-2: When an agent claims a story, ops-console pushes Busy."""

    @pytest.mark.asyncio
    async def test_claim_pushes_busy(self):
        from tech_dev_agents.ops_console.services.presence_push import push_presence

        mock_agent_service = MagicMock()
        mock_agent = MagicMock()
        mock_agent.host = "10.0.0.1"
        mock_agent.port = 8080
        mock_agent_service.get_agent.return_value = mock_agent

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post = AsyncMock(return_value=mock_resp)

        await push_presence(
            agent_name="dan",
            availability="Busy",
            activity="InACall",
            agent_service=mock_agent_service,
            http_client=mock_client,
        )

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "10.0.0.1" in call_args[0][0]
        assert call_args[1]["json"]["availability"] == "Busy"


# ---------------------------------------------------------------------------
# T05: Dispatch complete triggers Available push
# ---------------------------------------------------------------------------


class TestDispatchCompletePushesAvailable:
    """AC-3: When a story completes, ops-console pushes Available."""

    @pytest.mark.asyncio
    async def test_complete_pushes_available(self):
        from tech_dev_agents.ops_console.services.presence_push import push_presence

        mock_agent_service = MagicMock()
        mock_agent = MagicMock()
        mock_agent.host = "10.0.0.2"
        mock_agent.port = 8080
        mock_agent_service.get_agent.return_value = mock_agent

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post = AsyncMock(return_value=mock_resp)

        await push_presence(
            agent_name="dan",
            availability="Available",
            activity="Available",
            agent_service=mock_agent_service,
            http_client=mock_client,
        )

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["availability"] == "Available"


# ---------------------------------------------------------------------------
# T06: Dispatch fail triggers Available push
# ---------------------------------------------------------------------------


class TestDispatchFailPushesAvailable:
    """AC-3: When a story fails, ops-console pushes Available."""

    @pytest.mark.asyncio
    async def test_fail_pushes_available(self):
        from tech_dev_agents.ops_console.services.presence_push import push_presence

        mock_agent_service = MagicMock()
        mock_agent = MagicMock()
        mock_agent.host = "10.0.0.3"
        mock_agent.port = 8080
        mock_agent_service.get_agent.return_value = mock_agent

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post = AsyncMock(return_value=mock_resp)

        await push_presence(
            agent_name="dan",
            availability="Available",
            activity="Available",
            agent_service=mock_agent_service,
            http_client=mock_client,
        )

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["availability"] == "Available"


# ---------------------------------------------------------------------------
# T07: Dispatch cancel triggers Available push
# ---------------------------------------------------------------------------


class TestDispatchCancelPushesAvailable:
    """AC-3: When a story is cancelled, ops-console pushes Available."""

    @pytest.mark.asyncio
    async def test_cancel_pushes_available(self):
        from tech_dev_agents.ops_console.services.presence_push import push_presence

        mock_agent_service = MagicMock()
        mock_agent = MagicMock()
        mock_agent.host = "10.0.0.4"
        mock_agent.port = 8080
        mock_agent_service.get_agent.return_value = mock_agent

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post = AsyncMock(return_value=mock_resp)

        await push_presence(
            agent_name="dan",
            availability="Available",
            activity="Available",
            agent_service=mock_agent_service,
            http_client=mock_client,
        )

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["availability"] == "Available"


# ---------------------------------------------------------------------------
# T08: Presence push fails silently (backward compat)
# ---------------------------------------------------------------------------


class TestPresencePushFailsSilently:
    """AC-6: Presence push to unreachable agent does not raise."""

    @pytest.mark.asyncio
    async def test_unreachable_agent_no_error(self):
        """Connection error is swallowed after retries."""
        from tech_dev_agents.ops_console.services.presence_push import push_presence

        mock_agent_service = MagicMock()
        mock_agent = MagicMock()
        mock_agent.host = "10.0.0.99"
        mock_agent.port = 8080
        mock_agent_service.get_agent.return_value = mock_agent

        import httpx
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        # Must not raise
        await push_presence(
            agent_name="dan",
            availability="Available",
            activity="Available",
            agent_service=mock_agent_service,
            http_client=mock_client,
        )

    @pytest.mark.asyncio
    async def test_agent_not_in_registry_no_error(self):
        """If agent is not in registry, push is a no-op."""
        from tech_dev_agents.ops_console.services.presence_push import push_presence

        mock_agent_service = MagicMock()
        mock_agent_service.get_agent.side_effect = KeyError("not found")

        mock_client = AsyncMock()

        # Must not raise
        await push_presence(
            agent_name="unknown-agent",
            availability="Busy",
            activity="InACall",
            agent_service=mock_agent_service,
            http_client=mock_client,
        )

        # No HTTP call should have been made
        mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# T09: _presence_monitor_loop and _is_busy removed from TeamsAdapter
# ---------------------------------------------------------------------------


class TestPresenceMonitorRemoved:
    """AC-4: TeamsAdapter no longer has polling-based presence."""

    def test_no_presence_monitor_loop(self):
        """_presence_monitor_loop method must not exist on TeamsAdapter."""
        from deployment.hermes.teams_m365 import TeamsAdapter
        assert not hasattr(TeamsAdapter, "_presence_monitor_loop"), \
            "TeamsAdapter still has _presence_monitor_loop — must be removed"

    def test_no_is_busy_in_process_message(self):
        """_is_busy should not be set in _process_message."""
        from deployment.hermes.teams_m365 import TeamsAdapter
        source = inspect.getsource(TeamsAdapter._process_message)
        assert "_is_busy" not in source, \
            "_is_busy still referenced in _process_message — must be removed"


# ---------------------------------------------------------------------------
# T10: Morris heartbeat: sdk+inbox active -> Busy
# ---------------------------------------------------------------------------


class TestMorrisHeartbeatBusy:
    """AC-5: Morris sets Busy when SDK running or unread inbox."""

    @pytest.mark.asyncio
    async def test_busy_when_sdk_and_inbox_active(self):
        from deployment.hermes.teams_m365 import TeamsAdapter

        adapter = TeamsAdapter.__new__(TeamsAdapter)
        adapter._running = True
        adapter._session = MagicMock()
        adapter._token = "fake-token"
        adapter._token_expires = float("inf")
        adapter._bot_user_id = ""
        adapter._last_seen = {}

        # Mock _get_sdk_count and _get_unread_inbox_count
        adapter._get_sdk_count = AsyncMock(return_value=1)
        adapter._get_unread_inbox_count = AsyncMock(return_value=2)
        adapter._set_presence = AsyncMock()

        await adapter._morris_heartbeat_tick()

        adapter._set_presence.assert_called_with("Busy", "InACall")


# ---------------------------------------------------------------------------
# T11: Morris heartbeat: idle -> Available
# ---------------------------------------------------------------------------


class TestMorrisHeartbeatAvailable:
    """AC-5: Morris sets Available when idle."""

    @pytest.mark.asyncio
    async def test_available_when_idle(self):
        from deployment.hermes.teams_m365 import TeamsAdapter

        adapter = TeamsAdapter.__new__(TeamsAdapter)
        adapter._running = True
        adapter._session = MagicMock()
        adapter._token = "fake-token"
        adapter._token_expires = float("inf")
        adapter._bot_user_id = ""
        adapter._last_seen = {}

        adapter._get_sdk_count = AsyncMock(return_value=0)
        adapter._get_unread_inbox_count = AsyncMock(return_value=0)
        adapter._set_presence = AsyncMock()

        await adapter._morris_heartbeat_tick()

        adapter._set_presence.assert_called_with("Available", "Available")


# ---------------------------------------------------------------------------
# T12: Agent IP captured during dispatch registration
# ---------------------------------------------------------------------------


class _MockAsyncCtx:
    """Helper: async context manager that yields a mock connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


class TestAgentIPCapture:
    """AC-2: Agent IP stored during registration for presence pushes."""

    @pytest.mark.asyncio
    async def test_register_agent_stores_ip(self):
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"is_new": True})
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = _MockAsyncCtx(mock_conn)

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = mock_pool

        await svc.register_agent("dan", ip="10.0.0.1")

        # Verify the SQL includes the IP
        call_args = mock_conn.fetchrow.call_args
        sql = call_args[0][0]
        assert "ip" in sql.lower(), "register_agent SQL should include ip column"

    @pytest.mark.asyncio
    async def test_get_agent_ip(self):
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value="10.0.0.1")
        mock_pool = MagicMock()
        mock_pool.acquire.return_value = _MockAsyncCtx(mock_conn)

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = mock_pool

        ip = await svc.get_agent_ip("dan")
        assert ip == "10.0.0.1"
