"""Agent management dashboard data models and business logic.

STORY-014: Agent Management Dashboard
Phase 8 — Implementation

Pure data models, factory functions, and health evaluation for agent
monitoring, restart management, session tracking, and alerting.
Designed for Loki ingestion and Grafana visualization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AbstractSet, Callable, Sequence


# Import shared enum and thresholds from cost_dashboard (single source of truth)
from tech_dev_agents.cost_dashboard import (
    AgentActivityStatus,
    DEFAULT_OFFLINE_THRESHOLD_MINUTES,
    DEFAULT_STUCK_THRESHOLD_MINUTES,
    classify_agent_status,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentDashboardError(Exception):
    """Base error for agent dashboard operations."""


class AgentNotFoundError(AgentDashboardError):
    """Raised when an agent is not found in the registry."""


class RestartError(AgentDashboardError):
    """Raised when a restart operation fails."""


class SessionError(AgentDashboardError):
    """Raised when a session operation fails."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """Registry entry for a known agent."""

    name: str
    host: str
    port: int
    role: str  # e.g. "developer", "reviewer", "sre"
    enabled: bool = True
    loki_label: str | None = None


@dataclass(frozen=True, slots=True)
class AgentHealthSnapshot:
    """Point-in-time health observation for one agent."""

    agent_name: str
    status: AgentActivityStatus
    last_activity: str | None  # ISO 8601 UTC
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str  # ISO 8601 UTC


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Active session on an agent."""

    session_id: str
    agent_name: str
    started_at: str  # ISO 8601 UTC
    last_active: str  # ISO 8601 UTC
    story_id: str | None
    phase: str | None
    is_stuck: bool


@dataclass(frozen=True, slots=True)
class RestartCommand:
    """Validated restart request with audit trail."""

    agent_name: str
    reason: str
    requested_by: str
    requested_at: str  # ISO 8601 UTC
    force: bool = False


@dataclass(frozen=True, slots=True)
class RestartResult:
    """Outcome of a restart attempt."""

    agent_name: str
    success: bool
    message: str
    previous_status: AgentActivityStatus
    new_status: AgentActivityStatus | None
    completed_at: str  # ISO 8601 UTC


@dataclass(frozen=True, slots=True)
class DashboardAlert:
    """Health-based alert for agent monitoring."""

    agent_name: str
    alert_type: str  # "offline", "stuck", "high_errors", "session_stuck"
    severity: str  # "warning", "critical"
    message: str
    triggered_at: str
    cooldown_key: str  # dedup key: "{agent}:{alert_type}"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_STUCK_SESSION_THRESHOLD_MINUTES = 30
_DEFAULT_ERROR_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------


def _validate_agent_name(agent_name: str, context: str = "agent_name") -> str:
    """Validate and strip agent name. Raises AgentDashboardError if empty."""
    if not agent_name or not agent_name.strip():
        raise AgentDashboardError(f"{context} must be a non-empty string")
    return agent_name.strip()


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Factory Functions
# ---------------------------------------------------------------------------


def build_agent_record(
    name: str,
    host: str,
    port: int,
    role: str,
    enabled: bool = True,
    loki_label: str | None = None,
) -> AgentRecord:
    """Create a validated AgentRecord."""
    validated_name = _validate_agent_name(name, "name")
    return AgentRecord(
        name=validated_name,
        host=host,
        port=port,
        role=role,
        enabled=enabled,
        loki_label=loki_label,
    )


def build_health_snapshot(
    agent_name: str,
    last_activity: str | None,
    uptime_seconds: int,
    active_sessions: int,
    error_count: int,
) -> AgentHealthSnapshot:
    """Create a health snapshot with derived status classification."""
    validated_name = _validate_agent_name(agent_name)
    status = classify_agent_status(last_activity)
    return AgentHealthSnapshot(
        agent_name=validated_name,
        status=status,
        last_activity=last_activity,
        uptime_seconds=uptime_seconds,
        active_sessions=active_sessions,
        error_count=error_count,
        checked_at=_now_iso(),
    )


def build_session_info(
    session_id: str,
    agent_name: str,
    started_at: str,
    last_active: str,
    story_id: str | None = None,
    phase: str | None = None,
    stuck_threshold_minutes: int = _DEFAULT_STUCK_SESSION_THRESHOLD_MINUTES,
) -> SessionInfo:
    """Create a SessionInfo with derived is_stuck classification."""
    validated_name = _validate_agent_name(agent_name)

    # Determine if session is stuck based on last_active vs threshold
    is_stuck = False
    try:
        last_dt = datetime.fromisoformat(last_active)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - last_dt).total_seconds() / 60.0
        is_stuck = elapsed_minutes > stuck_threshold_minutes
    except (ValueError, TypeError):
        is_stuck = True  # Can't parse → assume stuck

    return SessionInfo(
        session_id=session_id,
        agent_name=validated_name,
        started_at=started_at,
        last_active=last_active,
        story_id=story_id,
        phase=phase,
        is_stuck=is_stuck,
    )


def build_restart_command(
    agent_name: str,
    reason: str,
    requested_by: str,
    force: bool = False,
) -> RestartCommand:
    """Create a validated restart command with timestamp."""
    validated_name = _validate_agent_name(agent_name)
    return RestartCommand(
        agent_name=validated_name,
        reason=reason,
        requested_by=requested_by,
        requested_at=_now_iso(),
        force=force,
    )


def build_restart_result(
    agent_name: str,
    success: bool,
    message: str,
    previous_status: AgentActivityStatus,
    new_status: AgentActivityStatus | None = None,
) -> RestartResult:
    """Create a restart result with completion timestamp."""
    return RestartResult(
        agent_name=agent_name,
        success=success,
        message=message,
        previous_status=previous_status,
        new_status=new_status,
        completed_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Business Logic
# ---------------------------------------------------------------------------


def collect_dashboard_health(
    agents: Sequence[AgentRecord],
    health_fetcher: Callable[[AgentRecord], AgentHealthSnapshot],
) -> list[AgentHealthSnapshot]:
    """Collect health snapshots from all registered agents.

    The health_fetcher callable is injected to decouple from HTTP transport.
    """
    results: list[AgentHealthSnapshot] = []
    for agent in agents:
        if not agent.enabled:
            continue
        try:
            snapshot = health_fetcher(agent)
            results.append(snapshot)
        except (OSError, TimeoutError, ConnectionError):
            # Agent unreachable — record as offline
            results.append(
                AgentHealthSnapshot(
                    agent_name=agent.name,
                    status=AgentActivityStatus.OFFLINE,
                    last_activity=None,
                    uptime_seconds=0,
                    active_sessions=0,
                    error_count=0,
                    checked_at=_now_iso(),
                )
            )
    return results


def find_stuck_sessions(sessions: Sequence[SessionInfo]) -> list[SessionInfo]:
    """Filter and return only stuck sessions."""
    return [s for s in sessions if s.is_stuck]


def validate_restart(
    command: RestartCommand,
    agents: Sequence[AgentRecord],
    current_health: AgentHealthSnapshot | None,
) -> RestartCommand:
    """Validate that a restart command targets a known agent.

    Raises AgentNotFoundError if the agent is not in the registry.
    Returns the command unchanged if valid.
    """
    known_names = {agent.name for agent in agents}
    if command.agent_name not in known_names:
        raise AgentNotFoundError(
            f"Agent '{command.agent_name}' not found in registry. "
            f"Known agents: {', '.join(sorted(known_names))}"
        )
    return command


def evaluate_health_alerts(
    snapshots: Sequence[AgentHealthSnapshot],
    *,
    stuck_threshold_minutes: int = DEFAULT_STUCK_THRESHOLD_MINUTES,
    offline_threshold_minutes: int = DEFAULT_OFFLINE_THRESHOLD_MINUTES,
    error_threshold: int = _DEFAULT_ERROR_THRESHOLD,
    cooldown_keys: AbstractSet[str] = frozenset(),
) -> list[DashboardAlert]:
    """Evaluate health snapshots and generate alerts.

    Returns alerts for:
    - Offline agents (classify_agent_status → OFFLINE)
    - Stuck agents (classify_agent_status → STUCK)
    - High error counts (above error_threshold)

    Status classification is delegated to classify_agent_status (from
    cost_dashboard) so both modules share the same threshold boundaries.

    Alerts with cooldown_key in cooldown_keys are suppressed.
    """
    now_ts = _now_iso()
    alerts: list[DashboardAlert] = []

    for snapshot in snapshots:
        # Delegate status classification to the single source of truth
        status = classify_agent_status(
            snapshot.last_activity,
            stuck_threshold_minutes=stuck_threshold_minutes,
            offline_threshold_minutes=offline_threshold_minutes,
        )

        if status == AgentActivityStatus.OFFLINE:
            key = f"{snapshot.agent_name}:offline"
            if key not in cooldown_keys:
                alerts.append(
                    DashboardAlert(
                        agent_name=snapshot.agent_name,
                        alert_type="offline",
                        severity="critical",
                        message=f"Agent '{snapshot.agent_name}' is offline (no activity recorded)",
                        triggered_at=now_ts,
                        cooldown_key=key,
                    )
                )
        elif status == AgentActivityStatus.STUCK:
            key = f"{snapshot.agent_name}:stuck"
            if key not in cooldown_keys:
                # Compute elapsed for the alert message
                elapsed_minutes = 0.0
                if snapshot.last_activity is not None:
                    try:
                        last_dt = datetime.fromisoformat(snapshot.last_activity)
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        elapsed_minutes = (
                            datetime.now(timezone.utc) - last_dt
                        ).total_seconds() / 60.0
                    except (ValueError, TypeError):
                        pass
                alerts.append(
                    DashboardAlert(
                        agent_name=snapshot.agent_name,
                        alert_type="stuck",
                        severity="critical",
                        message=(
                            f"Agent '{snapshot.agent_name}' has been inactive for "
                            f"{elapsed_minutes:.0f} minutes (threshold: {stuck_threshold_minutes})"
                        ),
                        triggered_at=now_ts,
                        cooldown_key=key,
                    )
                )

        # Check for high error count
        if snapshot.error_count > error_threshold:
            key = f"{snapshot.agent_name}:high_errors"
            if key not in cooldown_keys:
                alerts.append(
                    DashboardAlert(
                        agent_name=snapshot.agent_name,
                        alert_type="high_errors",
                        severity="warning",
                        message=(
                            f"Agent '{snapshot.agent_name}' has {snapshot.error_count} errors "
                            f"(threshold: {error_threshold})"
                        ),
                        triggered_at=now_ts,
                        cooldown_key=key,
                    )
                )

    return alerts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AgentDashboardError",
    "AgentHealthSnapshot",
    "AgentNotFoundError",
    "AgentRecord",
    "DashboardAlert",
    "RestartCommand",
    "RestartError",
    "RestartResult",
    "SessionError",
    "SessionInfo",
    "build_agent_record",
    "build_health_snapshot",
    "build_restart_command",
    "build_restart_result",
    "build_session_info",
    "collect_dashboard_health",
    "evaluate_health_alerts",
    "find_stuck_sessions",
    "validate_restart",
]
