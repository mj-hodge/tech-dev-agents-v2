"""Tests for STORY-014: Agent Management Dashboard.

Phase 7 test design — 20 tests covering all 6 success criteria.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tech_dev_agents.agent_dashboard import (
    AgentDashboardError,
    AgentNotFoundError,
    AgentRecord,
    AgentHealthSnapshot,
    DashboardAlert,
    RestartCommand,
    RestartError,
    RestartResult,
    SessionError,
    SessionInfo,
    build_agent_record,
    build_health_snapshot,
    build_restart_command,
    build_restart_result,
    build_session_info,
    collect_dashboard_health,
    evaluate_health_alerts,
    find_stuck_sessions,
    validate_restart,
)
from tech_dev_agents.cost_dashboard import AgentActivityStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso(minutes_ago: int = 0) -> str:
    """Return an ISO 8601 UTC timestamp, optionally offset by minutes_ago."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _make_agent(
    name: str = "hermes",
    host: str = "10.0.0.1",
    port: int = 8642,
    role: str = "developer",
    enabled: bool = True,
) -> AgentRecord:
    return build_agent_record(name=name, host=host, port=port, role=role, enabled=enabled)


_SENTINEL = object()
_OFFLINE = object()  # Explicit sentinel for None last_activity (agent offline)


def _make_snapshot(
    agent_name: str = "hermes",
    last_activity: str | object = _SENTINEL,
    uptime_seconds: int = 3600,
    active_sessions: int = 1,
    error_count: int = 0,
) -> AgentHealthSnapshot:
    if last_activity is None:
        raise TypeError(
            "Bare None passed to _make_snapshot; use _OFFLINE sentinel for "
            "explicit offline status, or omit for the default (2 min ago)."
        )
    if last_activity is _OFFLINE:
        resolved_activity = None
    elif last_activity is _SENTINEL:
        resolved_activity = _utc_iso(minutes_ago=2)
    else:
        resolved_activity = last_activity  # type: ignore[assignment]
    return build_health_snapshot(
        agent_name=agent_name,
        last_activity=resolved_activity,
        uptime_seconds=uptime_seconds,
        active_sessions=active_sessions,
        error_count=error_count,
    )


# ---------------------------------------------------------------------------
# Group 1 — Restart (T01-T04)  SC-1
# ---------------------------------------------------------------------------


class TestRestartCommand:
    """T01-T04: Restart command creation, validation, and result."""

    def test_t01_valid_restart_command_builds(self) -> None:
        """T01: Valid restart command builds correctly."""
        cmd = build_restart_command(
            agent_name="hermes",
            reason="stuck on STORY-012",
            requested_by="mark",
        )
        assert cmd.agent_name == "hermes"
        assert cmd.reason == "stuck on STORY-012"
        assert cmd.requested_by == "mark"
        assert cmd.force is False
        assert cmd.requested_at  # non-empty timestamp

    def test_t02_validate_restart_passes_for_known_agent(self) -> None:
        """T02: validate_restart succeeds when agent exists in registry."""
        agents = [_make_agent(name="hermes"), _make_agent(name="athena")]
        cmd = build_restart_command(
            agent_name="hermes", reason="test", requested_by="mark"
        )
        health = _make_snapshot(agent_name="hermes")
        validated = validate_restart(cmd, agents=agents, current_health=health)
        assert validated.agent_name == "hermes"

    def test_t03_validate_restart_rejects_unknown_agent(self) -> None:
        """T03: validate_restart raises AgentNotFoundError for unknown agent."""
        agents = [_make_agent(name="hermes")]
        cmd = build_restart_command(
            agent_name="unknown-agent", reason="test", requested_by="mark"
        )
        with pytest.raises(AgentNotFoundError, match="unknown-agent"):
            validate_restart(cmd, agents=agents, current_health=None)

    def test_t04_restart_result_captures_outcome(self) -> None:
        """T04: RestartResult captures success/failure and status transition."""
        result = build_restart_result(
            agent_name="hermes",
            success=True,
            message="Agent restarted successfully",
            previous_status=AgentActivityStatus.STUCK,
            new_status=AgentActivityStatus.ONLINE,
        )
        assert result.success is True
        assert result.previous_status == AgentActivityStatus.STUCK
        assert result.new_status == AgentActivityStatus.ONLINE
        assert result.completed_at  # non-empty


# ---------------------------------------------------------------------------
# Group 2 — Auto-Restart Alerts (T05-T07)  SC-2
# ---------------------------------------------------------------------------


class TestAutoRestartAlerts:
    """T05-T07: Stuck/offline detection triggers alerts for auto-restart."""

    def test_t05_stuck_agent_triggers_critical_alert(self) -> None:
        """T05: Agent stuck >60 min triggers critical alert."""
        snapshot = _make_snapshot(
            agent_name="hermes",
            last_activity=_utc_iso(minutes_ago=65),
        )
        alerts = evaluate_health_alerts([snapshot], stuck_threshold_minutes=60)
        assert len(alerts) >= 1
        alert = alerts[0]
        assert alert.agent_name == "hermes"
        assert alert.severity == "critical"
        assert alert.alert_type in ("stuck", "offline")

    def test_t06_offline_agent_triggers_critical_alert(self) -> None:
        """T06: Agent with no activity triggers critical alert."""
        snapshot = build_health_snapshot(
            agent_name="hermes",
            last_activity=None,
            uptime_seconds=0,
            active_sessions=0,
            error_count=0,
        )
        alerts = evaluate_health_alerts([snapshot], stuck_threshold_minutes=60)
        assert len(alerts) >= 1
        assert alerts[0].severity == "critical"
        assert alerts[0].alert_type == "offline"

    def test_t07_online_agent_triggers_no_alert(self) -> None:
        """T07: Healthy online agent triggers no alerts."""
        snapshot = _make_snapshot(
            agent_name="hermes",
            last_activity=_utc_iso(minutes_ago=2),
            error_count=0,
        )
        alerts = evaluate_health_alerts([snapshot])
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Group 3 — Health Dashboard (T08-T11)  SC-3
# ---------------------------------------------------------------------------


class TestHealthDashboard:
    """T08-T11: Health snapshot creation and collection."""

    def test_t08_health_snapshot_correct_status(self) -> None:
        """T08: Health snapshot derives correct status from last_activity."""
        snapshot = _make_snapshot(last_activity=_utc_iso(minutes_ago=2))
        assert snapshot.status == AgentActivityStatus.ONLINE

        snapshot_stuck = _make_snapshot(last_activity=_utc_iso(minutes_ago=120))
        assert snapshot_stuck.status == AgentActivityStatus.STUCK

    def test_t09_collect_health_from_multiple_agents(self) -> None:
        """T09: collect_dashboard_health gathers snapshots from all agents."""
        agents = [
            _make_agent(name="hermes"),
            _make_agent(name="athena"),
        ]

        def mock_fetcher(agent: AgentRecord) -> AgentHealthSnapshot:
            return _make_snapshot(agent_name=agent.name)

        results = collect_dashboard_health(agents, health_fetcher=mock_fetcher)
        assert len(results) == 2
        names = {s.agent_name for s in results}
        assert names == {"hermes", "athena"}

    def test_t10_health_snapshot_zero_uptime(self) -> None:
        """T10: Snapshot with zero uptime is valid."""
        snapshot = _make_snapshot(uptime_seconds=0)
        assert snapshot.uptime_seconds == 0

    def test_t11_error_count_in_snapshot(self) -> None:
        """T11: Error count is captured in snapshot."""
        snapshot = _make_snapshot(error_count=42)
        assert snapshot.error_count == 42


# ---------------------------------------------------------------------------
# Group 4 — Session Management (T12-T15)  SC-4
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """T12-T15: Session info and stuck session detection."""

    def test_t12_session_info_built_correctly(self) -> None:
        """T12: SessionInfo built with all fields populated."""
        session = build_session_info(
            session_id="sess-001",
            agent_name="hermes",
            started_at=_utc_iso(minutes_ago=60),
            last_active=_utc_iso(minutes_ago=5),
            story_id="STORY-014",
            phase="8",
        )
        assert session.session_id == "sess-001"
        assert session.agent_name == "hermes"
        assert session.story_id == "STORY-014"
        assert session.is_stuck is False

    def test_t13_stuck_session_detected_by_threshold(self) -> None:
        """T13: Session inactive beyond threshold is marked stuck."""
        session = build_session_info(
            session_id="sess-002",
            agent_name="hermes",
            started_at=_utc_iso(minutes_ago=120),
            last_active=_utc_iso(minutes_ago=45),
            stuck_threshold_minutes=30,
        )
        assert session.is_stuck is True

    def test_t14_recent_session_not_stuck(self) -> None:
        """T14: Recently active session is not stuck."""
        session = build_session_info(
            session_id="sess-003",
            agent_name="hermes",
            started_at=_utc_iso(minutes_ago=30),
            last_active=_utc_iso(minutes_ago=2),
            stuck_threshold_minutes=30,
        )
        assert session.is_stuck is False

    def test_t15_find_stuck_sessions_filters(self) -> None:
        """T15: find_stuck_sessions returns only stuck sessions."""
        sessions = [
            build_session_info(
                session_id="sess-ok",
                agent_name="hermes",
                started_at=_utc_iso(minutes_ago=30),
                last_active=_utc_iso(minutes_ago=2),
            ),
            build_session_info(
                session_id="sess-stuck",
                agent_name="hermes",
                started_at=_utc_iso(minutes_ago=120),
                last_active=_utc_iso(minutes_ago=45),
                stuck_threshold_minutes=30,
            ),
        ]
        stuck = find_stuck_sessions(sessions)
        assert len(stuck) == 1
        assert stuck[0].session_id == "sess-stuck"


# ---------------------------------------------------------------------------
# Group 5 — Alerts (T16-T18)  SC-6
# ---------------------------------------------------------------------------


class TestHealthAlerts:
    """T16-T18: Alert evaluation with thresholds and cooldown."""

    def test_t16_high_error_count_triggers_warning(self) -> None:
        """T16: Agent with errors above threshold triggers warning alert."""
        snapshot = _make_snapshot(
            agent_name="hermes",
            last_activity=_utc_iso(minutes_ago=2),
            error_count=10,
        )
        alerts = evaluate_health_alerts([snapshot], error_threshold=5)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "high_errors"
        assert alerts[0].severity == "warning"

    def test_t17_cooldown_prevents_duplicate_alert(self) -> None:
        """T17: Alerts with matching cooldown_key are suppressed."""
        snapshot = _make_snapshot(
            agent_name="hermes",
            last_activity=_utc_iso(minutes_ago=2),
            error_count=10,
        )
        # First call generates alert
        alerts = evaluate_health_alerts([snapshot], error_threshold=5)
        assert len(alerts) == 1

        # Second call with cooldown key suppresses it
        cooldown_keys = {alerts[0].cooldown_key}
        alerts2 = evaluate_health_alerts(
            [snapshot], error_threshold=5, cooldown_keys=cooldown_keys
        )
        assert len(alerts2) == 0

    def test_t18_multiple_agents_separate_alerts(self) -> None:
        """T18: Each agent gets its own alert independently."""
        snapshots = [
            _make_snapshot(agent_name="hermes", last_activity=_OFFLINE),
            _make_snapshot(agent_name="athena", last_activity=_OFFLINE),
        ]
        alerts = evaluate_health_alerts(snapshots)
        assert len(alerts) == 2
        assert {a.agent_name for a in alerts} == {"hermes", "athena"}


# ---------------------------------------------------------------------------
# Group 6 — Validation & Immutability (T19-T20)
# ---------------------------------------------------------------------------


class TestValidationAndImmutability:
    """T19-T20: Input validation and frozen dataclass enforcement."""

    def test_t19_empty_agent_name_rejected(self) -> None:
        """T19: Empty agent name raises AgentDashboardError."""
        with pytest.raises(AgentDashboardError):
            build_agent_record(name="", host="10.0.0.1", port=8642, role="dev")
        with pytest.raises(AgentDashboardError):
            build_health_snapshot(
                agent_name="  ", last_activity=_utc_iso(),
                uptime_seconds=0, active_sessions=0, error_count=0,
            )
        with pytest.raises(AgentDashboardError):
            build_session_info(
                session_id="s1", agent_name="", started_at=_utc_iso(),
                last_active=_utc_iso(),
            )

    def test_t20_all_models_are_frozen(self) -> None:
        """T20: All data models are immutable."""
        agent = _make_agent()
        with pytest.raises(AttributeError):
            agent.name = "other"  # type: ignore[misc]

        snapshot = _make_snapshot()
        with pytest.raises(AttributeError):
            snapshot.status = AgentActivityStatus.OFFLINE  # type: ignore[misc]

        session = build_session_info(
            session_id="s1", agent_name="hermes",
            started_at=_utc_iso(), last_active=_utc_iso(),
        )
        with pytest.raises(AttributeError):
            session.is_stuck = True  # type: ignore[misc]

        cmd = build_restart_command(
            agent_name="hermes", reason="test", requested_by="mark",
        )
        with pytest.raises(AttributeError):
            cmd.force = True  # type: ignore[misc]

        result = build_restart_result(
            agent_name="hermes", success=True, message="ok",
            previous_status=AgentActivityStatus.STUCK,
        )
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]
