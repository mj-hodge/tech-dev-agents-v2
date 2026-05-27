"""Agent health and restart API logic.

STORY-015: Deploy Agent Dashboards & Wiring
Phase 8 — Implementation

Pure functions for health reporting and restart management.
No HTTP framework dependency — returns plain dicts for any ASGI wrapper.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from tech_dev_agents.agent_dashboard import (
    AgentActivityStatus,
    AgentHealthSnapshot,
    RestartResult,
    build_health_snapshot,
    build_restart_command,
    build_restart_result,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Raised when API key validation fails."""


# ---------------------------------------------------------------------------
# API Key Validation
# ---------------------------------------------------------------------------


def validate_api_key(provided: str | None, expected: str) -> bool:
    """Validate an API key using constant-time comparison.

    Returns True if valid. Raises AuthError if missing or invalid.
    """
    if not provided:
        raise AuthError("Missing API key")
    if not hmac.compare_digest(provided, expected):
        raise AuthError("Invalid API key")
    return True


# ---------------------------------------------------------------------------
# Health Response
# ---------------------------------------------------------------------------


def build_health_response(
    agent_name: str,
    last_activity: str | None,
    uptime_seconds: int,
    active_sessions: int,
    error_count: int,
) -> dict[str, Any]:
    """Build a health response dict from agent state.

    Returns a JSON-serializable dict with agent health information
    including a derived status classification.
    """
    snapshot = build_health_snapshot(
        agent_name=agent_name,
        last_activity=last_activity,
        uptime_seconds=uptime_seconds,
        active_sessions=active_sessions,
        error_count=error_count,
    )
    return {
        "agent_name": snapshot.agent_name,
        "status": snapshot.status.value,
        "last_activity": snapshot.last_activity,
        "uptime_seconds": snapshot.uptime_seconds,
        "active_sessions": snapshot.active_sessions,
        "error_count": snapshot.error_count,
        "checked_at": snapshot.checked_at,
        "version": _VERSION,
    }


def build_restart_response(
    agent_name: str,
    reason: str,
    requested_by: str,
    force: bool = False,
    restart_fn: Any = None,
) -> dict[str, Any]:
    """Build a restart response by executing the restart logic.

    The restart_fn callable is injected to decouple from actual restart
    mechanics (systemctl, process signals, etc.). If restart_fn is None
    or raises, returns a failure response.

    Args:
        agent_name: Name of the agent to restart.
        reason: Human-readable reason for the restart.
        requested_by: Who requested the restart.
        force: Whether to force restart even if agent is healthy.
        restart_fn: Callable that performs the actual restart.
                    Signature: (agent_name: str, force: bool) -> None
                    Raises on failure.

    Returns:
        JSON-serializable dict with restart result.
    """
    command = build_restart_command(
        agent_name=agent_name,
        reason=reason,
        requested_by=requested_by,
        force=force,
    )

    previous_status = AgentActivityStatus.ONLINE  # Assumed pre-restart

    try:
        if restart_fn is not None:
            restart_fn(agent_name, force)

        result = build_restart_result(
            agent_name=command.agent_name,
            success=True,
            message=f"Agent '{agent_name}' restarted successfully",
            previous_status=previous_status,
            new_status=AgentActivityStatus.ONLINE,
        )
    except Exception as exc:
        result = build_restart_result(
            agent_name=command.agent_name,
            success=False,
            message=f"Restart failed: {exc}",
            previous_status=previous_status,
            new_status=None,
        )

    return {
        "agent_name": result.agent_name,
        "success": result.success,
        "message": result.message,
        "previous_status": result.previous_status.value,
        "new_status": result.new_status.value if result.new_status else None,
        "completed_at": result.completed_at,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AuthError",
    "build_health_response",
    "build_restart_response",
    "validate_api_key",
]
