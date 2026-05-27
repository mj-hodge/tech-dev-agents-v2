"""Monday.com phase-transition hooks for agent automation.

STORY-015: Deploy Agent Dashboards & Wiring
Phase 8 — Implementation

Provides hook functions called at story start, story completion,
and phase transitions to automatically update Monday.com boards.
Built on top of AgentMondayClient from STORY-013.
"""

from __future__ import annotations

from typing import Any

from tech_dev_agents.monday_agent import (
    AgentMondayClient,
    PhaseComment,
    StoryStatusTransition,
    PHASE_GROUP_MAP,
    PHASE_NAMES,
    format_duration,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HookError(Exception):
    """Raised when a Monday.com hook operation fails."""


# ---------------------------------------------------------------------------
# Hook Functions
# ---------------------------------------------------------------------------


def on_story_start(
    client: AgentMondayClient,
    item_id: int,
    agent_name: str,
) -> StoryStatusTransition:
    """Hook called when an agent starts working on a story.

    Moves the story to "In Progress" on Monday.com.

    Args:
        client: AgentMondayClient with agent identity.
        item_id: Monday.com item ID for the story.
        agent_name: Name of the agent starting the story.

    Returns:
        StoryStatusTransition record.

    Raises:
        HookError: If agent_name is empty.
    """
    if not agent_name or not agent_name.strip():
        raise HookError("agent_name must be a non-empty string")

    transition = StoryStatusTransition(
        item_id=item_id,
        from_group=None,
        to_group="In Progress",
        reason=f"Agent '{agent_name}' started work",
    )

    client.move_story(transition)
    return transition


def on_story_complete(
    client: AgentMondayClient,
    item_id: int,
    agent_name: str,
    summary: str,
    duration_seconds: float = 0.0,
) -> tuple[StoryStatusTransition, PhaseComment]:
    """Hook called when an agent completes a story.

    Moves the story to "Done" and posts a completion summary comment.

    Args:
        client: AgentMondayClient with agent identity.
        item_id: Monday.com item ID for the story.
        agent_name: Name of the agent completing the story.
        summary: Human-readable completion summary.
        duration_seconds: Total duration of the story.

    Returns:
        Tuple of (StoryStatusTransition, PhaseComment).
    """
    transition = StoryStatusTransition(
        item_id=item_id,
        from_group="In Progress",
        to_group="Done",
        reason=f"Agent '{agent_name}' completed story",
    )

    comment = PhaseComment(
        phase_number="done",
        phase_name="Story Complete",
        deliverables=[summary],
        decisions=[],
        duration_seconds=duration_seconds,
        next_phase=None,
        next_phase_name=None,
    )

    client.move_story(transition)
    client.post_phase_comment(item_id, comment)
    return transition, comment


def on_phase_complete(
    client: AgentMondayClient,
    item_id: int,
    phase_number: str,
    deliverables: list[str],
    decisions: list[str],
    duration_seconds: float,
    next_phase: str | None = None,
) -> PhaseComment:
    """Hook called when an agent completes an SDLC phase.

    Posts a structured phase-transition comment on the Monday.com item.

    Args:
        client: AgentMondayClient with agent identity.
        item_id: Monday.com item ID for the story.
        phase_number: Phase number (e.g. "1", "6b", "8").
        deliverables: List of deliverable filenames produced.
        decisions: List of key decisions made.
        duration_seconds: How long the phase took.
        next_phase: Next phase number, or None if story complete.

    Returns:
        PhaseComment record.
    """
    phase_name = PHASE_NAMES.get(phase_number, f"Phase {phase_number}")
    next_phase_name = PHASE_NAMES.get(next_phase, None) if next_phase else None

    comment = PhaseComment(
        phase_number=phase_number,
        phase_name=phase_name,
        deliverables=deliverables,
        decisions=decisions,
        duration_seconds=duration_seconds,
        next_phase=next_phase,
        next_phase_name=next_phase_name,
    )

    client.post_phase_comment(item_id, comment)
    return comment


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "HookError",
    "on_phase_complete",
    "on_story_complete",
    "on_story_start",
]
