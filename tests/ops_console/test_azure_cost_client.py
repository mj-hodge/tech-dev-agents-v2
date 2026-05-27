"""Tests for AzureCostClient — T28-T35 (STORY-227: managed identity migration)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tech_dev_agents.ops_console.models.responses import DailyCost
from tech_dev_agents.ops_console.services.azure_cost_client import AzureCostClient


class _FakeAccessToken:
    """Mimics azure.core.credentials.AccessToken."""

    def __init__(self, token: str, expires_on: int = 9999999999):
        self.token = token
        self.expires_on = expires_on


def _make_credential(token: str = "test-token-abc") -> MagicMock:
    """Create a mock TokenCredential that returns a fixed token."""
    cred = MagicMock()
    cred.get_token.return_value = _FakeAccessToken(token)
    return cred


def _make_cost_response(rows=None):
    if rows is None:
        rows = [
            [12.50, 20260401, "rg-agent-dan"],
            [8.00, 20260401, "rg-agent-derrick"],
        ]
    return httpx.Response(200, json={"properties": {"rows": rows}})


class TestAzureCostClient:
    """T28-T35: Azure Cost Management REST client with credential injection."""

    @pytest.mark.asyncio
    async def test_get_token_delegates_to_credential(self):
        """T28: _get_token calls credential.get_token with management scope."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        credential = _make_credential("my-token")

        client = AzureCostClient(
            credential=credential,
            subscription_id="test-sub",
            http_client=mock_http,
        )
        token = await client._get_token()

        assert token == "my-token"
        credential.get_token.assert_called_once_with("https://management.azure.com/.default")

    @pytest.mark.asyncio
    async def test_get_token_returns_token_string(self):
        """T29: _get_token returns the .token attribute from AccessToken."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        credential = _make_credential("token-value-xyz")

        client = AzureCostClient(
            credential=credential,
            subscription_id="test-sub",
            http_client=mock_http,
        )
        result = await client._get_token()

        assert result == "token-value-xyz"
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_get_daily_costs_maps_resource_groups(self):
        """T30: get_daily_costs maps resource group 'rg-agent-dan' to agent name 'dan'."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = _make_cost_response()
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
            agent_map={"rg-agent-dan": "dan", "rg-agent-derrick": "derrick"},
        )
        result = await client.get_daily_costs("2026-04-01", "2026-04-01")

        assert "dan" in result
        assert "derrick" in result
        assert result["dan"][0].azure_cost_usd == 12.50
        assert result["derrick"][0].azure_cost_usd == 8.00

    @pytest.mark.asyncio
    async def test_get_daily_costs_handles_empty_response(self):
        """T31: get_daily_costs with no cost data returns empty dict."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = _make_cost_response(rows=[])
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
        )
        result = await client.get_daily_costs("2026-04-01", "2026-04-01")

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_agent_daily_costs_filters_single_agent(self):
        """T32: get_agent_daily_costs returns only DailyCost entries for the requested agent."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = _make_cost_response()
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
            agent_map={"rg-agent-dan": "dan", "rg-agent-derrick": "derrick"},
        )
        result = await client.get_agent_daily_costs("dan", "2026-04-01", "2026-04-01")

        assert len(result) == 1
        assert result[0].azure_cost_usd == 12.50

    def test_constructor_accepts_credential_not_secrets(self):
        """T33: AzureCostClient accepts credential kwarg, not tenant/client/secret."""
        mock_http = MagicMock(spec=httpx.AsyncClient)
        credential = _make_credential()

        # Should succeed with credential kwarg
        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
        )
        assert client._credential is credential
        assert client._subscription_id == "sub"

        # Should NOT accept old-style kwargs
        with pytest.raises(TypeError):
            AzureCostClient(
                tenant_id="t",
                client_id="c",
                client_secret="s",
                subscription_id="sub",
                http_client=mock_http,
            )

    @pytest.mark.asyncio
    async def test_credential_failure_raises_azure_cost_error(self):
        """T34: When credential.get_token raises, AzureCostClient surfaces the error."""
        from tech_dev_agents.ops_console.services.azure_cost_client import AzureCostError

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        credential = MagicMock()
        credential.get_token.side_effect = Exception("No credential could be found")

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
        )

        with pytest.raises(Exception, match="No credential could be found"):
            await client._get_token()

    @pytest.mark.asyncio
    async def test_get_token_uses_management_scope(self):
        """T35: _get_token passes the Azure Management scope string."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
        )
        await client._get_token()

        args, _ = credential.get_token.call_args
        assert args[0] == "https://management.azure.com/.default"


class TestCostClassification:
    """Tests for Foundry vs OpenAI cost classification in _parse_cost_response."""

    def test_classify_foundry_resource_group(self):
        """Non-oai resource groups classify as Foundry cost."""
        foundry, openai = AzureCostClient._classify_cost("rg-agent-dan", 12.50)
        assert foundry == 12.50
        assert openai == 0.0

    def test_classify_openai_resource_group(self):
        """Resource groups starting with 'oai-' classify as OpenAI cost."""
        foundry, openai = AzureCostClient._classify_cost("oai-eastus-gpt4", 8.00)
        assert foundry == 0.0
        assert openai == 8.00

    def test_classify_moret_resource_group_as_foundry(self):
        """Resource groups like 'moret-*' classify as Foundry (not OpenAI)."""
        foundry, openai = AzureCostClient._classify_cost("moret-agent-prod", 5.25)
        assert foundry == 5.25
        assert openai == 0.0

    @pytest.mark.asyncio
    async def test_parse_cost_response_populates_foundry_and_openai_fields(self):
        """_parse_cost_response sets foundry_cost_usd and openai_cost_usd on DailyCost."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
            agent_map={"rg-agent-dan": "dan"},
        )

        data = {"properties": {"rows": [[12.50, 20260401, "rg-agent-dan"]]}}
        result = client._parse_cost_response(data)

        assert "dan" in result
        daily = result["dan"][0]
        assert daily.azure_cost_usd == 12.50
        assert daily.foundry_cost_usd == 12.50
        assert daily.openai_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_parse_cost_response_openai_resource_group(self):
        """OpenAI resource group puts cost in openai_cost_usd, not foundry_cost_usd."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
            agent_map={"oai-eastus-gpt4": "dan"},
        )

        data = {"properties": {"rows": [[8.00, 20260401, "oai-eastus-gpt4"]]}}
        result = client._parse_cost_response(data)

        assert "dan" in result
        daily = result["dan"][0]
        assert daily.azure_cost_usd == 8.00
        assert daily.foundry_cost_usd == 0.0
        assert daily.openai_cost_usd == 8.00
