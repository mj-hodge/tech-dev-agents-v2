"""Tests for STORY-015: Monday.com Hooks (SC-3).

Phase 7 test design — 6 tests covering story and phase transition hooks.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tech_dev_agents.monday_hooks import (
    HookError,
    on_phase_complete,
    on_story_complete,
    on_story_start,
)
from tech_dev_agents.monday_agent import (
    AgentIdentity,
    AgentMondayClient,
    PhaseComment,
    StoryStatusTransition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client() -> AgentMondayClient:
    """Create an AgentMondayClient with a mocked underlying MondayClient."""
    identity = AgentIdentity(
        agent_name="dan",
        api_token="test-token-123",
        board_id=12345,
    )
    client = AgentMondayClient(identity)
    # Mock the underlying client methods
    client._client = MagicMock()
    client._client.group_map = {
        "Backlog": "backlog_group",
        "In Progress": "in_progress_group",
        "E2E Gate": "e2e_gate_group",
        "Done": "done_group",
    }
    client._client.move_item_to_group = MagicMock(return_value={"success": True})
    client._client.post_update = MagicMock(return_value={"id": "update-1"})
    return client


# ---------------------------------------------------------------------------
# SC-3: Monday.com auto-update
# ---------------------------------------------------------------------------


class TestOnStoryStart:
    """Tests for on_story_start."""

    def test_returns_in_progress_transition(self) -> None:
        """Story start moves item to In Progress."""
        client = _mock_client()
        transition = on_story_start(client, item_id=100, agent_name="dan")

        assert isinstance(transition, StoryStatusTransition)
        assert transition.to_group == "In Progress"
        assert transition.item_id == 100
        client._client.move_item_to_group.assert_called_once()

    def test_empty_agent_name_raises(self) -> None:
        """Empty agent_name raises HookError."""
        client = _mock_client()
        with pytest.raises(HookError, match="non-empty"):
            on_story_start(client, item_id=100, agent_name="")


class TestOnStoryComplete:
    """Tests for on_story_complete."""

    def test_returns_done_transition_and_comment(self) -> None:
        """Story completion moves to Done and posts comment."""
        client = _mock_client()
        transition, comment = on_story_complete(
            client,
            item_id=100,
            agent_name="dan",
            summary="All 15 tests GREEN",
            duration_seconds=3600.0,
        )

        assert isinstance(transition, StoryStatusTransition)
        assert transition.to_group == "Done"
        assert isinstance(comment, PhaseComment)
        client._client.move_item_to_group.assert_called_once()
        client._client.post_update.assert_called_once()

    def test_includes_summary_in_comment(self) -> None:
        """Completion comment includes the summary text."""
        client = _mock_client()
        _, comment = on_story_complete(
            client,
            item_id=100,
            agent_name="dan",
            summary="Deployed successfully",
        )
        rendered = comment.render()
        assert "Deployed successfully" in rendered


class TestOnPhaseComplete:
    """Tests for on_phase_complete."""

    def test_builds_correct_phase_comment(self) -> None:
        """Phase comment has correct phase name and deliverables."""
        client = _mock_client()
        comment = on_phase_complete(
            client,
            item_id=100,
            phase_number="8",
            deliverables=["health_api.py", "cost_collector.py"],
            decisions=["Use pure functions"],
            duration_seconds=1800.0,
            next_phase="8b",
        )

        assert isinstance(comment, PhaseComment)
        assert comment.phase_name == "Implementation"
        assert comment.next_phase == "8b"
        assert comment.next_phase_name == "Code Review"
        client._client.post_update.assert_called_once()

    def test_formats_duration_correctly(self) -> None:
        """Phase comment duration is human-readable."""
        client = _mock_client()
        comment = on_phase_complete(
            client,
            item_id=100,
            phase_number="7",
            deliverables=["test-design.md"],
            decisions=[],
            duration_seconds=754.0,
        )
        rendered = comment.render()
        assert "12m 34s" in rendered
