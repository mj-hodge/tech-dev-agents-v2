"""Shared agent status mapping — single source of truth for health→status conversion.

STORY-541: Added derive_agent_status() for multi-signal 6-state taxonomy.
Precedence: unreachable > stopped > paused > working > idle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tech_dev_agents.ops_console.models.responses import AgentStatusEnum

_STATUS_MAP = {
    "online": AgentStatusEnum.ONLINE,
    "idle": AgentStatusEnum.IDLE,
    "stuck": AgentStatusEnum.STUCK,
    "offline": AgentStatusEnum.OFFLINE,
    # STORY-496: new dispatch-derived statuses
    "rate_limited": AgentStatusEnum.RATE_LIMITED,
    "working": AgentStatusEnum.WORKING,
    # STORY-541: new multi-signal statuses
    "paused": AgentStatusEnum.PAUSED,
    "stopped": AgentStatusEnum.STOPPED,
    "unreachable": AgentStatusEnum.UNREACHABLE,
}


def map_agent_status(health) -> AgentStatusEnum:
    """Map an AgentHealthSnapshot (or None) to AgentStatusEnum.

    Handles:
    - None health → OFFLINE
    - Enum status with .value attribute
    - Plain string status
    """
    if not health:
        return AgentStatusEnum.OFFLINE

    status = getattr(health, "status", None)
    if status is None:
        return AgentStatusEnum.OFFLINE

    # AgentActivityStatus is a str enum, so .value gives lowercase string
    status_str = status.value if hasattr(status, "value") else str(status).lower()
    return _STATUS_MAP.get(status_str, AgentStatusEnum.OFFLINE)


def derive_agent_status(
    *,
    reachable: bool,
    poller_active: bool,
    paused_until: datetime | None = None,
    has_claim: bool = False,
    phase_in_progress: bool = False,
) -> str:
    """Derive agent status from multiple independent signals.

    Precedence (highest to lowest):
      unreachable > stopped > paused > working > idle

    Returns one of: "unreachable", "stopped", "paused", "working", "idle".
    """
    # 1. VM not reachable — nothing else matters
    if not reachable:
        return "unreachable"

    # 2. Poller dead — can't do work regardless of other signals
    if not poller_active:
        return "stopped"

    # 3. Paused — poller alive but paused_until is in the future
    if paused_until is not None:
        now = datetime.now(timezone.utc)
        # Ensure comparison works: make paused_until tz-aware if naive
        pu = paused_until if paused_until.tzinfo else paused_until.replace(tzinfo=timezone.utc)
        if pu > now:
            return "paused"

    # 4. Working — has an active claim and a phase is running
    if has_claim and phase_in_progress:
        return "working"

    # 5. Default — idle (reachable, poller running, not paused, not working)
    return "idle"
