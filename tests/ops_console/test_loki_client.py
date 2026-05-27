"""Tests for LokiClient — T22-T27."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from tech_dev_agents.ops_console.services.loki_client import (
    LokiClient,
    LokiError,
    sanitize_label_value,
)


def _make_loki_response(streams=None, status=200):
    """Build a mock Loki API response."""
    if streams is None:
        streams = []
    data = {"data": {"result": streams, "resultType": "streams"}}
    return httpx.Response(status, json=data)


def _make_cost_summary_stream(agent="dan"):
    return {
        "stream": {"job": "agent-logs", "agent": agent},
        "values": [
            [
                "1711929600000000000",
                f"[COST_SUMMARY] agent={agent} date=2026-04-01 sdk_sessions=8 sdk_cost=$10.25 sdk_turns=142",
            ]
        ],
    }


def _make_done_stream(agent="dan"):
    return {
        "stream": {"job": "agent-logs", "agent": agent},
        "values": [
            ["1711929600000000000", "[DONE] session=abc123 cost=$5.50 turns=42"],
        ],
    }


def _make_guidance_stream(agent="dan"):
    return {
        "stream": {"job": "claude-code", "agent": agent},
        "values": [
            [
                "1711929600000000000",
                "[Claude Code] [Agent Guidance] Implement STORY-021 queue integration end-to-end",
            ]
        ],
    }


def _make_anomaly_stream(agent="dan"):
    return {
        "stream": {"job": "agent-logs", "agent": agent},
        "values": [
            [
                "1711929600000000000",
                f"[COST_ANOMALY] agent={agent} detected coding without Claude Code SDK",
            ]
        ],
    }


class TestLokiClientQueries:
    """T22-T26: Loki HTTP API query client."""

    @pytest.mark.asyncio
    async def test_query_cost_summaries_parses_lines(self):
        """T22: query_cost_summaries returns parsed cost fields from [COST_SUMMARY] log lines."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response([_make_cost_summary_stream()])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.query_cost_summaries("dan", "2026-04-01", "2026-04-02")

        assert len(result) == 1
        assert result[0]["agent_name"] == "dan"
        assert result[0]["cost"] == 10.25
        assert result[0]["sessions"] == 8
        assert result[0]["turns"] == 142

    @pytest.mark.asyncio
    async def test_query_current_work_prefers_agent_guidance(self):
        """Current work should come from latest [Agent Guidance] text when available."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response([_make_guidance_stream()])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.query_current_work("dan", lookback_hours=1)

        assert result == "Implement STORY-021 queue integration end-to-end"

    @pytest.mark.asyncio
    async def test_query_done_lines_uses_regex(self):
        """T23: query_done_lines extracts cost from [DONE] lines using cost_collector regex pattern."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response([_make_done_stream()])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.query_done_lines("dan", "2026-04-01", "2026-04-02")

        assert len(result) == 1
        assert result[0]["cost"] == 5.50
        assert result[0]["turns"] == 42

    @pytest.mark.asyncio
    async def test_query_range_handles_empty_result(self):
        """T24: query_range with no matching logs returns empty list (not error)."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response([])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.query_range(
            '{job="agent-logs"}', "2026-04-01", "2026-04-02"
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_query_anomalies_parses_cost_anomaly(self):
        """T25: query_anomalies returns structured anomaly data from [COST_ANOMALY] lines."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response([_make_anomaly_stream()])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.query_anomalies("dan", "2026-04-01", "2026-04-02")

        assert len(result) == 1
        assert result[0]["agent_name"] == "dan"
        assert result[0]["type"] == "cost_anomaly"
        assert result[0]["active"] is True

    @pytest.mark.asyncio
    async def test_query_range_loki_500_raises_loki_error(self):
        """T26: query_range with Loki returning 500 raises LokiError with status and message."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(500, text="Internal Server Error")

        client = LokiClient("http://loki:3100", "test-key", mock_http)

        with pytest.raises(LokiError) as exc_info:
            await client.query_range('{job="agent-logs"}', "2026-04-01", "2026-04-02")

        assert exc_info.value.status_code == 500
        assert "Internal Server Error" in exc_info.value.message


class TestLokiClientHealth:
    """T27: Loki reachability check."""

    @pytest.mark.asyncio
    async def test_is_reachable_returns_true_on_200(self):
        """T27: is_reachable returns True when GET /ready returns 200."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = httpx.Response(200, text="ready")

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.is_reachable()

        assert result is True
