"""Tests for Teams message API routes — STORY-016 v2.

Tests the REST endpoints that proxy Teams messaging through the ops console.
Uses the same fixtures and patterns as tests/ops_console/conftest.py.

Tests:
  T1: POST /api/agents/dan/message returns 200
  T2: POST /api/agents/nobody/message returns 404
  T3: GET /api/agents/dan/messages returns list
  T4: GET /api/agents/dan/messages?count=3 limits results
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_API_KEY = "test-ops-console-key-12345"
TEST_AGENT_REGISTRY = [
    {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True, "email": "dan@gorillacommerce.ai"},
    {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer", "enabled": True, "email": "derrick@gorillacommerce.ai"},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_file(tmp_path):
    """Write test registry to a temp JSON file."""
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps(TEST_AGENT_REGISTRY))
    return str(path)


@pytest.fixture
def test_settings(registry_file) -> Settings:
    """Settings with test values."""
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=registry_file,
        graph_api_token="test-graph-token",
        azure_subscription_id=None,
    )


@pytest_asyncio.fixture
async def app(test_settings):
    """FastAPI app with test config."""
    application = create_app(settings=test_settings)
    yield application


@pytest_asyncio.fixture
async def client(app):
    """Authenticated httpx AsyncClient."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


@pytest.fixture
def mock_teams_client():
    """Mocked TeamsClient for route tests."""
    client = AsyncMock()
    client.send_message.return_value = {
        "success": True,
        "message_id": "msg_001",
    }
    client.read_messages.return_value = [
        {"sender": "Hermes", "timestamp": "2026-04-01T10:00:00Z", "content": "Deploy to staging"},
        {"sender": "Dan", "timestamp": "2026-04-01T10:01:00Z", "content": "Deploying now"},
    ]
    return client


def inject_mock_services(app, **kwargs):
    """Inject mock services into app.state for route testing."""
    for key, value in kwargs.items():
        setattr(app.state, key, value)


# ---------------------------------------------------------------------------
# T1: POST /api/agents/dan/message
# ---------------------------------------------------------------------------


class TestPostMessage:
    """POST /api/agents/{name}/message endpoint."""

    @pytest.mark.asyncio
    async def test_post_message_endpoint(self, client, app, mock_teams_client):
        """T1: POST /api/agents/dan/message returns 200 with success."""
        inject_mock_services(app, teams_client=mock_teams_client)

        resp = await client.post(
            "/api/agents/dan/message",
            json={"content": "Please focus on tests"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_teams_client.send_message.assert_called_once_with("dan", "Please focus on tests")

    @pytest.mark.asyncio
    async def test_post_message_unknown_agent(self, client, app, mock_teams_client):
        """T2: POST /api/agents/nobody/message returns 404."""
        # TeamsClient raises agent-not-found for unknown agents
        mock_teams_client.send_message.side_effect = ValueError("Agent 'nobody' not found in registry")
        inject_mock_services(app, teams_client=mock_teams_client)

        resp = await client.post(
            "/api/agents/nobody/message",
            json={"content": "hello"},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T3-T4: GET /api/agents/{name}/messages
# ---------------------------------------------------------------------------


class TestGetMessages:
    """GET /api/agents/{name}/messages endpoint."""

    @pytest.mark.asyncio
    async def test_get_messages_endpoint(self, client, app, mock_teams_client):
        """T3: GET /api/agents/dan/messages returns list of messages."""
        inject_mock_services(app, teams_client=mock_teams_client)

        resp = await client.get("/api/agents/dan/messages")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list) or "messages" in data
        # Check at least one message returned
        messages = data if isinstance(data, list) else data["messages"]
        assert len(messages) >= 1
        # Verify message structure
        msg = messages[0]
        assert "sender" in msg
        assert "timestamp" in msg
        assert "content" in msg

    @pytest.mark.asyncio
    async def test_get_messages_with_count(self, client, app, mock_teams_client):
        """T4: GET /api/agents/dan/messages?count=3 passes count to TeamsClient."""
        inject_mock_services(app, teams_client=mock_teams_client)

        resp = await client.get("/api/agents/dan/messages?count=3")

        assert resp.status_code == 200
        # Verify that read_messages was called with count=3
        mock_teams_client.read_messages.assert_called_once()
        call_args = mock_teams_client.read_messages.call_args
        # count should be passed as keyword arg or positional
        assert call_args.kwargs.get("count") == 3 or \
               (len(call_args.args) > 1 and call_args.args[1] == 3)
