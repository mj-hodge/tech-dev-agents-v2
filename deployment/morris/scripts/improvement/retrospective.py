"""STORY-727: Retrospective — generate weekly [BRIEFING] DM for Morris improvement loop.

Runs Monday 09:00 ET. Assembles a [BRIEFING] DM covering:
- Top-3 recurring patterns (by count, excluding count < 3)
- Proposals decided in the past 7 days
- Tracking measurements in the past 7 days
- Pending proposals awaiting Mark's decision

Empty sections render as "(none)" rather than being omitted.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ._async_util import run_async as _run_async

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stub helpers — patched in tests
# ---------------------------------------------------------------------------


def _get_teams_client():  # pragma: no cover
    """Return a Teams DM client. Patched in tests."""
    return None


def _get_db_service():  # pragma: no cover
    """Return the improvement DB service. Patched in tests."""
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_top3(pattern_counts: dict[str, int]) -> str:
    """Format the Top-3 recurring patterns section.

    Excludes patterns with count < 3. Shows top 3 by count descending.
    """
    # Filter out patterns below minimum count of 3
    eligible = [(k, v) for k, v in pattern_counts.items() if v >= 3]
    sorted_patterns = sorted(eligible, key=lambda x: x[1], reverse=True)
    top3 = sorted_patterns[:3]

    if not top3:
        return "(none)"

    lines = []
    for key, count in top3:
        lines.append(f"  - {key}: {count}")
    return "\n".join(lines)


def _format_list(items: list, item_formatter=None) -> str:
    """Format a list of items; returns '(none)' if empty."""
    if not items:
        return "(none)"
    if item_formatter:
        return "\n".join(item_formatter(item) for item in items)
    return "\n".join(f"  - {item}" for item in items)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_retrospective(
    pattern_counts: dict[str, int],
    db_service: Any = None,
    now: datetime | None = None,
    teams_client: Any = None,
) -> str:
    """Generate and return the weekly [BRIEFING] report as a string.

    Also posts the briefing as a DM via teams_client (if available).

    Args:
        pattern_counts: Mapping of pattern_key -> count for the current week.
        db_service: The improvement DB service.
        now: The current datetime (for time-travel tests).
        teams_client: Teams DM client.

    Returns:
        The formatted briefing string.
    """
    db = db_service or _get_db_service()
    teams = teams_client or _get_teams_client()

    if now is None:
        from datetime import timezone
        now = datetime.now(tz=timezone.utc)

    # Fetch data from DB
    pending_proposals = []
    recent_decisions = []
    recent_tracking = []

    if db is not None:
        try:
            pending_proposals = _run_async(db.list_pending_proposals()) or []
        except Exception as exc:
            logger.warning("Could not fetch pending proposals: %s", exc)

        try:
            recent_decisions = _run_async(db.list_recent_decisions()) or []
        except Exception as exc:
            logger.warning("Could not fetch recent decisions: %s", exc)

        try:
            recent_tracking = _run_async(db.list_recent_tracking_measurements()) or []
        except Exception as exc:
            logger.warning("Could not fetch recent tracking measurements: %s", exc)

    # Count applied proposals from recent decisions
    proposals_applied = sum(
        1 for d in recent_decisions
        if (d.get("status") if isinstance(d, dict) else getattr(d, "status", "")) == "applied"
    )

    # Compute avg delta from recent tracking
    deltas = []
    for t in recent_tracking:
        if isinstance(t, dict):
            before = t.get("before_value")
            after = t.get("metric_value", t.get("after_value"))
        else:
            before = getattr(t, "before_value", None)
            after = getattr(t, "metric_value", None)
        if before is not None and after is not None:
            try:
                deltas.append(float(after) - float(before))
            except (TypeError, ValueError):
                pass

    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0

    # Count open patterns (patterns in pattern_counts that are above threshold)
    open_patterns = len([k for k, v in pattern_counts.items() if v >= 3])

    # Build the report
    top3_section = _format_top3(pattern_counts)

    def _fmt_decision(d):
        if isinstance(d, dict):
            return f"  - Proposal #{d.get('id', '?')}: {d.get('status', '?')} by {d.get('decided_by', '?')}"
        return f"  - {d}"

    def _fmt_tracking(t):
        if isinstance(t, dict):
            return f"  - Proposal #{t.get('proposal_id', '?')}: {t.get('metric_name', '?')} = {t.get('metric_value', '?')}"
        return f"  - {t}"

    def _fmt_pending(p):
        if isinstance(p, dict):
            return f"  - Proposal #{p.get('id', '?')}: {p.get('pattern_key', '?')} [{p.get('target_file', '?')}]"
        return f"  - {p}"

    decisions_section = _format_list(recent_decisions, _fmt_decision)
    tracking_section = _format_list(recent_tracking, _fmt_tracking)
    pending_section = _format_list(pending_proposals, _fmt_pending)

    report = (
        f"[BRIEFING] Weekly Self-Improvement Loop — {now.strftime('%Y-%m-%d')}\n\n"
        f"## Summary\n"
        f"  Proposals applied (last 7d): {proposals_applied}\n"
        f"  Average metric delta: {avg_delta:.2f}\n"
        f"  Open patterns (above threshold): {open_patterns}\n\n"
        f"## Top 3 Recurring Patterns\n"
        f"{top3_section}\n\n"
        f"## Decisions (last 7d)\n"
        f"{decisions_section}\n\n"
        f"## Tracking Measurements (last 7d)\n"
        f"{tracking_section}\n\n"
        f"## Pending Proposals (awaiting Mark)\n"
        f"{pending_section}\n"
    )

    # Post the briefing DM
    if teams is not None:
        try:
            _run_async(teams.post_dm(recipient="mark@gorillacommerce.co", message=report))
        except Exception as exc:
            logger.warning("Failed to post retrospective DM: %s", exc)

    logger.info("Retrospective generated: %d patterns, %d decisions, %d pending",
                len(pattern_counts), len(recent_decisions), len(pending_proposals))

    return report
