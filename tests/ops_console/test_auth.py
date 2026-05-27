"""Tests for API key authentication — T51-T55."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tests.ops_console.conftest import (
    TEST_API_KEY,
    inject_mock_services,
)


class TestApiKeyAuth:
    """T51-T55: API key authentication dependency."""

    @pytest.mark.asyncio
    async def test_valid_api_key_passes(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T51: Request with correct X-API-Key header returns 200."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_401(
        self, unauthed_client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T52: Request without X-API-Key header returns 401 with 'Missing API key'."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await unauthed_client.get("/api/agents")
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert "Missing" in detail  # "Missing authentication credentials" or "Missing API key"

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(
        self, unauthed_client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T53: Request with wrong X-API-Key returns 401 with 'Invalid API key'."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await unauthed_client.get(
            "/api/agents",
            headers={"X-API-Key": "wrong-key-12345"},
        )
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_401(
        self, unauthed_client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T54: Request with empty X-API-Key header returns 401."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await unauthed_client.get(
            "/api/agents",
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_health_endpoint_exempt_from_auth(
        self, unauthed_client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T55: GET /api/health returns 200 without any API key (exempt from auth)."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
        )

        resp = await unauthed_client.get("/api/health")
        assert resp.status_code == 200
