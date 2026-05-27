"""Tests for CostService caching behavior — T320-01 through T320-05.

STORY-320: Verify that get_today_cost() per-agent cache (300s TTL)
prevents redundant Azure Cost Management API calls on /api/fleet.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tech_dev_agents.ops_console.models.responses import DailyCost
from tech_dev_agents.ops_console.services.cost_service import CostService


def _make_mock_loki(cost: float = 10.25, sessions: int = 8, turns: int = 142) -> AsyncMock:
    """Build a mock LokiClient returning a single cost summary."""
    mock = AsyncMock()
    mock.query_cost_summaries.return_value = [
        {"cost": cost, "sessions": sessions, "turns": turns}
    ]
    return mock


def _make_mock_azure(azure_cost: float = 2.22, foundry_cost: float = 2.22, openai_cost: float = 0.0) -> AsyncMock:
    """Build a mock AzureCostClient returning a single daily cost row."""
    mock = AsyncMock()
    mock.get_agent_daily_costs.return_value = [
        DailyCost(
            date="2026-04-16",
            sdk_cost_usd=0.0,
            azure_cost_usd=azure_cost,
            foundry_cost_usd=foundry_cost,
            openai_cost_usd=openai_cost,
            total_cost_usd=azure_cost,
            sdk_sessions=0,
            sdk_turns=0,
        )
    ]
    return mock


class TestCostServiceCache:
    """T320-01 through T320-05: get_today_cost caching behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_azure_call(self):
        """T320-01: Second call returns cached data; Azure called only once."""
        loki = _make_mock_loki()
        azure = _make_mock_azure()
        service = CostService(loki=loki, azure=azure, cost_cache_ttl=300)

        first = await service.get_today_cost("dan")
        second = await service.get_today_cost("dan")

        assert first is second  # exact same object from cache
        assert azure.get_agent_daily_costs.call_count == 1
        assert loki.query_cost_summaries.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_miss_calls_azure(self):
        """T320-02: First call (cold cache) hits both Loki and Azure."""
        loki = _make_mock_loki()
        azure = _make_mock_azure()
        service = CostService(loki=loki, azure=azure, cost_cache_ttl=300)

        result = await service.get_today_cost("dan")

        loki.query_cost_summaries.assert_awaited_once()
        azure.get_agent_daily_costs.assert_awaited_once()
        assert result.sdk_cost_usd == 10.25
        assert result.azure_cost_usd == 2.22
        assert result.total_cost_usd == 12.47

    @pytest.mark.asyncio
    async def test_ttl_expiry_triggers_refresh(self):
        """T320-03: After 300s TTL expires, next call re-fetches from Azure."""
        loki = _make_mock_loki()
        azure = _make_mock_azure()
        service = CostService(loki=loki, azure=azure, cost_cache_ttl=300)

        # Populate cache
        with patch("tech_dev_agents.ops_console.cache.time.time", return_value=1000.0):
            first = await service.get_today_cost("dan")

        assert azure.get_agent_daily_costs.call_count == 1

        # Still within TTL — should hit cache
        with patch("tech_dev_agents.ops_console.cache.time.time", return_value=1299.0):
            cached = await service.get_today_cost("dan")
        assert cached is first
        assert azure.get_agent_daily_costs.call_count == 1

        # Past TTL — should re-fetch
        with patch("tech_dev_agents.ops_console.cache.time.time", return_value=1301.0):
            refreshed = await service.get_today_cost("dan")
        assert azure.get_agent_daily_costs.call_count == 2
        assert refreshed.sdk_cost_usd == 10.25  # same mock data

    @pytest.mark.asyncio
    async def test_different_agents_independent_cache(self):
        """T320-04: Each agent has its own cache entry."""
        loki = _make_mock_loki()
        azure = _make_mock_azure()
        service = CostService(loki=loki, azure=azure, cost_cache_ttl=300)

        dan_result = await service.get_today_cost("dan")
        derrick_result = await service.get_today_cost("derrick")

        # Both agents triggered Azure calls
        assert azure.get_agent_daily_costs.call_count == 2
        assert loki.query_cost_summaries.call_count == 2

        # Subsequent calls hit cache (no additional Azure calls)
        await service.get_today_cost("dan")
        await service.get_today_cost("derrick")
        assert azure.get_agent_daily_costs.call_count == 2

    @pytest.mark.asyncio
    async def test_fleet_daily_spend_uses_cached_per_agent_costs(self):
        """T320-05: get_fleet_daily_spend reuses warm per-agent cache."""
        loki = _make_mock_loki()
        azure = _make_mock_azure(azure_cost=3.00, foundry_cost=3.00)
        service = CostService(loki=loki, azure=azure, cost_cache_ttl=300, fleet_cache_ttl=60)

        # Warm per-agent cache
        await service.get_today_cost("dan")
        await service.get_today_cost("derrick")
        assert azure.get_agent_daily_costs.call_count == 2

        # Fleet spend should reuse cached per-agent data
        total = await service.get_fleet_daily_spend(["dan", "derrick"])
        assert azure.get_agent_daily_costs.call_count == 2  # no new Azure calls
        assert total == 6.0  # 3.00 * 2 agents
