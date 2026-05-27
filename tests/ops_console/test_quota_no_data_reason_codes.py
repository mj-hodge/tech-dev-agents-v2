"""Tests for STORY-536: Quota no_data internal reason classification.

Each no_data branch in LokiClient.query_agent_quota() must:
  1. Tag itself with a QuotaNoDataReason (LOKI_ERROR, EMPTY_RESULT, PARSE_MISS)
  2. Emit a structured logger.warning with agent, reason, entries_seen
  3. Increment the corresponding counter in _NO_DATA_COUNTERS
  4. Return QuotaInfo(source=QuotaSourceEnum.NO_DATA) — public contract unchanged

RED state: ImportError — QuotaNoDataReason, get_no_data_counters,
           _reset_no_data_counters are not yet defined in loki_client.py.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import httpx
import pytest

# These imports will fail (ImportError) until Phase 8 adds the symbols.
from tech_dev_agents.ops_console.services.loki_client import (
    LokiClient,
    LokiError,
    QuotaNoDataReason,         # NEW — internal enum (Phase 8 target)
    _reset_no_data_counters,   # NEW — test-isolation helper (Phase 8 target)
    get_no_data_counters,      # NEW — counter inspector (Phase 8 target)
)
from tech_dev_agents.ops_console.models.responses import QuotaInfo, QuotaSourceEnum

_LOKI_LOGGER = "tech_dev_agents.ops_console.services.loki_client"

# ---------------------------------------------------------------------------
# Helpers (shared with test_loki_client_quota.py pattern)
# ---------------------------------------------------------------------------


def _make_loki_response(streams=None, status: int = 200):
    """Build a mock Loki API response."""
    if streams is None:
        streams = []
    data = {"data": {"result": streams, "resultType": "streams"}}
    return httpx.Response(status, json=data)


def _make_stream(agent: str, lines: list[str]):
    """Build a Loki stream with the given log lines."""
    return {
        "stream": {"agent": agent},
        "values": [[str(1712000000000000000 + i * 1_000_000), line] for i, line in enumerate(lines)],
    }


def _make_client(mock_http: AsyncMock) -> LokiClient:
    return LokiClient("http://loki:3100", "test-key", mock_http)


# ---------------------------------------------------------------------------
# Fixture: reset counters before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset no_data counters before each test to prevent cross-test pollution."""
    _reset_no_data_counters()
    yield
    _reset_no_data_counters()


# ---------------------------------------------------------------------------
# AC-2, AC-3, AC-4: Reason-code classification + telemetry + counters
# ---------------------------------------------------------------------------


class TestNoDataReasonCodes:
    """STORY-536 AC-2/3/4: Each no_data branch emits reason code, log, counter."""

    @pytest.mark.asyncio
    async def test_loki_error_increments_counter_and_logs_reason(self, caplog):
        """AC-2/3/4: LokiError → reason=LOKI_ERROR, counter loki_error=1, warning logged.

        RED: ImportError on QuotaNoDataReason / get_no_data_counters.
        """
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(500, text="Internal Server Error")
        client = _make_client(mock_http)

        with caplog.at_level(logging.WARNING, logger=_LOKI_LOGGER):
            result = await client.query_agent_quota("dan")

        # Counter incremented
        counters = get_no_data_counters()
        assert counters["loki_error"] == 1, f"Expected loki_error=1, got {counters}"
        assert counters["empty_result"] == 0
        assert counters["parse_miss"] == 0

        # Structured log emitted
        assert any(
            "reason=loki_error" in r.message for r in caplog.records
        ), f"Expected 'reason=loki_error' in log, got: {[r.message for r in caplog.records]}"
        assert any(
            "agent=dan" in r.message for r in caplog.records
        ), "Expected 'agent=dan' in log"

        # Public contract unchanged
        assert isinstance(result, QuotaInfo)
        assert result.source == QuotaSourceEnum.NO_DATA

    @pytest.mark.asyncio
    async def test_empty_result_increments_counter_and_logs_reason(self, caplog):
        """AC-2/3/4: Empty Loki response → reason=EMPTY_RESULT, counter empty_result=1.

        RED: ImportError on QuotaNoDataReason / get_no_data_counters.
        """
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response(streams=[])
        client = _make_client(mock_http)

        with caplog.at_level(logging.WARNING, logger=_LOKI_LOGGER):
            result = await client.query_agent_quota("daisy")

        # Counter
        counters = get_no_data_counters()
        assert counters["empty_result"] == 1, f"Expected empty_result=1, got {counters}"
        assert counters["loki_error"] == 0
        assert counters["parse_miss"] == 0

        # Structured log
        msgs = [r.message for r in caplog.records]
        assert any("reason=empty_result" in m for m in msgs), f"Expected reason=empty_result in {msgs}"
        assert any("agent=daisy" in m for m in msgs), f"Expected agent=daisy in {msgs}"
        assert any("entries_seen=0" in m for m in msgs), f"Expected entries_seen=0 in {msgs}"

        # Public contract
        assert result.source == QuotaSourceEnum.NO_DATA
        assert result.current_block_tokens is None

    @pytest.mark.asyncio
    async def test_parse_miss_increments_counter_and_logs_reason_with_entry_count(self, caplog):
        """AC-2/3/4: Entries exist but none parseable → PARSE_MISS, entries_seen=3.

        Three lines contain '[USAGE]' (so Loki filter matches) but lack
        'total_tokens=' so _parse_usage_line returns None for all.

        RED: ImportError on QuotaNoDataReason / get_no_data_counters.
        """
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        # Lines contain [USAGE] but NOT 'total_tokens=' — parse fails for all
        bad_lines = [
            "[USAGE] unknown_metric=123 cost_usd=0.54",
            "[USAGE] tokens_used=50000 spend=0.86",   # wrong field name
            "[USAGE] malformed line without any fields",
        ]
        mock_http.get.return_value = _make_loki_response([_make_stream("derrick", bad_lines)])
        client = _make_client(mock_http)

        with caplog.at_level(logging.WARNING, logger=_LOKI_LOGGER):
            result = await client.query_agent_quota("derrick")

        # Counter
        counters = get_no_data_counters()
        assert counters["parse_miss"] == 1, f"Expected parse_miss=1, got {counters}"
        assert counters["loki_error"] == 0
        assert counters["empty_result"] == 0

        # Structured log — must include entry count
        msgs = [r.message for r in caplog.records]
        assert any("reason=parse_miss" in m for m in msgs), f"Expected reason=parse_miss in {msgs}"
        assert any("agent=derrick" in m for m in msgs), f"Expected agent=derrick in {msgs}"
        assert any("entries_seen=3" in m for m in msgs), f"Expected entries_seen=3 in {msgs}"

        # Public contract
        assert result.source == QuotaSourceEnum.NO_DATA
        assert result.current_block_tokens is None

    @pytest.mark.asyncio
    async def test_happy_path_does_not_increment_any_counter(self):
        """AC-4: Successful quota aggregation must NOT touch any no_data counter.

        RED: ImportError on get_no_data_counters.
        """
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        good_lines = ["[USAGE] total_tokens=50000 cost_usd=0.54"]
        mock_http.get.return_value = _make_loki_response([_make_stream("dan", good_lines)])
        client = _make_client(mock_http)

        result = await client.query_agent_quota("dan")

        # No counter incremented
        counters = get_no_data_counters()
        assert counters["loki_error"] == 0
        assert counters["empty_result"] == 0
        assert counters["parse_miss"] == 0

        # Did return populated quota
        assert result.source == QuotaSourceEnum.LOKI
        assert result.current_block_tokens == 50000


# ---------------------------------------------------------------------------
# AC-5: Public API contract backward-compatibility
# ---------------------------------------------------------------------------


class TestPublicApiContractUnchanged:
    """STORY-536 AC-5: All three no_data branches still return source=no_data externally."""

    @pytest.mark.asyncio
    async def test_loki_error_returns_no_data_source(self):
        """LOKI_ERROR branch: external source field is still 'no_data'."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(503, text="Service Unavailable")
        client = _make_client(mock_http)

        result = await client.query_agent_quota("dan")
        assert str(result.source) == "no_data"
        assert result.current_block_tokens is None

    @pytest.mark.asyncio
    async def test_empty_result_returns_no_data_source(self):
        """EMPTY_RESULT branch: external source field is still 'no_data'."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response(streams=[])
        client = _make_client(mock_http)

        result = await client.query_agent_quota("dan")
        assert str(result.source) == "no_data"
        assert result.current_block_tokens is None

    @pytest.mark.asyncio
    async def test_parse_miss_returns_no_data_source(self):
        """PARSE_MISS branch: external source field is still 'no_data'."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        bad_lines = ["[USAGE] broken_format xyz"]
        mock_http.get.return_value = _make_loki_response([_make_stream("dan", bad_lines)])
        client = _make_client(mock_http)

        result = await client.query_agent_quota("dan")
        assert str(result.source) == "no_data"

    @pytest.mark.asyncio
    async def test_no_data_result_has_no_new_public_fields(self):
        """AC-5: QuotaInfo returned for no_data does NOT grow new fields.

        QuotaSourceEnum must NOT have a new member for each reason — only NO_DATA.
        """
        # The source enum should not have LOKI_ERROR / EMPTY_RESULT / PARSE_MISS
        source_values = {e.value for e in QuotaSourceEnum}
        assert "loki_error" not in source_values, (
            "QuotaSourceEnum must NOT expose loki_error — reason codes are internal"
        )
        assert "empty_result" not in source_values, (
            "QuotaSourceEnum must NOT expose empty_result — reason codes are internal"
        )
        assert "parse_miss" not in source_values, (
            "QuotaSourceEnum must NOT expose parse_miss — reason codes are internal"
        )
        assert "no_data" in source_values, "QuotaSourceEnum.NO_DATA must still exist"


# ---------------------------------------------------------------------------
# AC-4: Counter reset helper contract
# ---------------------------------------------------------------------------


class TestCounterResetHelper:
    """AC-4: _reset_no_data_counters() zeroes all counters reliably."""

    @pytest.mark.asyncio
    async def test_reset_zeroes_all_counters(self):
        """After firing all three branches, reset brings all counts to zero.

        RED: ImportError on _reset_no_data_counters / get_no_data_counters.
        """
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        client = _make_client(mock_http)

        # Fire loki_error
        mock_http.get.return_value = httpx.Response(500, text="err")
        await client.query_agent_quota("dan")

        # Fire empty_result
        mock_http.get.return_value = _make_loki_response(streams=[])
        await client.query_agent_quota("dan")

        # Fire parse_miss
        mock_http.get.return_value = _make_loki_response([_make_stream("dan", ["[USAGE] bad"])])
        await client.query_agent_quota("dan")

        # Confirm all > 0 before reset
        pre = get_no_data_counters()
        assert pre["loki_error"] >= 1
        assert pre["empty_result"] >= 1
        assert pre["parse_miss"] >= 1

        # Reset and confirm zero
        _reset_no_data_counters()
        post = get_no_data_counters()
        assert post == {"loki_error": 0, "empty_result": 0, "parse_miss": 0}

    def test_get_no_data_counters_returns_copy(self):
        """AC-4: get_no_data_counters() returns a shallow copy, not the live dict.

        Mutating the return value must NOT affect internal counters.

        RED: ImportError on get_no_data_counters / _reset_no_data_counters.
        """
        snapshot = get_no_data_counters()
        snapshot["loki_error"] = 999   # mutate the copy

        fresh = get_no_data_counters()
        assert fresh["loki_error"] == 0, (
            "Mutating get_no_data_counters() return value should not affect internal state"
        )
