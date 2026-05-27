"""Tests for ops-console presence push service (AC-1, AC-2, AC-6).

STORY-304: Event-Driven Teams Presence
Tests the push_presence() service that sends presence state to agent gateways.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tech_dev_agents.ops_console.services.presence_push import (
    push_presence,
    _resolve_agent_gateway_url,
    PRESENCE_PATH,
    MAX_RETRIES,
    BASE_DELAY,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_agent_service():
    """Mock AgentService that returns agent records with host/port."""
    svc = MagicMock()
    agent = MagicMock()
    agent.host = "10.0.0.1"
    agent.port = 8080
    agent.name = "dan"
    svc.get_agent.return_value = agent
    return svc


@pytest.fixture
def mock_http_client():
    """Mock httpx.AsyncClient."""
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "OK"
    client.post.return_value = resp
    return client


# ---------------------------------------------------------------------------
# T1: push_presence sends Busy on claim (AC-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_busy_on_claim(mock_agent_service, mock_http_client):
    """T1: push_presence sends Busy/InACall to agent gateway on claim."""
    await push_presence(
        agent_name="dan",
        availability="Busy",
        activity="InACall",
        agent_service=mock_agent_service,
        http_client=mock_http_client,
    )

    mock_http_client.post.assert_called_once()
    call_args = mock_http_client.post.call_args
    assert call_args[0][0] == "http://10.0.0.1:8080/internal/presence"
    body = call_args[1]["json"]
    assert body["availability"] == "Busy"
    assert body["activity"] == "InACall"


# ---------------------------------------------------------------------------
# T2: push_presence sends Available on complete (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_available_on_complete(mock_agent_service, mock_http_client):
    """T2: push_presence sends Available/Available to agent gateway on complete."""
    await push_presence(
        agent_name="dan",
        availability="Available",
        activity="Available",
        agent_service=mock_agent_service,
        http_client=mock_http_client,
    )

    call_args = mock_http_client.post.call_args
    body = call_args[1]["json"]
    assert body["availability"] == "Available"
    assert body["activity"] == "Available"


# ---------------------------------------------------------------------------
# T3: push_presence sends Available on fail (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_available_on_fail(mock_agent_service, mock_http_client):
    """T3: push_presence sends Available/Available on fail event."""
    await push_presence(
        agent_name="dan",
        availability="Available",
        activity="Available",
        agent_service=mock_agent_service,
        http_client=mock_http_client,
    )

    assert mock_http_client.post.call_count == 1
    body = mock_http_client.post.call_args[1]["json"]
    assert body["availability"] == "Available"


# ---------------------------------------------------------------------------
# T4: Retry on ConnectionError with exponential backoff (AC-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_retries_on_connection_error(mock_agent_service):
    """T4: push_presence retries with backoff on ConnectionError, then gives up silently."""
    import httpx

    client = AsyncMock()
    client.post.side_effect = httpx.ConnectError("Connection refused")

    # Should not raise — fails silently (AC-6)
    await push_presence(
        agent_name="dan",
        availability="Busy",
        activity="InACall",
        agent_service=mock_agent_service,
        http_client=client,
    )

    # Should have retried MAX_RETRIES times
    assert client.post.call_count == MAX_RETRIES


# ---------------------------------------------------------------------------
# T5: Retry on 5xx, no retry on 4xx (except 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_retries_on_5xx(mock_agent_service):
    """T5: push_presence retries on 5xx responses."""
    client = AsyncMock()
    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.text = "Internal Server Error"
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = "OK"

    client.post.side_effect = [resp_500, resp_200]

    await push_presence(
        agent_name="dan",
        availability="Busy",
        activity="InACall",
        agent_service=mock_agent_service,
        http_client=client,
    )

    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_push_presence_no_retry_on_4xx(mock_agent_service):
    """T5b: push_presence does NOT retry on 4xx (except 401)."""
    client = AsyncMock()
    resp_400 = MagicMock()
    resp_400.status_code = 400
    resp_400.text = "Bad Request"

    client.post.return_value = resp_400

    await push_presence(
        agent_name="dan",
        availability="Busy",
        activity="InACall",
        agent_service=mock_agent_service,
        http_client=client,
    )

    # Should NOT retry on 400
    assert client.post.call_count == 1


# ---------------------------------------------------------------------------
# T6: resolve_agent_gateway_url builds correct URL
# ---------------------------------------------------------------------------


def test_resolve_agent_gateway_url(mock_agent_service):
    """T6: URL resolution uses agent host:port from registry."""
    url = _resolve_agent_gateway_url("dan", mock_agent_service)
    assert url == "http://10.0.0.1:8080/internal/presence"


def test_resolve_agent_gateway_url_unknown_agent():
    """T6b: URL resolution returns None for unknown agent."""
    from tech_dev_agents.agent_dashboard import AgentNotFoundError

    svc = MagicMock()
    svc.get_agent.side_effect = AgentNotFoundError("not found")
    url = _resolve_agent_gateway_url("unknown", svc)
    assert url is None


# ---------------------------------------------------------------------------
# T7: OPS_CONSOLE_API_KEY sent in header (AC-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_sends_api_key_header(mock_agent_service, mock_http_client):
    """T7: push_presence includes OPS_CONSOLE_API_KEY in X-API-Key header."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "test-secret-key"}):
        await push_presence(
            agent_name="dan",
            availability="Busy",
            activity="InACall",
            agent_service=mock_agent_service,
            http_client=mock_http_client,
        )

    call_args = mock_http_client.post.call_args
    headers = call_args[1]["headers"]
    assert headers["X-API-Key"] == "test-secret-key"


# ---------------------------------------------------------------------------
# T8: Silent failure when agent not in registry (AC-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_presence_silent_on_unknown_agent():
    """T8: push_presence fails silently when agent not found in registry."""
    from tech_dev_agents.agent_dashboard import AgentNotFoundError

    svc = MagicMock()
    svc.get_agent.side_effect = AgentNotFoundError("not found")
    client = AsyncMock()

    # Should not raise
    await push_presence(
        agent_name="unknown",
        availability="Busy",
        activity="InACall",
        agent_service=svc,
        http_client=client,
    )

    # Should not have attempted HTTP call
    client.post.assert_not_called()
