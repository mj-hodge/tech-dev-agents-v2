"""Deterministic fixtures for usage->quota pipeline contract tests.

STORY-544: Mock factories for Loki responses and USAGE line generation.
All fixtures return mock objects — no real Loki, no real agent VMs.
Import explicitly in scenario files.
"""
from unittest.mock import AsyncMock, MagicMock
from typing import Any


def make_usage_lines(token_amounts: list[int]) -> list[str]:
    """Generate deterministic [USAGE] log lines with known token amounts.

    Args:
        token_amounts: List of token counts. Each generates one [USAGE] line.

    Returns:
        List of log line strings matching the format _parse_usage_line expects.
    """
    lines = []
    for tokens in token_amounts:
        cost = round(tokens * 0.000003, 6)  # ~$3/1M tokens
        lines.append(f"[USAGE] total_tokens={tokens} cost_usd={cost}")
    return lines


def mock_loki_entries(lines: list[str]) -> list:
    """Create mock LokiLogEntry-compatible objects from raw line strings.

    Returns objects with .line, .timestamp, .labels attributes
    matching the LokiLogEntry interface.
    """
    entries = []
    for line in lines:
        entry = MagicMock()
        entry.line = line
        entry.timestamp = "2026-04-23T12:00:00Z"
        entry.labels = {"agent": "test-agent"}
        entries.append(entry)
    return entries


def mock_loki_client_success(token_amounts: list[int]) -> AsyncMock:
    """Mock LokiClient whose query_range returns valid [USAGE] entries.

    Args:
        token_amounts: Token counts for each [USAGE] line.
    """
    lines = make_usage_lines(token_amounts)
    entries = mock_loki_entries(lines)
    client = AsyncMock()
    client.query_range = AsyncMock(return_value=entries)
    return client


def mock_loki_client_error() -> AsyncMock:
    """Mock LokiClient whose query_range raises LokiError.

    Simulates Loki unreachable — should produce NO_DATA with LOKI_ERROR reason.
    """
    from tech_dev_agents.ops_console.services.loki_client import LokiError

    client = AsyncMock()
    client.query_range = AsyncMock(side_effect=LokiError(502, "Bad Gateway"))
    return client


def mock_loki_client_empty() -> AsyncMock:
    """Mock LokiClient whose query_range returns empty list.

    Simulates no matching entries — should produce NO_DATA with EMPTY_RESULT.
    """
    client = AsyncMock()
    client.query_range = AsyncMock(return_value=[])
    return client


def mock_loki_client_unparseable() -> AsyncMock:
    """Mock LokiClient whose query_range returns entries that don't parse.

    Simulates [USAGE] format drift — should produce NO_DATA with PARSE_MISS.
    """
    garbage_lines = ["not a usage line", "random garbage", "[USAGE] malformed"]
    entries = mock_loki_entries(garbage_lines)
    client = AsyncMock()
    client.query_range = AsyncMock(return_value=entries)
    return client


def fake_quota_info(**overrides: Any) -> dict[str, Any]:
    """Factory for QuotaInfo-compatible dicts with sensible defaults."""
    defaults = {
        "source": "loki",
        "current_block_tokens": 50000,
        "current_block_cost_usd": 0.15,
        "sessions_in_block": 3,
        "reset_in_minutes": 120,
        "remaining_tokens": 150000,
        "block_start": "10:00Z",
        "block_end": "15:00Z",
    }
    defaults.update(overrides)
    return defaults
