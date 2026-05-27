"""Tests for MSAL Graph API token provider — STORY-228.

Tests:
  T1: acquire token via client credentials flow
  T2: MSAL error raises RuntimeError with description
  T3: __call__ runs in executor (non-blocking)
  T4: factory returns None when credentials are incomplete
  T5: factory returns provider when all credentials present
  T6: lazy MSAL app initialisation (built once, reused)
  T7: integration with v2 TeamsClient token_provider on 401 refresh
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.ops_console.graph_token_provider import (
    GRAPH_SCOPES,
    GraphTokenProvider,
    create_graph_token_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT_ID = "test-tenant-id"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"


@pytest.fixture
def provider():
    return GraphTokenProvider(TENANT_ID, CLIENT_ID, CLIENT_SECRET)


@pytest.fixture
def mock_msal_app():
    """Mock msal.ConfidentialClientApplication."""
    app = MagicMock()
    app.acquire_token_for_client.return_value = {
        "access_token": "eyJ0eXAi.fresh-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    return app


# ---------------------------------------------------------------------------
# T1: acquire token via client credentials
# ---------------------------------------------------------------------------


class TestAcquireToken:
    def test_acquire_token_sync_returns_access_token(self, provider, mock_msal_app):
        """T1: Synchronous acquisition returns the access_token string."""
        provider._msal_app = mock_msal_app

        token = provider._acquire_token_sync()

        assert token == "eyJ0eXAi.fresh-token"
        mock_msal_app.acquire_token_for_client.assert_called_once_with(
            scopes=GRAPH_SCOPES,
        )

    def test_acquire_token_sync_raises_on_error(self, provider, mock_msal_app):
        """T2: MSAL error result raises RuntimeError with description."""
        mock_msal_app.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "Client secret is expired",
        }
        provider._msal_app = mock_msal_app

        with pytest.raises(RuntimeError, match="Client secret is expired"):
            provider._acquire_token_sync()


# ---------------------------------------------------------------------------
# T3: async __call__ uses executor
# ---------------------------------------------------------------------------


class TestAsyncCall:
    @pytest.mark.asyncio
    async def test_call_runs_in_executor(self, provider):
        """T3: __call__ offloads sync MSAL to run_in_executor."""
        provider._acquire_token_sync = MagicMock(return_value="async-token")

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                return_value="async-token",
            )
            token = await provider()

        assert token == "async-token"
        mock_loop.return_value.run_in_executor.assert_called_once_with(
            None, provider._acquire_token_sync,
        )


# ---------------------------------------------------------------------------
# T4-T5: factory function
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_returns_none_when_incomplete(self):
        """T4: Missing credentials -> None (graceful fallback)."""
        assert create_graph_token_provider("", CLIENT_ID, CLIENT_SECRET) is None
        assert create_graph_token_provider(TENANT_ID, "", CLIENT_SECRET) is None
        assert create_graph_token_provider(TENANT_ID, CLIENT_ID, "") is None
        assert create_graph_token_provider("", "", "") is None

    def test_factory_returns_provider_when_complete(self):
        """T5: All credentials present -> GraphTokenProvider instance."""
        result = create_graph_token_provider(TENANT_ID, CLIENT_ID, CLIENT_SECRET)
        assert isinstance(result, GraphTokenProvider)


# ---------------------------------------------------------------------------
# T6: lazy initialisation
# ---------------------------------------------------------------------------


class TestLazyInit:
    def test_msal_app_built_once(self, provider):
        """T6: _build_msal_app creates app once and reuses it."""
        mock_app = MagicMock()
        mock_msal_module = MagicMock()
        mock_msal_module.ConfidentialClientApplication.return_value = mock_app

        with patch.dict("sys.modules", {"msal": mock_msal_module}):
            app1 = provider._build_msal_app()
            app2 = provider._build_msal_app()

            assert app1 is app2
            mock_msal_module.ConfidentialClientApplication.assert_called_once()


# ---------------------------------------------------------------------------
# T7: integration with v2 TeamsClient
# ---------------------------------------------------------------------------


class TestTeamsClientIntegration:
    @pytest.mark.asyncio
    async def test_token_provider_wired_to_teams_client(self):
        """T7: GraphTokenProvider works as TeamsClient.token_provider."""
        import json
        import tempfile
        from unittest.mock import MagicMock as SyncMock

        from tech_dev_agents.ops_console.clients.teams_client import TeamsClient

        # Create a minimal registry file
        registry = [{"name": "dan", "email": "dan@test.com"}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
        ) as f:
            json.dump(registry, f)
            registry_path = f.name

        # Create provider that returns a known token
        provider = GraphTokenProvider(TENANT_ID, CLIENT_ID, CLIENT_SECRET)
        provider._acquire_token_sync = SyncMock(return_value="refreshed-token")

        # Create TeamsClient with the provider
        mock_http = AsyncMock()
        client = TeamsClient(
            http_client=mock_http,
            registry_path=registry_path,
            access_token="initial-token",
            token_provider=provider,
        )

        # Simulate refresh
        new_token = await client.refresh_access_token()
        assert new_token == "refreshed-token"
        assert client._access_token == "refreshed-token"
