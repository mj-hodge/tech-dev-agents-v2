"""Client for Azure Cost Management REST API.

STORY-227: Uses azure.core TokenCredential injection instead of manual OAuth2.
DefaultAzureCredential auto-discovers managed identity on Azure VMs,
falls back to AzureCliCredential for local dev.

STORY-627: Added health_check() for startup probe + auth-failure surfacing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from azure.core.credentials import TokenCredential

from tech_dev_agents.ops_console.models.responses import DailyCost

logger = logging.getLogger(__name__)

_MAX_ERROR_LEN = 200


def _sanitize_error(exc: BaseException, secrets: list[str | None]) -> str:
    """Return a sanitized, length-capped error string safe for API responses and logs.

    STORY-721: Prevents credential leaks in health_check() result["error"].

    - Replaces each non-empty string secret with '<REDACTED>'
    - None and empty-string entries in secrets are skipped (no crash)
    - Truncates to _MAX_ERROR_LEN characters with '…' suffix when exceeded
    """
    msg = str(exc)
    for s in (secrets or []):
        if s and isinstance(s, str):
            msg = msg.replace(s, "<REDACTED>")
    if len(msg) > _MAX_ERROR_LEN:
        msg = msg[: _MAX_ERROR_LEN - 1] + "…"
    return msg


class AzureCostError(Exception):
    """Raised on Azure Cost API failures."""


class AzureCostClient:
    """Client for Azure Cost Management REST API.

    Accepts any azure.core TokenCredential (DefaultAzureCredential,
    ManagedIdentityCredential, ClientSecretCredential, etc.).
    """

    _SCOPE = "https://management.azure.com/.default"

    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
        http_client: httpx.AsyncClient,
        agent_map: dict[str, str] | None = None,
    ):
        self._credential = credential
        self._subscription_id = subscription_id
        self._http = http_client
        self._agent_map = agent_map or {}

    async def _get_token(self) -> str:
        """Get access token from the injected credential.

        azure-identity handles caching, refresh, and retry internally.
        """
        token = self._credential.get_token(self._SCOPE)
        return token.token

    async def health_check(self) -> dict[str, Any]:
        """Probe Azure Cost Management auth + query reachability.

        STORY-627: Returns a structured dict — never raises.
        Used by /api/health to surface cost_mgmt_reachable status.

        Returns:
            {"ok": bool, "auth": bool, "query": bool, "error": str | None}
        """
        result: dict[str, Any] = {"ok": False, "auth": False, "query": False, "error": None}

        # Collect credential secrets for sanitization.
        # ClientSecretCredential stores the raw client secret as _client_credential.
        # DefaultAzureCredential / ManagedIdentityCredential return None here — safe to skip.
        _secrets: list[str | None] = [getattr(self._credential, "_client_credential", None)]

        # Step 1: Try to get a token (auth check)
        try:
            token = await self._get_token()
        except Exception as exc:
            error_msg = _sanitize_error(exc, _secrets)
            logger.warning("Azure Cost Management auth failed: %s", error_msg)
            result["error"] = error_msg
            return result

        result["auth"] = True

        # Step 2: Try a minimal Cost Management query (today, no grouping)
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            url = (
                f"https://management.azure.com/subscriptions/{self._subscription_id}"
                "/providers/Microsoft.CostManagement/query"
                "?api-version=2023-03-01"
            )
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            body = {
                "type": "ActualCost",
                "timeframe": "Custom",
                "timePeriod": {"from": today, "to": today},
                "dataset": {
                    "granularity": "Daily",
                    "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                },
            }
            resp = await self._http.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                error_msg = f"Cost query failed: {resp.status_code}"
                logger.warning("Azure Cost Management query failed: %s", error_msg)
                result["error"] = error_msg
                return result
        except Exception as exc:
            error_msg = _sanitize_error(exc, _secrets)
            logger.warning("Azure Cost Management query error: %s", error_msg)
            result["error"] = error_msg
            return result

        result["query"] = True
        result["ok"] = True
        return result

    async def get_daily_costs(
        self,
        start_date: str,
        end_date: str,
    ) -> dict[str, list[DailyCost]]:
        """Query daily costs grouped by resource group.

        Maps resource group names to agent names via config.
        Returns: {agent_name: [DailyCost, ...]}
        """
        token = await self._get_token()
        url = (
            f"https://management.azure.com/subscriptions/{self._subscription_id}"
            "/providers/Microsoft.CostManagement/query"
            "?api-version=2023-03-01"
        )
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start_date, "to": end_date},
            "dataset": {
                "granularity": "Daily",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "grouping": [{"type": "Dimension", "name": "ResourceGroup"}],
            },
        }
        resp = await self._http.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise AzureCostError(f"Cost query failed: {resp.status_code} {resp.text}")

        return self._parse_cost_response(resp.json())

    async def get_agent_daily_costs(
        self,
        agent_name: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyCost]:
        """Get daily costs for a single agent (filters from get_daily_costs)."""
        all_costs = await self.get_daily_costs(start_date, end_date)
        return all_costs.get(agent_name, [])

    # Resource group prefixes for classifying Azure costs.
    # "oai-" = Azure OpenAI Service; everything else = Azure AI Foundry.
    _OPENAI_RG_PREFIXES = ("oai-",)

    @classmethod
    def _classify_cost(cls, rg_name: str, cost_value: float) -> tuple[float, float]:
        """Classify a cost row into (foundry_cost, openai_cost) by resource group name.

        Heuristic: resource groups starting with ``oai-`` are Azure OpenAI;
        all other resource groups (the vast majority of the fleet) are
        Azure AI Foundry.  Returns ``(foundry, openai)`` tuple.
        """
        if rg_name.startswith(cls._OPENAI_RG_PREFIXES):
            return 0.0, cost_value
        return cost_value, 0.0

    def _parse_cost_response(self, data: dict[str, Any]) -> dict[str, list[DailyCost]]:
        """Parse Azure Cost Management API response into agent-keyed DailyCost lists."""
        result: dict[str, list[DailyCost]] = {}
        rows = data.get("properties", {}).get("rows", [])

        for row in rows:
            if len(row) < 3:
                continue
            cost_value = float(row[0])
            date_num = str(row[1])  # YYYYMMDD integer format
            rg_name = str(row[2]).lower()

            agent_name = self._agent_map.get(rg_name)
            if not agent_name:
                continue

            foundry_cost, openai_cost = self._classify_cost(rg_name, cost_value)

            date_str = f"{date_num[:4]}-{date_num[4:6]}-{date_num[6:8]}"
            daily = DailyCost(
                date=date_str,
                sdk_cost_usd=0.0,
                azure_cost_usd=cost_value,
                foundry_cost_usd=foundry_cost,
                openai_cost_usd=openai_cost,
                total_cost_usd=cost_value,
                sdk_sessions=0,
                sdk_turns=0,
            )
            result.setdefault(agent_name, []).append(daily)

        return result
