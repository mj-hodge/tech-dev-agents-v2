"""STORY-736: Tests for cost_status field on CostToday — replaces silent-zero fallback.

SC-6: cost_service.get_today_cost() no longer silently returns foundry_cost_usd=0 on
      Cost Management exception — it sets cost_status="unavailable".
SC-4: Fleet overview shows total Foundry spend when at least one agent has real usage.

Groups:
  A — CostToday.foundry_cost_status values for each Azure state
  B — Output-variance and regression checks
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tech_dev_agents.ops_console.models.responses import CostToday, DailyCost
from tech_dev_agents.ops_console.services.cost_service import CostService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_loki(cost: float = 5.0) -> AsyncMock:
    """Return a Loki mock with predictable SDK cost."""
    mock = AsyncMock()
    mock.query_cost_summaries.return_value = [
        {"cost": cost, "sessions": 2, "turns": 30}
    ]
    return mock


def _mock_azure_real(foundry_usd: float = 3.50) -> AsyncMock:
    """Return an Azure mock returning one DailyCost entry with real Foundry cost."""
    mock = AsyncMock()
    mock.get_agent_daily_costs.return_value = [
        DailyCost(
            date="2026-04-27",
            sdk_cost_usd=0.0,
            azure_cost_usd=foundry_usd,
            foundry_cost_usd=foundry_usd,
            openai_cost_usd=0.0,
            total_cost_usd=foundry_usd,
            sdk_sessions=0,
            sdk_turns=0,
        )
    ]
    return mock


def _mock_azure_empty() -> AsyncMock:
    """Return an Azure mock returning an empty list (no rows for this agent/date)."""
    mock = AsyncMock()
    mock.get_agent_daily_costs.return_value = []
    return mock


def _mock_azure_raises() -> AsyncMock:
    """Return an Azure mock that raises on every call (simulates CM outage)."""
    mock = AsyncMock()
    mock.get_agent_daily_costs.side_effect = Exception("Connection timeout to management.azure.com")
    return mock


# ---------------------------------------------------------------------------
# Group A — foundry_cost_status per Azure state
# ---------------------------------------------------------------------------


class TestCostStatusField:
    """A01–A05: CostToday.foundry_cost_status populated correctly by get_today_cost."""

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_none_returns_status_unavailable(self):
        """A01: When azure=None (Cost Management not configured), status is 'unavailable'.

        SC-6: This is the core bug fix — ops dashboard should show "—" not "$0.00"
        when no AzureCostClient has been wired up at startup.
        """
        service = CostService(loki=_mock_loki(), azure=None)
        result = await service.get_today_cost("dan")

        # Phase 7 RED: cost_service does not yet set foundry_cost_status
        assert result.foundry_cost_status == "unavailable"

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_exception_returns_status_unavailable(self):
        """A02: When the Azure Cost Management call raises, status is 'unavailable'.

        SC-6: The except block at cost_service.py:90 currently logs+ignores,
        leaving foundry_cost_status=None. Must become 'unavailable'.
        """
        service = CostService(loki=_mock_loki(), azure=_mock_azure_raises())
        result = await service.get_today_cost("dan")

        # Phase 7 RED: foundry_cost_status still None until Phase 8 sets it
        assert result.foundry_cost_status == "unavailable"

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_empty_returns_status_no_usage(self):
        """A03: When Azure returns [] (no rows for this agent/date) early in the day,
        status is 'no_usage'.

        Distinguishes "Cost Management is down" from "agent simply had no Foundry
        spend today". Before 08:00 UTC, empty data is treated as genuine no-usage
        rather than Azure reporting lag.
        """
        # Pin time to 05:00 UTC (before stale threshold of 08:00)
        early = datetime(2026, 4, 27, 5, 0, 0, tzinfo=timezone.utc)
        with patch("tech_dev_agents.ops_console.services.cost_service.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = early
            service = CostService(loki=_mock_loki(), azure=_mock_azure_empty())
            result = await service.get_today_cost("dan")

        assert result.foundry_cost_status == "no_usage"

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_empty_late_day_returns_status_stale(self):
        """A06: When Azure returns [] late in the day (>= 08:00 UTC), status is 'stale'.

        SC-7: Azure Cost Management has a 24-48h reporting lag. If it's past 08:00 UTC
        and today still shows $0, the data is likely stale rather than genuinely zero.
        """
        # Pin time to 14:00 UTC (well past stale threshold)
        late = datetime(2026, 4, 27, 14, 0, 0, tzinfo=timezone.utc)
        with patch("tech_dev_agents.ops_console.services.cost_service.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = late
            service = CostService(loki=_mock_loki(), azure=_mock_azure_empty())
            result = await service.get_today_cost("dan")

        assert result.foundry_cost_status == "stale"

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_real_cost_returns_status_ok(self):
        """A04: When Azure returns real Foundry cost > 0, status is 'ok'.

        SC-4: Happy path — Cost Management is healthy and billing data is present.
        """
        service = CostService(loki=_mock_loki(), azure=_mock_azure_real(foundry_usd=3.50))
        result = await service.get_today_cost("dan")

        # Phase 7 RED: foundry_cost_status still None
        assert result.foundry_cost_status == "ok"

    @pytest.mark.asyncio
    async def test_get_today_cost_status_output_varies_with_azure_config(self):
        """A05: Output-variance — two different Azure configs → two different statuses.

        Detects stub implementations that always return the same cost_status regardless
        of input (e.g. hardcoded None).
        """
        # Case 1: Azure not configured
        svc_none = CostService(loki=_mock_loki(), azure=None)
        result_none = await svc_none.get_today_cost("dan")

        # Case 2: Azure configured with real cost
        svc_real = CostService(loki=_mock_loki(), azure=_mock_azure_real(foundry_usd=2.0))
        result_real = await svc_real.get_today_cost("dan")

        # Phase 7 RED: both return None currently; must differ after Phase 8
        assert result_none.foundry_cost_status != result_real.foundry_cost_status


# ---------------------------------------------------------------------------
# Group B — regression and model-existence checks
# ---------------------------------------------------------------------------


class TestCostStatusRegression:
    """B01–B03: Regression guards — existing behavior must not break."""

    def test_cost_today_model_has_foundry_cost_status_field(self):
        """B01: CostToday model has foundry_cost_status attribute (scaffolding check).

        Verifies the model field was added to responses.py. This test PASSes
        immediately after the model is updated — it gates all downstream tests.
        """
        obj = CostToday(
            sdk_cost_usd=5.0,
            azure_cost_usd=0.0,
            total_cost_usd=5.0,
            sdk_sessions=1,
            sdk_turns=10,
        )
        assert hasattr(obj, "foundry_cost_status")

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_exception_preserves_foundry_cost_zero(self):
        """B02: When Azure raises, foundry_cost_usd is still 0.0 (regression guard).

        The fix adds cost_status but must NOT change the numeric value — the UI
        uses cost_status to decide how to render the number, not the number itself.
        """
        service = CostService(loki=_mock_loki(), azure=_mock_azure_raises())
        result = await service.get_today_cost("dan")

        # Should still be 0.0 (regression — only status changes)
        assert result.foundry_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_get_today_cost_azure_none_cost_status_is_not_none(self):
        """B03: When azure=None, foundry_cost_status is NOT None.

        SC-6: The silent-zero fallback bug manifests as None status. Phase 8
        must set a concrete string value so frontend can branch on it.
        """
        service = CostService(loki=_mock_loki(), azure=None)
        result = await service.get_today_cost("dan")

        # Phase 7 RED: currently returns None
        assert result.foundry_cost_status is not None
