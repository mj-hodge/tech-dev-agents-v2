"""Cost collection from Claude Code SDK session logs.

STORY-015: Deploy Agent Dashboards & Wiring
Phase 8 — Implementation

Parses SDK session logs for cost data and produces structured summary
lines for Promtail/Loki ingestion. Designed to run as a cron job.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostSummary:
    """Aggregated cost data from SDK session logs."""

    agent_name: str
    date: str  # YYYY-MM-DD
    sdk_sessions: int
    sdk_cost: float
    sdk_turns: int


# ---------------------------------------------------------------------------
# Log Parsing
# ---------------------------------------------------------------------------

# Matches lines like: [DONE] cost=$16.59 turns=42
_DONE_PATTERN = re.compile(
    r"\[DONE\]"
    r"(?:.*?cost=\$(?P<cost>[\d.]+))?"
    r"(?:.*?turns=(?P<turns>\d+))?"
)


def parse_done_lines(text: str) -> list[dict[str, float | int]]:
    """Parse [DONE] lines from log text.

    Returns a list of dicts with 'cost' and 'turns' keys.
    Lines without a cost field are skipped.
    """
    results: list[dict[str, float | int]] = []
    for line in text.splitlines():
        match = _DONE_PATTERN.search(line)
        if match and match.group("cost") is not None:
            cost = float(match.group("cost"))
            turns = int(match.group("turns")) if match.group("turns") else 0
            results.append({"cost": cost, "turns": turns})
    return results


def collect_costs_from_logs(
    log_dir: str,
    agent_name: str,
    date: str | None = None,
) -> CostSummary:
    """Collect cost data from all session log files in a directory.

    Scans for session-*.log files, parses [DONE] lines, and aggregates
    into a CostSummary.

    Args:
        log_dir: Directory containing session-*.log files.
        agent_name: Name of the agent.
        date: Date string (YYYY-MM-DD). Defaults to today UTC.

    Returns:
        CostSummary with aggregated data.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pattern = os.path.join(log_dir, "session-*.log")
    log_files = glob.glob(pattern)

    total_sessions = 0
    total_cost = 0.0
    total_turns = 0

    for log_file in sorted(log_files):
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        entries = parse_done_lines(content)
        if entries:
            total_sessions += len(entries)
            total_cost += sum(e["cost"] for e in entries)
            total_turns += sum(int(e["turns"]) for e in entries)

    return CostSummary(
        agent_name=agent_name,
        date=date,
        sdk_sessions=total_sessions,
        sdk_cost=round(total_cost, 2),
        sdk_turns=total_turns,
    )


def format_cost_summary_log(summary: CostSummary) -> str:
    """Format a CostSummary as a structured log line for Promtail.

    Output format:
        [COST_SUMMARY] agent=dan date=2026-03-31 sdk_sessions=14 sdk_cost=$16.59 sdk_turns=419
    """
    return (
        f"[COST_SUMMARY] agent={summary.agent_name} "
        f"date={summary.date} "
        f"sdk_sessions={summary.sdk_sessions} "
        f"sdk_cost=${summary.sdk_cost:.2f} "
        f"sdk_turns={summary.sdk_turns}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CostSummary",
    "collect_costs_from_logs",
    "format_cost_summary_log",
    "parse_done_lines",
]
