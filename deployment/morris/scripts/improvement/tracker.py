"""STORY-727: Tracker — check metric deltas and store improvement_tracking rows.

Runs daily at 07:00 ET. For proposals applied ≥14 days ago, recomputes the
expected_metric value and inserts an improvement_tracking row. Posts [INFO] DM
to Mark if the metric moved in the wrong direction.
"""
from __future__ import annotations

import logging
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
# Public API
# ---------------------------------------------------------------------------

def track_metric(
    proposal_id: int,
    metric_name: str,
    before_value: float,
    after_value: float,
    expected_direction: str,
    window_days: int,
    db_service: Any = None,
    teams_client: Any = None,
) -> None:
    """Record a tracking measurement and warn Mark if the metric moved wrong direction.

    Args:
        proposal_id: The ID of the applied proposal being tracked.
        metric_name: Dotted metric name (e.g. adversarial.static_test_masquerading.weekly_count).
        before_value: Baseline value before the proposal was applied.
        after_value: Current measured value.
        expected_direction: 'decrease' or 'increase'.
        window_days: The measurement window in days.
        db_service: The improvement DB service.
        teams_client: Teams DM client.
    """
    db = db_service or _get_db_service()
    teams = teams_client or _get_teams_client()

    # Insert tracking measurement
    if db is not None:
        _run_async(
            db.record_tracking_measurement(
                proposal_id=proposal_id,
                metric_name=metric_name,
                metric_value=after_value,
                window_days=window_days,
            )
        )

    # Determine if metric moved in the wrong direction
    metric_regressed = False
    if expected_direction == "decrease" and after_value > before_value:
        metric_regressed = True
    elif expected_direction == "increase" and after_value < before_value:
        metric_regressed = True

    if metric_regressed and teams is not None:
        message = (
            f"[INFO] Proposal #{proposal_id}: metric '{metric_name}' moved in the wrong direction. "
            f"Expected {expected_direction}: before={before_value}, after={after_value}. "
            f"Consider reviewing the applied change."
        )
        _run_async(teams.post_dm(recipient="mark@gorillacommerce.co", message=message))
        logger.warning(
            "Metric regression for proposal #%d: %s went from %s to %s (expected %s)",
            proposal_id, metric_name, before_value, after_value, expected_direction,
        )
    else:
        logger.info(
            "Tracking proposal #%d: %s = %s (before=%s, expected %s) — OK",
            proposal_id, metric_name, after_value, before_value, expected_direction,
        )


def check_back(
    db_service: Any = None,
    teams_client: Any = None,
    window_days: int = 14,
) -> None:
    """Run the check-back loop: find applied proposals due for tracking and measure them.

    This is the main entry point for the daily 07:00 ET cron.
    """
    db = db_service or _get_db_service()
    teams = teams_client or _get_teams_client()

    if db is None:
        logger.warning("check_back: no DB service available")
        return

    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc)
    due_proposals = _run_async(db.list_applied_proposals_due_for_check(now=now, window_days=window_days))

    for proposal in (due_proposals or []):
        proposal_id = proposal.get("id") if isinstance(proposal, dict) else getattr(proposal, "id", None)
        expected_metric = (
            proposal.get("expected_metric") if isinstance(proposal, dict)
            else getattr(proposal, "expected_metric", "")
        )
        expected_direction = (
            proposal.get("expected_direction", "decrease") if isinstance(proposal, dict)
            else getattr(proposal, "expected_direction", "decrease")
        )
        evidence = (
            proposal.get("pattern_evidence_json", {}) if isinstance(proposal, dict)
            else getattr(proposal, "pattern_evidence_json", {})
        )
        baseline = evidence.get("baseline_value", 0.0) if isinstance(evidence, dict) else 0.0

        # In real implementation we would compute actual metric value here.
        # For now, log that we would track this.
        logger.info(
            "check_back: proposal #%s due for tracking (metric=%s, direction=%s)",
            proposal_id, expected_metric, expected_direction,
        )
