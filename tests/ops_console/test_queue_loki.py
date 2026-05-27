"""Tests for STORY-025: Loki queue parsing and fleet queue wiring.

Phase 7 test design — T20-T26 covering get_agent_queue() and fleet integration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from tech_dev_agents.ops_console.services.loki_client import (
    LokiClient,
    LokiLogEntry,
    _parse_queue_line,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loki_response(streams=None, status=200):
    """Build a mock Loki API response."""
    if streams is None:
        streams = []
    data = {"data": {"result": streams, "resultType": "streams"}}
    return httpx.Response(status, json=data)


def _make_queue_stream(agent="dan", line=None):
    """Build a Loki stream with a [QUEUE] log line."""
    if line is None:
        line = "[QUEUE] active=STORY-024 queued=STORY-093,STORY-094 total=2"
    return {
        "stream": {"job": "agent-logs", "agent": agent},
        "values": [["1711929600000000000", line]],
    }


# ---------------------------------------------------------------------------
# T20: _parse_queue_line parses active + queued
# ---------------------------------------------------------------------------


class TestParseQueueLine:
    """T20-T22: _parse_queue_line extracts structured data from [QUEUE] lines."""

    def test_parse_full_queue_line(self):
        """T20: Parse [QUEUE] line with active story and queued stories."""
        result = _parse_queue_line(
            "[QUEUE] active=STORY-024 queued=STORY-093,STORY-094 total=2"
        )
        assert result is not None
        assert result["active"] == "STORY-024"
        assert result["queued"] == ["STORY-093", "STORY-094"]
        assert result["total"] == 2

    def test_parse_no_active(self):
        """T21: Parse [QUEUE] line with no active story."""
        result = _parse_queue_line(
            "[QUEUE] active=none queued=STORY-093 total=1"
        )
        assert result is not None
        assert result["active"] is None
        assert result["queued"] == ["STORY-093"]
        assert result["total"] == 1

    def test_parse_empty_queue(self):
        """T22: Parse [QUEUE] line with no queued stories."""
        result = _parse_queue_line(
            "[QUEUE] active=none queued=none total=0"
        )
        assert result is not None
        assert result["active"] is None
        assert result["queued"] == []
        assert result["total"] == 0

    def test_parse_non_queue_line_returns_none(self):
        """Non-[QUEUE] lines return None."""
        result = _parse_queue_line("[DONE] turns=42 cost=$5.50")
        assert result is None


# ---------------------------------------------------------------------------
# T23-T24: get_agent_queue() Loki integration
# ---------------------------------------------------------------------------


class TestGetAgentQueue:
    """T23-T24: get_agent_queue queries Loki and returns parsed queue state."""

    @pytest.mark.asyncio
    async def test_get_agent_queue_returns_parsed_state(self):
        """T23: get_agent_queue returns active and queued stories from Loki."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response(
            [_make_queue_stream("dan")]
        )

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.get_agent_queue("dan")

        assert result is not None
        assert result["active"] == "STORY-024"
        assert result["queued"] == ["STORY-093", "STORY-094"]
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_get_agent_queue_no_logs_returns_none(self):
        """T24: get_agent_queue returns None when no [QUEUE] lines exist."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_loki_response([])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.get_agent_queue("dan")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_agent_queue_uses_latest_line(self):
        """T25: When multiple [QUEUE] lines exist, use the most recent."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        stream = {
            "stream": {"job": "agent-logs", "agent": "dan"},
            "values": [
                ["1711929500000000000", "[QUEUE] active=STORY-020 queued=none total=0"],
                ["1711929600000000000", "[QUEUE] active=STORY-024 queued=STORY-093 total=1"],
            ],
        }
        mock_http.get.return_value = _make_loki_response([stream])

        client = LokiClient("http://loki:3100", "test-key", mock_http)
        result = await client.get_agent_queue("dan")

        assert result is not None
        assert result["active"] == "STORY-024"
        assert result["queued"] == ["STORY-093"]


# ---------------------------------------------------------------------------
# T26: Fleet route wires queue data
# ---------------------------------------------------------------------------


class TestFleetQueueWiring:
    """T26: Fleet endpoint populates queued_stories from Loki."""

    @pytest.mark.asyncio
    async def test_fleet_agents_include_queued_stories(
        self, client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """T26: GET /api/fleet agents include queued_stories from Loki data."""
        mock_loki_client.get_agent_queue.return_value = {
            "active": "STORY-024",
            "queued": ["STORY-093", "STORY-094"],
            "total": 2,
        }
        mock_cost_service.get_fleet_monthly_spend.return_value = 120.0

        from tests.ops_console.conftest import inject_mock_services
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await client.get("/api/fleet")
        assert resp.status_code == 200
        data = resp.json()

        # Each agent should have queued_stories populated
        for agent in data["agents"]:
            assert "queued_stories" in agent
            assert len(agent["queued_stories"]) == 2
            story_ids = [q["story_id"] for q in agent["queued_stories"]]
            assert "STORY-093" in story_ids
            assert "STORY-094" in story_ids
