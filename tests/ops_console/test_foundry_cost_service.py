"""Tests for FoundryCostService — STORY-576.

T01-T09: ResourceId classification, DB read/write, output variance.
All tests are RED until Phase 8 implements foundry_cost_service.py.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from tech_dev_agents.ops_console.services.foundry_cost_service import (
    FoundryCostService,
    FoundryCostResponse,
    classify_resource_id,
)


# ---------------------------------------------------------------------------
# T01-T05: classify_resource_id — pure function tests
# ---------------------------------------------------------------------------


class TestClassifyResourceId:
    """T01-T05: Classify Azure ResourceId strings into model buckets."""

    def test_classify_opus_resource_id(self):
        """T01: ResourceId containing 'claude-opus' returns 'opus'."""
        rid = (
            "/subscriptions/abc-123/resourceGroups/rg-foundry-ops"
            "/providers/Microsoft.MachineLearningServices"
            "/workspaces/foundry-prod/deployments/claude-opus-4-6-20260401"
        )
        assert classify_resource_id(rid) == "opus"

    def test_classify_sonnet_resource_id(self):
        """T02: ResourceId containing 'claude-sonnet' returns 'sonnet'."""
        rid = (
            "/subscriptions/abc-123/resourceGroups/rg-foundry-ops"
            "/providers/Microsoft.MachineLearningServices"
            "/workspaces/foundry-prod/deployments/claude-sonnet-4-5-20260415"
        )
        assert classify_resource_id(rid) == "sonnet"

    def test_classify_haiku_resource_id(self):
        """T03: ResourceId containing 'claude-haiku' returns 'haiku'."""
        rid = (
            "/subscriptions/abc-123/resourceGroups/rg-foundry-ops"
            "/providers/Microsoft.MachineLearningServices"
            "/workspaces/foundry-prod/deployments/claude-haiku-3-5-20260401"
        )
        assert classify_resource_id(rid) == "haiku"

    def test_classify_unknown_resource_id(self):
        """T04: ResourceId not matching any model pattern returns 'other'."""
        rid = (
            "/subscriptions/abc-123/resourceGroups/rg-foundry-ops"
            "/providers/Microsoft.CognitiveServices"
            "/accounts/some-other-model-deployment"
        )
        assert classify_resource_id(rid) == "other"

    def test_classify_case_insensitive(self):
        """T05: Classification is case-insensitive."""
        rid = (
            "/subscriptions/abc-123/resourceGroups/rg-foundry-ops"
            "/providers/Microsoft.MachineLearningServices"
            "/workspaces/foundry-prod/deployments/Claude-OPUS-4-6-LATEST"
        )
        assert classify_resource_id(rid) == "opus"


# ---------------------------------------------------------------------------
# Helpers for mock DB pool
# ---------------------------------------------------------------------------


def _make_mock_pool(rows: list[dict]) -> MagicMock:
    """Create a mock asyncpg pool that returns given rows from fetch().

    asyncpg pool.acquire() returns an async context manager. We simulate this
    with a MagicMock that supports ``async with pool.acquire() as conn:``.
    """
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = rows

    # Create an async context manager for pool.acquire()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value = acm

    return mock_pool


def _make_db_row(
    usage_date: date,
    opus: float,
    sonnet: float,
    haiku: float,
    other: float,
    fetched_at: datetime | None = None,
) -> dict:
    """Create a dict mimicking an asyncpg Record for foundry_cost_daily."""
    return {
        "usage_date": usage_date,
        "opus_usd": Decimal(str(opus)),
        "sonnet_usd": Decimal(str(sonnet)),
        "haiku_usd": Decimal(str(haiku)),
        "other_usd": Decimal(str(other)),
        "fetched_at": fetched_at or datetime(2026, 4, 24, 14, 0, 0, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# T06-T07: get_daily_by_model — DB read path
# ---------------------------------------------------------------------------


class TestGetDailyByModel:
    """T06-T07: Read cached cost data from Postgres."""

    @pytest.mark.asyncio
    async def test_returns_cached_rows(self):
        """T06: get_daily_by_model reads from DB and returns FoundryCostResponse."""
        rows = [
            _make_db_row(date(2026, 4, 23), opus=100.0, sonnet=20.0, haiku=5.0, other=1.0),
            _make_db_row(date(2026, 4, 24), opus=80.0, sonnet=15.0, haiku=3.0, other=0.5),
        ]
        pool = _make_mock_pool(rows)
        service = FoundryCostService(db_pool=pool)

        result = await service.get_daily_by_model(days=7)

        assert isinstance(result, FoundryCostResponse)
        assert len(result.daily) == 2
        assert result.daily[0].date == "2026-04-23"
        assert result.daily[0].opus_usd == 100.0
        assert result.daily[0].sonnet_usd == 20.0
        assert result.daily[0].haiku_usd == 5.0
        assert result.daily[0].other_usd == 1.0
        assert result.daily[0].total_usd == 126.0  # 100 + 20 + 5 + 1
        assert result.daily[1].total_usd == 98.5   # 80 + 15 + 3 + 0.5
        assert result.fetched_at is not None
        assert result.cache_age_seconds >= 0

    @pytest.mark.asyncio
    async def test_empty_cache_returns_empty_response(self):
        """T07: get_daily_by_model with no rows returns empty daily list."""
        pool = _make_mock_pool([])
        service = FoundryCostService(db_pool=pool)

        result = await service.get_daily_by_model(days=7)

        assert result.daily == []
        assert result.fetched_at is None
        assert result.cache_age_seconds == 0


# ---------------------------------------------------------------------------
# T08: upsert_daily_costs — DB write path
# ---------------------------------------------------------------------------


class TestUpsertDailyCosts:
    """T08: Write classified cost rows to Postgres."""

    @pytest.mark.asyncio
    async def test_upsert_calls_execute_for_each_row(self):
        """T08: upsert_daily_costs executes UPSERT for each row."""
        mock_conn = AsyncMock()

        rows = [
            {
                "usage_date": date(2026, 4, 23),
                "opus_usd": 100.0,
                "sonnet_usd": 20.0,
                "haiku_usd": 5.0,
                "other_usd": 1.0,
            },
            {
                "usage_date": date(2026, 4, 24),
                "opus_usd": 80.0,
                "sonnet_usd": 15.0,
                "haiku_usd": 3.0,
                "other_usd": 0.5,
            },
        ]

        count = await FoundryCostService.upsert_daily_costs(mock_conn, rows)

        assert count == 2
        assert mock_conn.execute.call_count == 2
        # Verify first call includes correct date
        first_call_args = mock_conn.execute.call_args_list[0]
        assert first_call_args[0][1] == date(2026, 4, 23)  # usage_date
        assert first_call_args[0][2] == 100.0               # opus_usd


# ---------------------------------------------------------------------------
# T09: Output-Variance Gate
# ---------------------------------------------------------------------------


class TestOutputVariance:
    """T09: Verify output changes with different input (stub detection)."""

    @pytest.mark.asyncio
    async def test_get_daily_by_model_output_varies_with_data(self):
        """T09: Different DB rows produce different response totals."""
        # Fixture A: high-cost Opus day
        rows_a = [
            _make_db_row(date(2026, 4, 23), opus=500.0, sonnet=50.0, haiku=10.0, other=5.0),
        ]
        # Fixture B: low-cost mixed day
        rows_b = [
            _make_db_row(date(2026, 4, 23), opus=10.0, sonnet=80.0, haiku=30.0, other=2.0),
        ]

        service_a = FoundryCostService(db_pool=_make_mock_pool(rows_a))
        service_b = FoundryCostService(db_pool=_make_mock_pool(rows_b))

        result_a = await service_a.get_daily_by_model(days=7)
        result_b = await service_b.get_daily_by_model(days=7)

        # Outputs must differ — a stub returning hardcoded data would fail this
        assert result_a.daily[0].opus_usd != result_b.daily[0].opus_usd
        assert result_a.daily[0].total_usd != result_b.daily[0].total_usd
        assert result_a.daily[0].opus_usd == 500.0
        assert result_b.daily[0].opus_usd == 10.0
