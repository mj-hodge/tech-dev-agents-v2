"""Tests for CostService — T09-T14."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tech_dev_agents.ops_console.models.responses import CostToday, DailyCost
from tech_dev_agents.ops_console.services.cost_service import CostService


class TestCostServiceToday:
    """T09-T11: Today's cost calculation."""

    @pytest.mark.asyncio
    async def test_today_cost_sdk_only(self):
        """T09: get_today_cost with Loki data but no Azure returns SDK cost with azure_cost_usd=0."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"cost": 10.25, "sessions": 8, "turns": 142}
        ]

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_today_cost("dan")

        assert isinstance(result, CostToday)
        assert result.sdk_cost_usd == 10.25
        assert result.azure_cost_usd == 0.0
        assert result.total_cost_usd == 10.25
        assert result.sdk_sessions == 8
        assert result.sdk_turns == 142

    @pytest.mark.asyncio
    async def test_today_cost_combined(self):
        """T10: get_today_cost with both Loki and Azure data returns combined total."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"cost": 10.25, "sessions": 8, "turns": 142}
        ]

        mock_azure = AsyncMock()
        mock_azure.get_agent_daily_costs.return_value = [
            DailyCost(
                date="2026-04-01",
                sdk_cost_usd=0.0,
                azure_cost_usd=2.22,
                total_cost_usd=2.22,
                sdk_sessions=0,
                sdk_turns=0,
            )
        ]

        service = CostService(loki=mock_loki, azure=mock_azure)
        result = await service.get_today_cost("dan")

        assert result.sdk_cost_usd == 10.25
        assert result.azure_cost_usd == 2.22
        assert result.total_cost_usd == 12.47

    @pytest.mark.asyncio
    async def test_today_cost_loki_error_degrades_gracefully(self):
        """T11: get_today_cost when Loki is unreachable returns zero costs (not exception)."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.side_effect = Exception("Connection refused")

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_today_cost("dan")

        assert result.sdk_cost_usd == 0.0
        assert result.azure_cost_usd == 0.0
        assert result.total_cost_usd == 0.0


class TestCostServiceBreakdown:
    """T12-T14: Historical cost breakdown."""

    @pytest.mark.asyncio
    async def test_cost_breakdown_daily_granularity(self):
        """T12: get_cost_breakdown with days=7, granularity='daily' returns 7 DailyCost entries."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = []

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_cost_breakdown("dan", days=7, granularity="daily")

        assert len(result.daily) == 7
        assert result.granularity == "daily"
        assert result.agent_name == "dan"

    @pytest.mark.asyncio
    async def test_cost_breakdown_weekly_granularity(self):
        """T13: get_cost_breakdown with days=14, granularity='weekly' returns 2 aggregated entries."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = []

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_cost_breakdown("dan", days=14, granularity="weekly")

        assert len(result.daily) == 2
        assert result.granularity == "weekly"

    @pytest.mark.asyncio
    async def test_cost_breakdown_azure_disabled(self):
        """T14: get_cost_breakdown with azure=None returns SDK-only costs with azure_cost_usd=0."""
        mock_loki = AsyncMock()
        mock_loki.query_cost_summaries.return_value = [
            {"date": "2026-04-01", "cost": 5.0, "sessions": 3, "turns": 50}
        ]

        service = CostService(loki=mock_loki, azure=None)
        result = await service.get_cost_breakdown("dan", days=7, granularity="daily")

        for entry in result.daily:
            assert entry.azure_cost_usd == 0.0
        assert result.data_freshness.azure_as_of is None
