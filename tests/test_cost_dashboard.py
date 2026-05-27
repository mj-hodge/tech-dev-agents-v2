"""Tests for STORY-012: Agent Cost & Usage Dashboard.

Phase 7 test design — 21 tests covering all 5 success criteria.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tech_dev_agents.cost_dashboard import (
    AgentActivityStatus,
    AgentStatus,
    AlertThreshold,
    CostAlert,
    CostDashboardError,
    CostEvent,
    CostSource,
    InvalidCostEventError,
    InvalidThresholdError,
    UsageMetrics,
    aggregate_usage,
    build_agent_status,
    build_cost_event,
    classify_agent_status,
    evaluate_alerts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_iso(minutes_ago: int = 0) -> str:
    """Return an ISO 8601 UTC timestamp, optionally offset by minutes_ago."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _make_event(
    agent: str = "hermes",
    source: CostSource = CostSource.CLAUDE_SDK,
    amount: float = 0.05,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    session_id: str | None = "sess-001",
    timestamp: str | None = None,
) -> CostEvent:
    return build_cost_event(
        agent_name=agent,
        source=source,
        amount_usd=amount,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        session_id=session_id,
        timestamp=timestamp or _utc_iso(),
    )


# ---------------------------------------------------------------------------
# Group 1 — Data Models (T01, T20)
# ---------------------------------------------------------------------------


class TestCostEventConstruction:
    """T01, T20: CostEvent creation and immutability."""

    def test_cost_event_valid_construction(self) -> None:
        """T01: CostEvent with valid data returns a populated instance."""
        event = _make_event()
        assert event.agent_name == "hermes"
        assert event.source == CostSource.CLAUDE_SDK
        assert event.amount_usd == 0.05
        assert event.input_tokens == 1000
        assert event.output_tokens == 500
        assert event.session_id == "sess-001"
        assert event.timestamp  # non-empty

    def test_cost_event_and_agent_status_are_frozen(self) -> None:
        """T20: CostEvent and AgentStatus are immutable."""
        event = _make_event()
        with pytest.raises(AttributeError):
            event.amount_usd = 999  # type: ignore[misc]

        status = build_agent_status(
            agent_name="hermes",
            last_activity=_utc_iso(),
            current_story=None,
            phases_completed_today=0,
        )
        with pytest.raises(AttributeError):
            status.status = AgentActivityStatus.OFFLINE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Group 2 — Validation (T02, T03, T04, T14, T15)
# ---------------------------------------------------------------------------


class TestCostEventValidation:
    """T02-T04: CostEvent input validation."""

    def test_rejects_negative_amount(self) -> None:
        """T02: Negative amount_usd raises InvalidCostEventError."""
        with pytest.raises(InvalidCostEventError, match="amount_usd"):
            _make_event(amount=-0.01)

    def test_rejects_negative_tokens(self) -> None:
        """T03: Negative token counts raise InvalidCostEventError."""
        with pytest.raises(InvalidCostEventError, match="input_tokens"):
            _make_event(input_tokens=-1)
        with pytest.raises(InvalidCostEventError, match="output_tokens"):
            _make_event(output_tokens=-1)

    def test_rejects_empty_agent_name(self) -> None:
        """T04: Empty or whitespace agent_name raises InvalidCostEventError."""
        with pytest.raises(InvalidCostEventError, match="agent_name"):
            _make_event(agent="")
        with pytest.raises(InvalidCostEventError, match="agent_name"):
            _make_event(agent="   ")


class TestAlertThresholdValidation:
    """T14-T15: AlertThreshold validation."""

    def test_rejects_non_positive_limits(self) -> None:
        """T14: Zero or negative limits raise InvalidThresholdError."""
        with pytest.raises(InvalidThresholdError, match="daily_limit_usd"):
            AlertThreshold(agent_name="hermes", daily_limit_usd=0.0, weekly_limit_usd=10.0)
        with pytest.raises(InvalidThresholdError, match="weekly_limit_usd"):
            AlertThreshold(agent_name="hermes", daily_limit_usd=5.0, weekly_limit_usd=0.0)

    def test_rejects_weekly_less_than_daily(self) -> None:
        """T15: weekly_limit_usd < daily_limit_usd raises InvalidThresholdError."""
        with pytest.raises(InvalidThresholdError, match="weekly_limit_usd"):
            AlertThreshold(agent_name="hermes", daily_limit_usd=10.0, weekly_limit_usd=5.0)


# ---------------------------------------------------------------------------
# Group 3 — Aggregation (T05-T09)
# ---------------------------------------------------------------------------


class TestUsageAggregation:
    """T05-T09: aggregate_usage function."""

    def test_aggregate_single_agent_multiple_events(self) -> None:
        """T05: Multiple events for one agent are summed correctly."""
        now = _utc_iso()
        events = [
            _make_event(amount=0.10, input_tokens=1000, output_tokens=500, timestamp=now),
            _make_event(amount=0.20, input_tokens=2000, output_tokens=1000, timestamp=now),
        ]
        start = _utc_iso(minutes_ago=60)
        metrics = aggregate_usage(events, agent_name="hermes", period_start=start, period_end=now)
        assert metrics.total_sessions == 2
        assert metrics.total_input_tokens == 3000
        assert metrics.total_output_tokens == 1500
        assert abs(metrics.total_cost_usd - 0.30) < 1e-9

    def test_aggregate_filters_by_agent_name(self) -> None:
        """T06: Only events matching the requested agent are included."""
        now = _utc_iso()
        start = _utc_iso(minutes_ago=60)
        events = [
            _make_event(agent="hermes", amount=0.10, timestamp=now),
            _make_event(agent="athena", amount=0.50, timestamp=now),
        ]
        metrics = aggregate_usage(events, agent_name="hermes", period_start=start, period_end=now)
        assert metrics.total_sessions == 1
        assert abs(metrics.total_cost_usd - 0.10) < 1e-9

    def test_aggregate_filters_by_time_period(self) -> None:
        """T07: Events outside the period are excluded."""
        now = _utc_iso()
        old = _utc_iso(minutes_ago=120)
        start = _utc_iso(minutes_ago=60)
        events = [
            _make_event(amount=0.10, timestamp=now),   # inside
            _make_event(amount=0.50, timestamp=old),   # outside
        ]
        metrics = aggregate_usage(events, agent_name="hermes", period_start=start, period_end=now)
        assert metrics.total_sessions == 1
        assert abs(metrics.total_cost_usd - 0.10) < 1e-9

    def test_aggregate_empty_events_returns_zeros(self) -> None:
        """T08: No matching events produce zero-valued metrics."""
        now = _utc_iso()
        start = _utc_iso(minutes_ago=60)
        metrics = aggregate_usage([], agent_name="hermes", period_start=start, period_end=now)
        assert metrics.total_sessions == 0
        assert metrics.total_input_tokens == 0
        assert metrics.total_output_tokens == 0
        assert metrics.total_cost_usd == 0.0
        assert metrics.cost_by_source == {}

    def test_aggregate_cost_by_source_breakdown(self) -> None:
        """T09: cost_by_source groups totals by CostSource."""
        now = _utc_iso()
        start = _utc_iso(minutes_ago=60)
        events = [
            _make_event(source=CostSource.CLAUDE_SDK, amount=0.10, timestamp=now),
            _make_event(source=CostSource.AZURE_FOUNDRY, amount=0.30, timestamp=now),
            _make_event(source=CostSource.CLAUDE_SDK, amount=0.05, timestamp=now),
        ]
        metrics = aggregate_usage(events, agent_name="hermes", period_start=start, period_end=now)
        assert abs(metrics.cost_by_source["claude_sdk"] - 0.15) < 1e-9
        assert abs(metrics.cost_by_source["azure_foundry"] - 0.30) < 1e-9


# ---------------------------------------------------------------------------
# Group 4 — Agent Health (T10-T13, T21)
# ---------------------------------------------------------------------------


class TestAgentHealthClassification:
    """T10-T13: classify_agent_status time-based classification."""

    def test_online_when_recent_activity(self) -> None:
        """T10: Activity within 5 minutes → ONLINE."""
        status = classify_agent_status(_utc_iso(minutes_ago=2))
        assert status == AgentActivityStatus.ONLINE

    def test_idle_when_moderate_gap(self) -> None:
        """T11: Activity 5-60 minutes ago → IDLE."""
        status = classify_agent_status(_utc_iso(minutes_ago=30))
        assert status == AgentActivityStatus.IDLE

    def test_stuck_when_long_gap(self) -> None:
        """T12: Activity 60-480 minutes ago → STUCK."""
        status = classify_agent_status(_utc_iso(minutes_ago=120))
        assert status == AgentActivityStatus.STUCK

    def test_boundary_at_exactly_5_minutes(self) -> None:
        """Boundary: exactly 5 min → ONLINE (<=5 is ONLINE)."""
        fixed_now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        activity = (fixed_now - timedelta(minutes=5)).isoformat()
        with patch("tech_dev_agents.cost_dashboard.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            status = classify_agent_status(activity)
        assert status == AgentActivityStatus.ONLINE

    def test_boundary_at_exactly_60_minutes(self) -> None:
        """Boundary: exactly 60 min → IDLE (<=stuck_threshold is IDLE)."""
        fixed_now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        activity = (fixed_now - timedelta(minutes=60)).isoformat()
        with patch("tech_dev_agents.cost_dashboard.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            status = classify_agent_status(activity)
        assert status == AgentActivityStatus.IDLE

    def test_boundary_at_exactly_480_minutes(self) -> None:
        """Boundary: exactly 480 min → STUCK (<=offline_threshold is STUCK)."""
        fixed_now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        activity = (fixed_now - timedelta(minutes=480)).isoformat()
        with patch("tech_dev_agents.cost_dashboard.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            status = classify_agent_status(activity)
        assert status == AgentActivityStatus.STUCK

    def test_offline_when_very_long_gap_or_none(self) -> None:
        """T13: Activity >480 minutes ago or None → OFFLINE."""
        status = classify_agent_status(_utc_iso(minutes_ago=600))
        assert status == AgentActivityStatus.OFFLINE

        status_none = classify_agent_status(None)
        assert status_none == AgentActivityStatus.OFFLINE


class TestBuildAgentStatus:
    """T21: build_agent_status factory."""

    def test_build_agent_status_composes_correctly(self) -> None:
        """T21: Factory returns AgentStatus with derived status field."""
        status = build_agent_status(
            agent_name="hermes",
            last_activity=_utc_iso(minutes_ago=2),
            current_story="STORY-012",
            phases_completed_today=3,
        )
        assert status.agent_name == "hermes"
        assert status.status == AgentActivityStatus.ONLINE
        assert status.current_story == "STORY-012"
        assert status.phases_completed_today == 3


# ---------------------------------------------------------------------------
# Group 5 — Alerts (T16-T19)
# ---------------------------------------------------------------------------


class TestAlertEvaluation:
    """T16-T19: evaluate_alerts threshold checking."""

    def test_triggers_when_daily_cost_exceeds_threshold(self) -> None:
        """T16: Alert fires when daily spend exceeds daily_limit_usd."""
        now = _utc_iso()
        events = [_make_event(amount=6.0, timestamp=now)]
        thresholds = [AlertThreshold(agent_name="hermes", daily_limit_usd=5.0, weekly_limit_usd=25.0)]
        alerts = evaluate_alerts(events, thresholds, now=now)
        assert len(alerts) >= 1
        daily_alerts = [a for a in alerts if a.period == "daily"]
        assert len(daily_alerts) == 1
        assert daily_alerts[0].actual_usd >= 6.0
        assert daily_alerts[0].threshold_usd == 5.0

    def test_no_alert_when_under_threshold(self) -> None:
        """T17: No alert when spend is under both thresholds."""
        now = _utc_iso()
        events = [_make_event(amount=1.0, timestamp=now)]
        thresholds = [AlertThreshold(agent_name="hermes", daily_limit_usd=5.0, weekly_limit_usd=25.0)]
        alerts = evaluate_alerts(events, thresholds, now=now)
        assert len(alerts) == 0

    def test_wildcard_threshold_applies_to_all_agents(self) -> None:
        """T18: A threshold with agent_name='*' applies to any agent."""
        now = _utc_iso()
        events = [_make_event(agent="athena", amount=11.0, timestamp=now)]
        thresholds = [AlertThreshold(agent_name="*", daily_limit_usd=10.0, weekly_limit_usd=50.0)]
        alerts = evaluate_alerts(events, thresholds, now=now)
        assert len(alerts) >= 1
        assert alerts[0].agent_name == "athena"

    def test_agent_specific_threshold_overrides_wildcard(self) -> None:
        """T19: Agent-specific threshold takes priority over wildcard."""
        now = _utc_iso()
        events = [_make_event(agent="hermes", amount=8.0, timestamp=now)]
        thresholds = [
            AlertThreshold(agent_name="*", daily_limit_usd=5.0, weekly_limit_usd=25.0),
            AlertThreshold(agent_name="hermes", daily_limit_usd=10.0, weekly_limit_usd=50.0),
        ]
        alerts = evaluate_alerts(events, thresholds, now=now)
        # hermes-specific limit is 10.0, spend is 8.0 → no daily alert
        daily_alerts = [a for a in alerts if a.period == "daily"]
        assert len(daily_alerts) == 0
