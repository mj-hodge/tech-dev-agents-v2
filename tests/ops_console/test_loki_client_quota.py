"""Tests for LokiClient.query_agent_quota() — STORY-513 / STORY-543.

STORY-513 tests: query_agent_quota aggregates [USAGE] entries → QuotaInfo
STORY-543 tests: P90 token quota baseline derived from Loki history

AC-2: query_agent_quota aggregates [USAGE] entries → QuotaInfo(source="loki", ...)
AC-5: Empty Loki result → QuotaInfo(source="no_data")
STORY-543: Replace hardcoded 200K ceiling with P90 from historical blocks
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tech_dev_agents.ops_console.services.loki_client import (
    LokiClient,
    LokiError,
    LokiLogEntry,
    _compute_p90,
)
from tech_dev_agents.ops_console.models.responses import QuotaInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loki_response(streams=None, status: int = 200):
    """Build a mock Loki API response."""
    if streams is None:
        streams = []
    data = {"data": {"result": streams, "resultType": "streams"}}
    return httpx.Response(status, json=data)


def _make_usage_stream(agent: str = "dan", entries=None):
    """Build a mock Loki stream response with [USAGE] log lines.

    Args:
        agent: The agent label value for the stream.
        entries: List of (line, nanosecond_timestamp) tuples. Defaults to one
                 entry with total_tokens=50000 and cost_usd=0.54.
    """
    if entries is None:
        entries = [("[USAGE] total_tokens=50000 cost_usd=0.54", "1712000000000000000")]
    return {
        "stream": {"agent": agent},
        "values": [[ts, line] for line, ts in entries],
    }


def _make_empty_loki_response():
    """Build a Loki response with no streams (no data)."""
    return _make_loki_response(streams=[])


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestLokiClientQueryAgentQuota:
    """Tests for LokiClient.query_agent_quota(agent_name) — AC-2, AC-5."""

    @pytest.mark.asyncio
    async def test_query_agent_quota_with_usage_lines_returns_source_loki(self):
        """AC-2: Three [USAGE] entries with known totals → source="loki", sums correct."""
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        entries = [
            ("[USAGE] total_tokens=50000 cost_usd=0.54", "1712000000000000000"),
            ("[USAGE] total_tokens=80000 cost_usd=0.86", "1712000001000000000"),
            ("[USAGE] total_tokens=120000 cost_usd=1.29", "1712000002000000000"),
        ]
        mock_http.get.return_value = _make_loki_response([_make_usage_stream("dan", entries)])
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Act
        result = await client.query_agent_quota("dan")

        # Assert
        assert isinstance(result, QuotaInfo)
        assert result.source.value == "loki"
        assert result.current_block_tokens == 250000  # 50000+80000+120000
        assert result.sessions_in_block == 3

    @pytest.mark.asyncio
    async def test_query_agent_quota_empty_loki_returns_no_data(self):
        """AC-5: Empty Loki result → source="no_data", current_block_tokens is None."""
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_empty_loki_response()
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Act
        result = await client.query_agent_quota("dan")

        # Assert
        assert isinstance(result, QuotaInfo)
        assert result.source.value == "no_data"
        assert result.current_block_tokens is None

    @pytest.mark.asyncio
    async def test_query_agent_quota_output_varies_between_agents(self):
        """AC-6: Output varies by agent — separate calls return different data."""
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Dan has high token usage
        dan_entries = [
            ("[USAGE] total_tokens=400000 cost_usd=4.32", "1712000000000000000"),
        ]
        # Derrick has low token usage
        derrick_entries = [
            ("[USAGE] total_tokens=10000 cost_usd=0.11", "1712000000000000000"),
        ]

        def _side_effect(url, *, params, headers, **kwargs):
            # Inspect the query param to differentiate agents
            query = params.get("query", "")
            if "derrick" in query:
                return _make_loki_response([_make_usage_stream("derrick", derrick_entries)])
            return _make_loki_response([_make_usage_stream("dan", dan_entries)])

        mock_http.get.side_effect = _side_effect

        # Act
        dan_result = await client.query_agent_quota("dan")
        derrick_result = await client.query_agent_quota("derrick")

        # Assert — results differ
        assert dan_result.source.value == "loki"
        assert derrick_result.source.value == "loki"
        assert dan_result.current_block_tokens != derrick_result.current_block_tokens
        assert dan_result.current_block_tokens == 400000
        assert derrick_result.current_block_tokens == 10000

    @pytest.mark.asyncio
    async def test_query_agent_quota_multiple_entries_summed(self):
        """AC-2: Two [USAGE] entries → tokens summed correctly."""
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        entries = [
            ("[USAGE] total_tokens=30000 cost_usd=0.32", "1712000000000000000"),
            ("[USAGE] total_tokens=45000 cost_usd=0.48", "1712000001000000000"),
        ]
        mock_http.get.return_value = _make_loki_response([_make_usage_stream("dan", entries)])
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Act
        result = await client.query_agent_quota("dan")

        # Assert
        assert result.current_block_tokens == 75000  # 30000 + 45000

    @pytest.mark.asyncio
    async def test_query_agent_quota_loki_error_returns_no_data(self):
        """AC-5: LokiError from query_range → graceful degradation, source="no_data"."""
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(500, text="Internal Server Error")
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Act — should not raise
        result = await client.query_agent_quota("dan")

        # Assert — graceful degradation
        assert isinstance(result, QuotaInfo)
        assert result.source.value == "no_data"

    @pytest.mark.asyncio
    async def test_query_agent_quota_sanitizes_agent_name(self):
        """AC-2: Unsafe chars in agent name are stripped before embedding in LogQL."""
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_empty_loki_response()
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Act — name with LogQL-unsafe characters
        await client.query_agent_quota("dan{inject}")

        # Assert — the query param must not contain the injection payload
        assert mock_http.get.called
        call_kwargs = mock_http.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
        if not params and call_kwargs.kwargs:
            params = call_kwargs.kwargs.get("params", {})
        query_str = params.get("query", "")
        assert "{inject}" not in query_str, (
            f"Unsafe injection payload found in LogQL query: {query_str!r}"
        )
        assert "danjnject" in query_str or "dan" in query_str  # sanitized form retained

    @pytest.mark.asyncio
    async def test_query_agent_quota_queries_current_block(self):
        """AC-2: The time window passed to Loki must start at the current 5h block boundary.

        The query must NOT use a rolling 5-hour lookback — that bleeds previous-block
        tokens into the current block count. The start must be a block boundary hour
        (0, 5, 10, 15, or 20 UTC) and the window must be ≤ 5 hours.
        """
        # Arrange
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_empty_loki_response()
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # Act
        await client.query_agent_quota("dan")

        after_call = datetime.now(timezone.utc)

        # Assert — inspect start/end params passed to Loki
        assert mock_http.get.called
        call_kwargs = mock_http.get.call_args
        params = call_kwargs.kwargs.get("params", {})

        start_str = params.get("start", "")
        end_str = params.get("end", "")

        assert start_str, "Expected a 'start' param in the Loki request"
        assert end_str, "Expected an 'end' param in the Loki request"

        # Parse start and end — may be ISO strings or epoch nanoseconds
        def _parse_time(s: str) -> datetime:
            s = str(s)
            if s.isdigit() and len(s) > 13:
                return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        start_dt = _parse_time(start_str)
        end_dt = _parse_time(end_str)

        # Start must be a block boundary: minute=0, second=0, hour in {0,5,10,15,20}
        assert start_dt.minute == 0 and start_dt.second == 0, (
            f"Block start should be on an exact hour, got {start_dt}"
        )
        assert start_dt.hour in (0, 5, 10, 15, 20), (
            f"Block start hour must be 0/5/10/15/20, got {start_dt.hour}"
        )

        # Window must be ≤ 5 hours (never more than one block)
        window_hours = (end_dt - start_dt).total_seconds() / 3600
        assert window_hours <= 5.0, (
            f"Query window must not exceed 5h (one block), got {window_hours:.2f}h"
        )

        # End should be close to now
        assert abs((end_dt - after_call).total_seconds()) < 60, (
            f"End time {end_dt} is not close to now ({after_call})"
        )


# ---------------------------------------------------------------------------
# STORY-543: Helpers for historical block token data
# ---------------------------------------------------------------------------


def _make_historical_usage_entries(
    block_totals: list[int],
    agent: str = "dan",
    start_dt: datetime | None = None,
) -> list[tuple[str, str]]:
    """Generate [USAGE] log entries spread across multiple 5h blocks.

    Args:
        block_totals: List of token totals, one per block. Each block gets a
                      single [USAGE] line with that total.
        agent: Agent name for labels.
        start_dt: Starting timestamp. Defaults to 8 days ago.

    Returns:
        List of (line, nanosecond_timestamp) tuples suitable for _make_usage_stream.
    """
    if start_dt is None:
        start_dt = datetime.now(timezone.utc) - timedelta(days=8)
    entries = []
    for i, tokens in enumerate(block_totals):
        # Space entries 5 hours apart to land in different blocks
        ts = start_dt + timedelta(hours=5 * i)
        ts_ns = str(int(ts.timestamp() * 1e9))
        cost = round(tokens * 0.0000108, 2)  # approximate cost
        entries.append((f"[USAGE] total_tokens={tokens} cost_usd={cost}", ts_ns))
    return entries


# ---------------------------------------------------------------------------
# STORY-543: _compute_p90 pure function tests
# ---------------------------------------------------------------------------


class TestComputeP90:
    """Tests for _compute_p90() — STORY-543."""

    def test_p90_happy_path_10_blocks(self):
        """10 sorted block totals → P90 is the value at the 90th percentile."""
        block_totals = [
            100_000, 110_000, 120_000, 130_000, 140_000,
            150_000, 160_000, 170_000, 180_000, 200_000,
        ]
        result = _compute_p90(block_totals)
        assert result is not None
        # P90 of 10 values: index 8 (0-based) in sorted = 180_000,
        # or interpolated ~182_000. Allow range.
        assert 178_000 <= result <= 202_000

    def test_p90_exactly_3_blocks_minimum(self):
        """Exactly 3 blocks (minimum threshold) → returns a value."""
        block_totals = [100_000, 150_000, 200_000]
        result = _compute_p90(block_totals)
        assert result is not None
        assert isinstance(result, int)
        # P90 of [100K, 150K, 200K] should be close to 200K
        assert result >= 150_000

    def test_p90_fewer_than_3_blocks_returns_none(self):
        """Fewer than 3 blocks → returns None (insufficient data)."""
        result = _compute_p90([100_000, 150_000])
        assert result is None

    def test_p90_empty_list_returns_none(self):
        """Empty list → returns None."""
        result = _compute_p90([])
        assert result is None

    def test_p90_all_identical_values(self):
        """All identical values → P90 equals that value."""
        block_totals = [100_000] * 10
        result = _compute_p90(block_totals)
        assert result == 100_000

    def test_p90_returns_int(self):
        """P90 result should always be an int (not float) when not None."""
        block_totals = [100_000, 133_333, 166_666, 200_000]
        result = _compute_p90(block_totals)
        assert result is not None
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# STORY-543: query_historical_block_tokens tests
# ---------------------------------------------------------------------------


class TestQueryHistoricalBlockTokens:
    """Tests for LokiClient.query_historical_block_tokens() — STORY-543."""

    @pytest.mark.asyncio
    async def test_returns_per_block_totals_from_8_days(self):
        """8 days of [USAGE] data → returns list of per-block token totals."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        block_totals_input = [100_000, 120_000, 150_000, 180_000, 130_000]
        entries = _make_historical_usage_entries(block_totals_input)
        mock_http.get.return_value = _make_loki_response(
            [_make_usage_stream("dan", entries)]
        )
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_historical_block_tokens("dan")

        assert isinstance(result, list)
        assert len(result) > 0
        # Each element should be a positive int
        for total in result:
            assert isinstance(total, int)
            assert total > 0

    @pytest.mark.asyncio
    async def test_loki_error_returns_empty_list(self):
        """Loki HTTP 500 → returns empty list (graceful degradation)."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(500, text="Internal Server Error")
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_historical_block_tokens("dan")

        assert result == []

    @pytest.mark.asyncio
    async def test_no_entries_returns_empty_list(self):
        """No [USAGE] entries from Loki → returns empty list."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_empty_loki_response()
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_historical_block_tokens("dan")

        assert result == []

    @pytest.mark.asyncio
    async def test_queries_8_day_window(self):
        """The Loki query should span approximately 8 days."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_empty_loki_response()
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        before = datetime.now(timezone.utc)
        await client.query_historical_block_tokens("dan")
        after = datetime.now(timezone.utc)

        assert mock_http.get.called
        call_kwargs = mock_http.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        start_str = str(params.get("start", ""))
        end_str = str(params.get("end", ""))

        assert start_str, "Expected 'start' param in historical Loki query"

        def _parse_time(s: str) -> datetime:
            if s.isdigit() and len(s) > 13:
                return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        start_dt = _parse_time(start_str)
        end_dt = _parse_time(end_str)
        window_days = (end_dt - start_dt).total_seconds() / 86400

        # Should be approximately 8 days (allow ±0.5 day tolerance)
        assert 7.5 <= window_days <= 8.5, (
            f"Expected ~8 day window, got {window_days:.2f} days"
        )


# ---------------------------------------------------------------------------
# STORY-543: Updated query_agent_quota with P90 integration
# ---------------------------------------------------------------------------


class TestQueryAgentQuotaP90:
    """Tests for query_agent_quota() P90 integration — STORY-543."""

    @pytest.mark.asyncio
    async def test_p90_populates_quota_info_fields(self):
        """When P90 is available → p90_limit, remaining_tokens, percent_used populated."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        # Current block: 100K tokens
        current_entries = [
            ("[USAGE] total_tokens=100000 cost_usd=1.08", "1712000000000000000"),
        ]

        # We'll need two Loki queries: one for current block (5h), one for history (8d).
        # Set up side_effect to return different data based on the time window.
        call_count = 0

        def _side_effect(url, *, params, headers, **kwargs):
            nonlocal call_count
            call_count += 1
            start_str = str(params.get("start", ""))
            end_str = str(params.get("end", ""))

            # Parse time window to distinguish current vs historical query
            def _parse(s):
                if s.isdigit() and len(s) > 13:
                    return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            try:
                start_dt = _parse(start_str)
                end_dt = _parse(end_str)
                window_hours = (end_dt - start_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                window_hours = 5  # fallback

            if window_hours > 24:
                # Historical query — return data for P90 calculation
                hist_entries = _make_historical_usage_entries(
                    [150_000, 160_000, 170_000, 180_000, 190_000]
                )
                return _make_loki_response(
                    [_make_usage_stream("dan", hist_entries)]
                )
            else:
                # Current block query
                return _make_loki_response(
                    [_make_usage_stream("dan", current_entries)]
                )

        mock_http.get.side_effect = _side_effect
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_agent_quota("dan")

        assert isinstance(result, QuotaInfo)
        assert result.source.value == "loki"
        assert result.p90_limit is not None
        assert isinstance(result.p90_limit, int)
        assert result.p90_limit > 0
        # remaining_tokens should be based on p90_limit, not hardcoded 200K
        assert result.remaining_tokens is not None
        assert result.remaining_tokens == max(0, result.p90_limit - 100_000)
        # percent_used should be populated
        assert result.percent_used is not None
        assert 0 <= result.percent_used <= 100

    @pytest.mark.asyncio
    async def test_p90_fallback_insufficient_data(self):
        """Fewer than 3 historical blocks → falls back to 200K default, p90_limit=None."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        current_entries = [
            ("[USAGE] total_tokens=50000 cost_usd=0.54", "1712000000000000000"),
        ]

        def _side_effect(url, *, params, headers, **kwargs):
            start_str = str(params.get("start", ""))
            end_str = str(params.get("end", ""))

            def _parse(s):
                if s.isdigit() and len(s) > 13:
                    return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            try:
                start_dt = _parse(start_str)
                end_dt = _parse(end_str)
                window_hours = (end_dt - start_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                window_hours = 5

            if window_hours > 24:
                # Only 2 blocks — below minimum of 3
                hist_entries = _make_historical_usage_entries([100_000, 150_000])
                return _make_loki_response(
                    [_make_usage_stream("dan", hist_entries)]
                )
            else:
                return _make_loki_response(
                    [_make_usage_stream("dan", current_entries)]
                )

        mock_http.get.side_effect = _side_effect
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_agent_quota("dan")

        assert isinstance(result, QuotaInfo)
        assert result.source.value == "loki"
        # Fallback: p90_limit should be None (not enough data)
        assert result.p90_limit is None
        # remaining_tokens should use 200K default
        assert result.remaining_tokens == 200_000 - 50_000

    @pytest.mark.asyncio
    async def test_p90_fallback_on_historical_loki_error(self):
        """Historical Loki query fails → falls back to 200K, current block still works."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        current_entries = [
            ("[USAGE] total_tokens=80000 cost_usd=0.86", "1712000000000000000"),
        ]

        def _side_effect(url, *, params, headers, **kwargs):
            start_str = str(params.get("start", ""))
            end_str = str(params.get("end", ""))

            def _parse(s):
                if s.isdigit() and len(s) > 13:
                    return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            try:
                start_dt = _parse(start_str)
                end_dt = _parse(end_str)
                window_hours = (end_dt - start_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                window_hours = 5

            if window_hours > 24:
                # Historical query fails
                return httpx.Response(500, text="Internal Server Error")
            else:
                return _make_loki_response(
                    [_make_usage_stream("dan", current_entries)]
                )

        mock_http.get.side_effect = _side_effect
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_agent_quota("dan")

        assert isinstance(result, QuotaInfo)
        assert result.source.value == "loki"
        assert result.current_block_tokens == 80_000
        # Fallback to 200K default
        assert result.remaining_tokens == 200_000 - 80_000
        assert result.p90_limit is None

    @pytest.mark.asyncio
    async def test_p90_cached_across_calls(self):
        """Two calls within 1h → historical query called only once (cached)."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        current_entries = [
            ("[USAGE] total_tokens=50000 cost_usd=0.54", "1712000000000000000"),
        ]
        hist_call_count = 0

        def _side_effect(url, *, params, headers, **kwargs):
            nonlocal hist_call_count
            start_str = str(params.get("start", ""))
            end_str = str(params.get("end", ""))

            def _parse(s):
                if s.isdigit() and len(s) > 13:
                    return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            try:
                start_dt = _parse(start_str)
                end_dt = _parse(end_str)
                window_hours = (end_dt - start_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                window_hours = 5

            if window_hours > 24:
                hist_call_count += 1
                hist_entries = _make_historical_usage_entries(
                    [150_000, 160_000, 170_000, 180_000, 190_000]
                )
                return _make_loki_response(
                    [_make_usage_stream("dan", hist_entries)]
                )
            else:
                return _make_loki_response(
                    [_make_usage_stream("dan", current_entries)]
                )

        mock_http.get.side_effect = _side_effect
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # First call — should query history
        await client.query_agent_quota("dan")
        first_hist_count = hist_call_count

        # Second call — should use cache
        await client.query_agent_quota("dan")
        second_hist_count = hist_call_count

        assert first_hist_count == 1, "First call should query historical data"
        assert second_hist_count == 1, "Second call should use cached P90 (no new historical query)"

    @pytest.mark.asyncio
    async def test_p90_cache_expired_re_queries(self):
        """After TTL expires → historical query is called again."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        current_entries = [
            ("[USAGE] total_tokens=50000 cost_usd=0.54", "1712000000000000000"),
        ]
        hist_call_count = 0

        def _side_effect(url, *, params, headers, **kwargs):
            nonlocal hist_call_count
            start_str = str(params.get("start", ""))
            end_str = str(params.get("end", ""))

            def _parse(s):
                if s.isdigit() and len(s) > 13:
                    return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            try:
                start_dt = _parse(start_str)
                end_dt = _parse(end_str)
                window_hours = (end_dt - start_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                window_hours = 5

            if window_hours > 24:
                hist_call_count += 1
                hist_entries = _make_historical_usage_entries(
                    [150_000, 160_000, 170_000, 180_000, 190_000]
                )
                return _make_loki_response(
                    [_make_usage_stream("dan", hist_entries)]
                )
            else:
                return _make_loki_response(
                    [_make_usage_stream("dan", current_entries)]
                )

        mock_http.get.side_effect = _side_effect
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        # First call
        await client.query_agent_quota("dan")
        assert hist_call_count == 1

        # Simulate cache expiry by manipulating the internal cache timestamp
        for key in client._p90_cache:
            cache_entry = client._p90_cache[key]
            if isinstance(cache_entry, tuple) and len(cache_entry) == 2:
                client._p90_cache[key] = (cache_entry[0], time.monotonic() - 3700)

        # Second call after "expiry"
        await client.query_agent_quota("dan")
        assert hist_call_count >= 2, "Should re-query after cache expiry"

    @pytest.mark.asyncio
    async def test_percent_used_calculated_from_p90(self):
        """percent_used = (current_tokens / p90_limit) * 100."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        current_entries = [
            ("[USAGE] total_tokens=90000 cost_usd=0.97", "1712000000000000000"),
        ]

        def _side_effect(url, *, params, headers, **kwargs):
            start_str = str(params.get("start", ""))
            end_str = str(params.get("end", ""))

            def _parse(s):
                if s.isdigit() and len(s) > 13:
                    return datetime.fromtimestamp(int(s) / 1e9, tz=timezone.utc)
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            try:
                start_dt = _parse(start_str)
                end_dt = _parse(end_str)
                window_hours = (end_dt - start_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                window_hours = 5

            if window_hours > 24:
                # P90 should be ~180K from these values
                hist_entries = _make_historical_usage_entries(
                    [100_000, 120_000, 140_000, 160_000, 180_000,
                     130_000, 150_000, 170_000, 190_000, 200_000]
                )
                return _make_loki_response(
                    [_make_usage_stream("dan", hist_entries)]
                )
            else:
                return _make_loki_response(
                    [_make_usage_stream("dan", current_entries)]
                )

        mock_http.get.side_effect = _side_effect
        client = LokiClient("http://loki:3100", "test-key", mock_http)

        result = await client.query_agent_quota("dan")

        assert result.p90_limit is not None
        assert result.percent_used is not None
        # percent_used should be (90000 / p90_limit) * 100
        expected_pct = round((90_000 / result.p90_limit) * 100, 2)
        assert abs(result.percent_used - expected_pct) < 1.0
