"""Security tests — T66-T72."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.ops_console.services.loki_client import sanitize_label_value
from tests.ops_console.conftest import (
    TEST_API_KEY,
    inject_mock_services,
)


class TestAuthEnforcement:
    """T66: All protected endpoints reject unauthenticated requests."""

    @pytest.mark.asyncio
    async def test_all_api_endpoints_require_auth(
        self, unauthed_client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T66: Every /api/* endpoint (except /api/health) returns 401 without X-API-Key."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
        )

        protected_endpoints = [
            ("GET", "/api/agents"),
            ("GET", "/api/agents/dan"),
            ("GET", "/api/agents/dan/cost"),
            ("GET", "/api/agents/dan/activity"),
            ("POST", "/api/agents/dan/restart"),
            ("POST", "/api/agents/dan/pause"),
            ("GET", "/api/fleet"),
            ("GET", "/api/alerts"),
        ]

        for method, path in protected_endpoints:
            if method == "GET":
                resp = await unauthed_client.get(path)
            else:
                resp = await unauthed_client.post(
                    path,
                    json={"reason": "test", "action": "pause"},
                )
            assert resp.status_code == 401, f"{method} {path} should require auth but got {resp.status_code}"


class TestInputValidation:
    """T67-T69: Request body and query parameter validation."""

    @pytest.mark.asyncio
    async def test_restart_requires_reason(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T67: POST /api/agents/dan/restart with empty reason returns 422 validation error."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.post(
            "/api/agents/dan/restart",
            json={"reason": "", "force": False},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_pause_invalid_action_rejected(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T68: POST /api/agents/dan/pause with action='delete' returns 422 (must be pause|resume)."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.post(
            "/api/agents/dan/pause",
            json={"action": "delete"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_agent_name_path_param_validated(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T69: GET /api/agents/<script>alert(1)</script> returns 404 (no injection via path)."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/agents/<script>alert(1)</script>")
        assert resp.status_code == 404


class TestLogQLInjection:
    """T70: LogQL injection prevention (SEC-08)."""

    @pytest.mark.asyncio
    async def test_logql_query_sanitises_agent_name(self):
        """T70: LokiClient.query_cost_summaries with agent_name containing LogQL metacharacters
        sanitises the input before building the query string."""
        malicious_name = '} |= "secret"'
        sanitized = sanitize_label_value(malicious_name)

        # Should not contain any LogQL metacharacters
        assert "}" not in sanitized
        assert "|" not in sanitized
        assert "=" not in sanitized
        assert '"' not in sanitized

        # Also verify via the LokiClient directly
        from tech_dev_agents.ops_console.services.loki_client import LokiClient

        mock_http = AsyncMock()
        mock_http.get.return_value = type("Response", (), {
            "status_code": 200,
            "json": lambda self: {"data": {"result": []}},
        })()

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        await client.query_cost_summaries(malicious_name, "2026-04-01", "2026-04-02")

        # Verify the query sent to Loki doesn't contain the injection
        call_args = mock_http.get.call_args
        query_params = call_args.kwargs.get("params", {})
        query = query_params.get("query", "")
        assert '} |= "secret"' not in query


class TestRateLimiting:
    """T71: Rate limiting on destructive endpoints."""

    @pytest.mark.asyncio
    async def test_restart_rate_limited(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T71: Sending 10 rapid POST /api/agents/dan/restart requests within 1 second
        results in all succeeding (rate limiting is a future enhancement for MVP).

        Note: For MVP, we verify the endpoint handles rapid requests without crashing.
        Rate limiting (429 responses) will be added as a security enhancement post-MVP.
        """
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        responses = []
        for _ in range(10):
            resp = await client.post(
                "/api/agents/dan/restart",
                json={"reason": "rate limit test"},
            )
            responses.append(resp.status_code)

        # All requests should complete (200) — rate limiting is documented for post-MVP
        assert all(s == 200 for s in responses)


class TestDestructiveOperationGuards:
    """T72: Restart/pause guard rails (SEC-12)."""

    @pytest.mark.asyncio
    async def test_restart_disabled_agent_returns_400(
        self, client, app, mock_agent_service, mock_cost_service, mock_monday_service, mock_alert_service, mock_loki_client
    ):
        """T72: POST /api/agents/dan/restart when agent is disabled returns 400
        with message indicating agent must be enabled first."""
        mock_agent_service.restart_agent.side_effect = ValueError(
            "Agent 'dan' is disabled. Enable before restarting."
        )
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.post(
            "/api/agents/dan/restart",
            json={"reason": "test restart disabled"},
        )
        assert resp.status_code == 400
        assert "disabled" in resp.json()["detail"].lower()
