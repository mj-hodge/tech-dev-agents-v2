"""Agent cost and usage dashboard data models.

STORY-012: Agent Cost & Usage Dashboard
Phase 8 — Implementation

Pure data models and aggregation functions for tracking per-agent costs,
usage metrics, health status, and threshold-based alerting. Designed to
emit structured data for Loki ingestion and Grafana visualization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Sequence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CostDashboardError(Exception):
    """Base error for cost dashboard operations."""


class InvalidCostEventError(CostDashboardError):
    """Raised when cost event data fails validation."""


class InvalidThresholdError(CostDashboardError):
    """Raised when alert threshold configuration is invalid."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CostSource(str, Enum):
    """Origin of a cost observation."""

    CLAUDE_SDK = "claude_sdk"
    AZURE_FOUNDRY = "azure_foundry"


class AgentActivityStatus(str, Enum):
    """Health/activity classification for an agent."""

    ONLINE = "online"
    IDLE = "idle"
    STUCK = "stuck"
    OFFLINE = "offline"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

# Default thresholds for activity status classification (minutes).
# Public so agent_dashboard can reference the same single source of truth.
ONLINE_THRESHOLD_MINUTES = 5
DEFAULT_STUCK_THRESHOLD_MINUTES = 60
DEFAULT_OFFLINE_THRESHOLD_MINUTES = 480

# Keep private aliases for internal backward-compat references.
_ONLINE_THRESHOLD_MINUTES = ONLINE_THRESHOLD_MINUTES
_DEFAULT_STUCK_THRESHOLD_MINUTES = DEFAULT_STUCK_THRESHOLD_MINUTES
_DEFAULT_OFFLINE_THRESHOLD_MINUTES = DEFAULT_OFFLINE_THRESHOLD_MINUTES


@dataclass(frozen=True, slots=True)
class CostEvent:
    """Single cost observation tied to an agent and source."""

    agent_name: str
    source: CostSource
    amount_usd: float
    input_tokens: int
    output_tokens: int
    session_id: str | None
    timestamp: str  # ISO 8601 UTC


@dataclass(frozen=True, slots=True)
class UsageMetrics:
    """Aggregated usage for an agent over a time period."""

    agent_name: str
    period_start: str
    period_end: str
    total_sessions: int
    total_turns: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    cost_by_source: dict[str, float]


@dataclass(frozen=True, slots=True)
class AgentStatus:
    """Health and activity status for an agent."""

    agent_name: str
    status: AgentActivityStatus
    last_activity: str | None
    current_story: str | None
    phases_completed_today: int


@dataclass(frozen=True, slots=True)
class CostAlert:
    """Threshold breach notification."""

    agent_name: str
    threshold_usd: float
    actual_usd: float
    period: str  # "daily" or "weekly"
    triggered_at: str
    message: str


class AlertThreshold:
    """Cost alert threshold configuration for an agent.

    Uses __init__ with validation instead of frozen dataclass so we can
    raise InvalidThresholdError with descriptive messages.
    """

    __slots__ = ("agent_name", "daily_limit_usd", "weekly_limit_usd")

    def __init__(
        self,
        agent_name: str,
        daily_limit_usd: float,
        weekly_limit_usd: float,
    ) -> None:
        if daily_limit_usd <= 0:
            raise InvalidThresholdError(
                f"daily_limit_usd must be positive, got {daily_limit_usd}"
            )
        if weekly_limit_usd <= 0:
            raise InvalidThresholdError(
                f"weekly_limit_usd must be positive, got {weekly_limit_usd}"
            )
        if weekly_limit_usd < daily_limit_usd:
            raise InvalidThresholdError(
                f"weekly_limit_usd ({weekly_limit_usd}) must be >= daily_limit_usd ({daily_limit_usd})"
            )
        object.__setattr__(self, "agent_name", agent_name)
        object.__setattr__(self, "daily_limit_usd", daily_limit_usd)
        object.__setattr__(self, "weekly_limit_usd", weekly_limit_usd)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"Cannot set attribute '{name}' on frozen AlertThreshold")

    def __repr__(self) -> str:
        return (
            f"AlertThreshold(agent_name={self.agent_name!r}, "
            f"daily_limit_usd={self.daily_limit_usd}, "
            f"weekly_limit_usd={self.weekly_limit_usd})"
        )


# ---------------------------------------------------------------------------
# Factory Functions
# ---------------------------------------------------------------------------


def build_cost_event(
    agent_name: str,
    source: CostSource,
    amount_usd: float,
    input_tokens: int,
    output_tokens: int,
    session_id: str | None = None,
    timestamp: str | None = None,
) -> CostEvent:
    """Create a validated CostEvent.

    Raises InvalidCostEventError for invalid inputs.
    """
    if not agent_name or not agent_name.strip():
        raise InvalidCostEventError("agent_name must be a non-empty string")
    if amount_usd < 0:
        raise InvalidCostEventError(f"amount_usd must be >= 0, got {amount_usd}")
    if input_tokens < 0:
        raise InvalidCostEventError(f"input_tokens must be >= 0, got {input_tokens}")
    if output_tokens < 0:
        raise InvalidCostEventError(f"output_tokens must be >= 0, got {output_tokens}")

    ts = timestamp or datetime.now(timezone.utc).isoformat()

    return CostEvent(
        agent_name=agent_name.strip(),
        source=source,
        amount_usd=amount_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        session_id=session_id,
        timestamp=ts,
    )


def build_agent_status(
    agent_name: str,
    last_activity: str | None,
    current_story: str | None,
    phases_completed_today: int,
    stuck_threshold_minutes: int = _DEFAULT_STUCK_THRESHOLD_MINUTES,
    offline_threshold_minutes: int = _DEFAULT_OFFLINE_THRESHOLD_MINUTES,
) -> AgentStatus:
    """Create an AgentStatus with derived activity classification."""
    status = classify_agent_status(
        last_activity,
        stuck_threshold_minutes=stuck_threshold_minutes,
        offline_threshold_minutes=offline_threshold_minutes,
    )
    return AgentStatus(
        agent_name=agent_name,
        status=status,
        last_activity=last_activity,
        current_story=current_story,
        phases_completed_today=phases_completed_today,
    )


# ---------------------------------------------------------------------------
# Classification & Aggregation
# ---------------------------------------------------------------------------


def classify_agent_status(
    last_activity: str | None,
    stuck_threshold_minutes: int = _DEFAULT_STUCK_THRESHOLD_MINUTES,
    offline_threshold_minutes: int = _DEFAULT_OFFLINE_THRESHOLD_MINUTES,
) -> AgentActivityStatus:
    """Classify agent activity status based on last activity timestamp.

    - ≤ 5 min → ONLINE
    - > 5 min and ≤ stuck_threshold (default 60 min) → IDLE
    - > stuck_threshold and ≤ offline_threshold (default 480 min) → STUCK
    - > offline_threshold or None → OFFLINE
    """
    if last_activity is None:
        return AgentActivityStatus.OFFLINE

    try:
        last_dt = datetime.fromisoformat(last_activity)
    except (ValueError, TypeError):
        return AgentActivityStatus.OFFLINE

    # Ensure timezone-aware comparison
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    elapsed = now - last_dt
    elapsed_minutes = elapsed.total_seconds() / 60.0

    if elapsed_minutes <= _ONLINE_THRESHOLD_MINUTES:
        return AgentActivityStatus.ONLINE
    if elapsed_minutes <= stuck_threshold_minutes:
        return AgentActivityStatus.IDLE
    if elapsed_minutes <= offline_threshold_minutes:
        return AgentActivityStatus.STUCK
    return AgentActivityStatus.OFFLINE


def _parse_iso(ts: str) -> datetime:
    """Parse ISO 8601 timestamp, defaulting to UTC if no timezone."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def aggregate_usage(
    events: Sequence[CostEvent],
    agent_name: str,
    period_start: str,
    period_end: str,
) -> UsageMetrics:
    """Aggregate cost events into usage metrics for one agent over a period."""
    start_dt = _parse_iso(period_start)
    end_dt = _parse_iso(period_end)

    total_sessions = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0
    cost_by_source: dict[str, float] = {}

    for event in events:
        if event.agent_name != agent_name:
            continue

        event_dt = _parse_iso(event.timestamp)
        if event_dt < start_dt or event_dt > end_dt:
            continue

        total_sessions += 1
        total_input_tokens += event.input_tokens
        total_output_tokens += event.output_tokens
        total_cost_usd += event.amount_usd

        source_key = event.source.value
        cost_by_source[source_key] = cost_by_source.get(source_key, 0.0) + event.amount_usd

    return UsageMetrics(
        agent_name=agent_name,
        period_start=period_start,
        period_end=period_end,
        total_sessions=total_sessions,
        total_turns=0,  # Turns not tracked at the cost-event level
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cost_usd=total_cost_usd,
        cost_by_source=cost_by_source,
    )


# ---------------------------------------------------------------------------
# Alert Evaluation
# ---------------------------------------------------------------------------


def evaluate_alerts(
    events: Sequence[CostEvent],
    thresholds: Sequence[AlertThreshold],
    now: str | None = None,
) -> list[CostAlert]:
    """Check cost events against alert thresholds.

    Returns a list of triggered CostAlert instances. Agent-specific
    thresholds take priority over wildcard ('*') thresholds.
    """
    now_ts = now or datetime.now(timezone.utc).isoformat()
    now_dt = _parse_iso(now_ts)

    # Compute start of today and start of this week (Monday)
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_monday = now_dt.weekday()
    week_start = day_start - timedelta(days=days_since_monday)

    # Aggregate daily and weekly costs per agent
    daily_costs: dict[str, float] = {}
    weekly_costs: dict[str, float] = {}

    for event in events:
        event_dt = _parse_iso(event.timestamp)
        agent = event.agent_name

        if event_dt >= day_start:
            daily_costs[agent] = daily_costs.get(agent, 0.0) + event.amount_usd
        if event_dt >= week_start:
            weekly_costs[agent] = weekly_costs.get(agent, 0.0) + event.amount_usd

    # Build threshold lookup: agent-specific overrides wildcard
    wildcard_threshold: AlertThreshold | None = None
    agent_thresholds: dict[str, AlertThreshold] = {}

    for threshold in thresholds:
        if threshold.agent_name == "*":
            wildcard_threshold = threshold
        else:
            agent_thresholds[threshold.agent_name] = threshold

    # Collect all agent names from events
    all_agents = set(daily_costs.keys()) | set(weekly_costs.keys())
    alerts: list[CostAlert] = []

    for agent in sorted(all_agents):
        threshold = agent_thresholds.get(agent, wildcard_threshold)
        if threshold is None:
            continue

        daily = daily_costs.get(agent, 0.0)
        weekly = weekly_costs.get(agent, 0.0)

        if daily > threshold.daily_limit_usd:
            alerts.append(
                CostAlert(
                    agent_name=agent,
                    threshold_usd=threshold.daily_limit_usd,
                    actual_usd=daily,
                    period="daily",
                    triggered_at=now_ts,
                    message=f"Agent '{agent}' daily cost ${daily:.2f} exceeds limit ${threshold.daily_limit_usd:.2f}",
                )
            )

        if weekly > threshold.weekly_limit_usd:
            alerts.append(
                CostAlert(
                    agent_name=agent,
                    threshold_usd=threshold.weekly_limit_usd,
                    actual_usd=weekly,
                    period="weekly",
                    triggered_at=now_ts,
                    message=f"Agent '{agent}' weekly cost ${weekly:.2f} exceeds limit ${threshold.weekly_limit_usd:.2f}",
                )
            )

    return alerts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AgentActivityStatus",
    "AgentStatus",
    "AlertThreshold",
    "CostAlert",
    "CostDashboardError",
    "CostEvent",
    "CostSource",
    "DEFAULT_OFFLINE_THRESHOLD_MINUTES",
    "DEFAULT_STUCK_THRESHOLD_MINUTES",
    "InvalidCostEventError",
    "InvalidThresholdError",
    "ONLINE_THRESHOLD_MINUTES",
    "UsageMetrics",
    "aggregate_usage",
    "build_agent_status",
    "build_cost_event",
    "classify_agent_status",
    "evaluate_alerts",
]
