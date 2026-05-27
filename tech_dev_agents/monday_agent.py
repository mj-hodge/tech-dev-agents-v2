"""
Agent Monday.com integration layer for tech-dev-agents.

STORY-013: Agent Monday.com Integration
Phase 8 — Implementation

Provides per-agent Monday.com identity, structured phase-transition comments,
and story status management. Built on top of the existing MondayClient (STORY-004).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tech_dev_agents.monday import MondayClient


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentIdentityError(Exception):
    """Raised when agent identity configuration is invalid."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_GROUP_MAP: dict[str, str] = {
    "1": "Backlog",
    "2": "Backlog",
    "3": "Backlog",
    "4": "Backlog",
    "5": "Backlog",
    "6": "Backlog",
    "6b": "Backlog",
    "6c": "Backlog",
    "6d": "Backlog",
    "7": "In Progress",
    "8": "In Progress",
    "8b": "In Progress",
    "9": "In Progress",
    "10": "In Progress",
    "11": "E2E Gate",
    "done": "Done",
}

PHASE_NAMES: dict[str, str] = {
    "1": "Seed",
    "2": "Research",
    "3": "Expansion",
    "4": "Analysis",
    "5": "Selection",
    "6": "Design",
    "6b": "Security Review",
    "6c": "UX Review",
    "6d": "Ops Review",
    "7": "Test Design",
    "8": "Implementation",
    "8b": "Code Review",
    "9": "Refinement",
    "10": "Operations",
    "11": "Pre-Deploy Gate",
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples:
        45.0 -> "45s"
        754.0 -> "12m 34s"
        3661.0 -> "1h 1m 1s"
        0.0 -> "0s"
    """
    total = int(seconds)
    if total <= 0:
        return "0s"

    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentIdentity:
    """Per-agent Monday.com identity.

    Each agent (Bot Dan, Bot Sarah, etc.) has its own Monday.com user account
    and API token so updates are attributed to the correct agent.
    """

    agent_name: str
    api_token: str = field(repr=False)
    board_id: int

    def __post_init__(self) -> None:
        if not self.agent_name or not self.agent_name.strip():
            raise AgentIdentityError("agent_name must be a non-empty string.")
        if not self.api_token or not self.api_token.strip():
            raise AgentIdentityError("api_token must be a non-empty string.")
        if not isinstance(self.board_id, int) or self.board_id <= 0:
            raise AgentIdentityError("board_id must be a positive integer.")


@dataclass(frozen=True)
class PhaseComment:
    """Structured comment posted at each SDLC phase transition.

    Renders to a formatted text body suitable for Monday.com update API.
    """

    phase_number: str
    phase_name: str
    deliverables: list[str]
    decisions: list[str]
    duration_seconds: float
    next_phase: str | None
    next_phase_name: str | None

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0.")

    def render(self) -> str:
        """Render to a Monday.com update body."""
        lines: list[str] = []

        lines.append(f"**Phase {self.phase_number} ({self.phase_name}) Complete**")

        # Deliverables
        if self.deliverables:
            deliverable_str = ", ".join(self.deliverables)
        else:
            deliverable_str = "None"
        lines.append(f"- **Deliverables:** {deliverable_str}")

        # Decisions
        if self.decisions:
            decision_str = "; ".join(self.decisions)
        else:
            decision_str = "None"
        lines.append(f"- **Decisions:** {decision_str}")

        # Duration
        lines.append(f"- **Duration:** {format_duration(self.duration_seconds)}")

        # Next phase or completion
        if self.next_phase is not None and self.next_phase_name is not None:
            lines.append(f"- **Next:** Phase {self.next_phase} ({self.next_phase_name})")
        else:
            lines.append("- **Status:** Story complete")

        return "\n".join(lines)


@dataclass(frozen=True)
class StoryStatusTransition:
    """Represents a story moving between Monday.com groups."""

    item_id: int
    from_group: str | None
    to_group: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, int) or self.item_id <= 0:
            raise ValueError("item_id must be a positive integer.")
        if not self.to_group or not self.to_group.strip():
            raise ValueError("to_group must be a non-empty string.")


# ---------------------------------------------------------------------------
# Agent Monday.com client
# ---------------------------------------------------------------------------


class AgentMondayClient:
    """Monday.com client with per-agent identity and SDLC-aware operations.

    Uses composition over the base MondayClient to add agent identity,
    structured phase comments, and story status management.
    """

    def __init__(self, identity: AgentIdentity) -> None:
        self._identity = identity
        self._client = MondayClient(
            api_key=identity.api_token,
            board_id=identity.board_id,
        )

    @property
    def agent_name(self) -> str:
        """Return the agent's display name."""
        return self._identity.agent_name

    @property
    def board_id(self) -> int:
        """Return the configured board ID."""
        return self._identity.board_id

    def post_phase_comment(self, item_id: int, comment: PhaseComment) -> dict[str, Any]:
        """Post a structured phase-transition comment on a Monday.com item.

        Renders the PhaseComment to text and delegates to the underlying
        MondayClient.post_update().
        """
        body = comment.render()
        return self._client.post_update(item_id=item_id, body=body)

    def move_story(self, transition: StoryStatusTransition) -> dict[str, Any]:
        """Move a story to a new status group.

        Resolves the group name to a group ID via the client's group_map.
        Raises ValueError if the group name is not recognized.
        """
        group_map = self._client.group_map
        group_id = group_map.get(transition.to_group)
        if group_id is None:
            valid_groups = ", ".join(sorted(group_map.keys()))
            raise ValueError(
                f"Unknown group '{transition.to_group}'. "
                f"Valid groups: {valid_groups}"
            )
        return self._client.move_item_to_group(
            item_id=transition.item_id,
            group_id=group_id,
        )

    def get_story(self, item_id: int) -> dict[str, Any]:
        """Fetch story details from Monday.com.

        Delegates to MondayClient.get_item().
        """
        return self._client.get_item(item_id=item_id)


__all__ = [
    "AgentIdentity",
    "AgentIdentityError",
    "AgentMondayClient",
    "PhaseComment",
    "StoryStatusTransition",
    "PHASE_GROUP_MAP",
    "PHASE_NAMES",
    "format_duration",
]
