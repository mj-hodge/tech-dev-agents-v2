"""Tests for STORY-015: Cost Collector (SC-2).

Phase 7 test design — 7 tests covering log parsing and cost aggregation.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from tech_dev_agents.cost_collector import (
    CostSummary,
    collect_costs_from_logs,
    format_cost_summary_log,
    parse_done_lines,
)


# ---------------------------------------------------------------------------
# SC-2: Cost collection cron
# ---------------------------------------------------------------------------


class TestParseDoneLines:
    """Tests for parse_done_lines."""

    def test_parse_single_done_line(self) -> None:
        """Extracts cost from a single [DONE] line."""
        text = "2026-03-31 10:00:00 [DONE] cost=$16.59 turns=42"
        results = parse_done_lines(text)
        assert len(results) == 1
        assert results[0]["cost"] == 16.59
        assert results[0]["turns"] == 42

    def test_parse_multiple_done_lines(self) -> None:
        """Sums costs from multiple [DONE] lines."""
        text = (
            "[DONE] cost=$10.00 turns=20\n"
            "[DONE] cost=$5.50 turns=15\n"
            "[DONE] cost=$3.25 turns=8\n"
        )
        results = parse_done_lines(text)
        assert len(results) == 3
        total_cost = sum(r["cost"] for r in results)
        assert abs(total_cost - 18.75) < 0.01

    def test_ignores_non_done_lines(self) -> None:
        """Non-DONE lines are ignored."""
        text = (
            "[START] session=sess-001\n"
            "[INFO] processing...\n"
            "[DONE] cost=$5.00 turns=10\n"
            "[END] cleanup\n"
        )
        results = parse_done_lines(text)
        assert len(results) == 1
        assert results[0]["cost"] == 5.00

    def test_handles_missing_cost_field(self) -> None:
        """[DONE] lines without cost= are skipped."""
        text = "[DONE] status=complete\n[DONE] cost=$2.00 turns=5"
        results = parse_done_lines(text)
        assert len(results) == 1
        assert results[0]["cost"] == 2.00


class TestFormatCostSummaryLog:
    """Tests for format_cost_summary_log."""

    def test_produces_correct_format(self) -> None:
        """Output matches expected structured log format."""
        summary = CostSummary(
            agent_name="dan",
            date="2026-03-31",
            sdk_sessions=14,
            sdk_cost=16.59,
            sdk_turns=419,
        )
        line = format_cost_summary_log(summary)
        assert line == "[COST_SUMMARY] agent=dan date=2026-03-31 sdk_sessions=14 sdk_cost=$16.59 sdk_turns=419"


class TestCollectCostsFromLogs:
    """Tests for collect_costs_from_logs."""

    def test_empty_dir_returns_zero_summary(self) -> None:
        """Empty log directory returns zero-value summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = collect_costs_from_logs(tmpdir, "dan", date="2026-03-31")
            assert summary.sdk_sessions == 0
            assert summary.sdk_cost == 0.0
            assert summary.sdk_turns == 0
            assert summary.agent_name == "dan"

    def test_aggregates_from_real_log_files(self) -> None:
        """Aggregates cost data from multiple session log files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two session log files
            with open(os.path.join(tmpdir, "session-001.log"), "w") as f:
                f.write("[START] session=001\n[DONE] cost=$10.00 turns=20\n")
            with open(os.path.join(tmpdir, "session-002.log"), "w") as f:
                f.write("[START] session=002\n[DONE] cost=$5.50 turns=15\n[DONE] cost=$3.00 turns=8\n")

            summary = collect_costs_from_logs(tmpdir, "dan", date="2026-03-31")
            assert summary.sdk_sessions == 3
            assert abs(summary.sdk_cost - 18.50) < 0.01
            assert summary.sdk_turns == 43
