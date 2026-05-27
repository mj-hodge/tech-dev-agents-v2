"""
STORY-013: Agent Monday.com Integration — Test Suite
Phase 7 (Test Design)

Tests for the agent-identity-aware Monday.com integration layer.
Covers: AgentIdentity validation, PhaseComment rendering, StoryStatusTransition,
AgentMondayClient operations, and phase/group mappings.

Run with:  pytest tests/test_monday_agent.py -v
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from tech_dev_agents.monday_agent import (
    AgentIdentity,
    AgentIdentityError,
    PhaseComment,
    StoryStatusTransition,
    AgentMondayClient,
    PHASE_GROUP_MAP,
    PHASE_NAMES,
    format_duration,
)
from tech_dev_agents.monday import _DEFAULT_GROUP_MAP


# -----------------------------------------------------------------------
# Group 1 — AgentIdentity Validation (SC-1)
# -----------------------------------------------------------------------


class TestAgentIdentity:
    def test_valid_identity_construction(self):
        """T01 — Valid identity stores all fields correctly."""
        ident = AgentIdentity(agent_name="Bot Dan", api_token="tok-123", board_id=18405631030)
        assert ident.agent_name == "Bot Dan"
        assert ident.api_token == "tok-123"
        assert ident.board_id == 18405631030

    def test_empty_agent_name_raises(self):
        """T02 — Empty agent_name raises AgentIdentityError."""
        with pytest.raises(AgentIdentityError, match="agent_name"):
            AgentIdentity(agent_name="", api_token="tok-123", board_id=1)

    def test_empty_api_token_raises(self):
        """T03 — Empty api_token raises AgentIdentityError."""
        with pytest.raises(AgentIdentityError, match="api_token"):
            AgentIdentity(agent_name="Bot Dan", api_token="", board_id=1)

    def test_non_positive_board_id_raises(self):
        """T04 — Zero or negative board_id raises AgentIdentityError."""
        with pytest.raises(AgentIdentityError, match="board_id"):
            AgentIdentity(agent_name="Bot Dan", api_token="tok-123", board_id=0)
        with pytest.raises(AgentIdentityError, match="board_id"):
            AgentIdentity(agent_name="Bot Dan", api_token="tok-123", board_id=-1)

    def test_identity_is_frozen(self):
        """T05 — AgentIdentity is immutable (frozen dataclass)."""
        ident = AgentIdentity(agent_name="Bot Dan", api_token="tok-123", board_id=1)
        with pytest.raises(AttributeError):
            ident.agent_name = "Bot Sarah"


# -----------------------------------------------------------------------
# Group 2 — PhaseComment Rendering (SC-6)
# -----------------------------------------------------------------------


class TestPhaseComment:
    def test_render_full_comment(self):
        """T06 — Full comment renders with all sections."""
        comment = PhaseComment(
            phase_number="7",
            phase_name="Test Design",
            deliverables=["test-design.md", "tests/test_monday_agent.py"],
            decisions=["Used composition over inheritance"],
            duration_seconds=754.0,
            next_phase="8",
            next_phase_name="Implementation",
        )
        rendered = comment.render()
        assert "Phase 7 (Test Design) Complete" in rendered
        assert "test-design.md" in rendered
        assert "tests/test_monday_agent.py" in rendered
        assert "Used composition over inheritance" in rendered
        assert "12m 34s" in rendered
        assert "Phase 8 (Implementation)" in rendered

    def test_render_no_next_phase(self):
        """T07 — When next_phase is None, shows 'Story complete' instead."""
        comment = PhaseComment(
            phase_number="11",
            phase_name="Pre-Deploy Gate",
            deliverables=["predeploy-gate.md"],
            decisions=["All checks passed"],
            duration_seconds=312.0,
            next_phase=None,
            next_phase_name=None,
        )
        rendered = comment.render()
        assert "Phase 11" in rendered
        assert "Story complete" in rendered
        assert "Phase None" not in rendered

    def test_render_empty_deliverables(self):
        """T08 — Empty deliverables list renders as 'None'."""
        comment = PhaseComment(
            phase_number="4",
            phase_name="Analysis",
            deliverables=[],
            decisions=["Selected Approach A"],
            duration_seconds=60.0,
            next_phase="6",
            next_phase_name="Design",
        )
        rendered = comment.render()
        assert "None" in rendered or "none" in rendered

    def test_render_empty_decisions(self):
        """T09 — Empty decisions list renders as 'None'."""
        comment = PhaseComment(
            phase_number="6b",
            phase_name="Security Review",
            deliverables=["security-review.md"],
            decisions=[],
            duration_seconds=120.0,
            next_phase="6c",
            next_phase_name="UX Review",
        )
        rendered = comment.render()
        # Should not crash; should have some placeholder for decisions
        assert "Security Review" in rendered

    def test_render_multiple_deliverables_joined(self):
        """T10 — Multiple deliverables are comma-separated in rendered output."""
        comment = PhaseComment(
            phase_number="6",
            phase_name="Design",
            deliverables=["feature-spec.md", "architecture.md", "api-design.md"],
            decisions=[],
            duration_seconds=600.0,
            next_phase="6b",
            next_phase_name="Security Review",
        )
        rendered = comment.render()
        assert "feature-spec.md" in rendered
        assert "architecture.md" in rendered
        assert "api-design.md" in rendered

    def test_phase_comment_negative_duration_raises(self):
        """T11 — Negative duration_seconds raises ValueError."""
        with pytest.raises(ValueError, match="duration"):
            PhaseComment(
                phase_number="7",
                phase_name="Test Design",
                deliverables=[],
                decisions=[],
                duration_seconds=-1.0,
                next_phase=None,
                next_phase_name=None,
            )


# -----------------------------------------------------------------------
# Group 3 — StoryStatusTransition Validation (SC-5)
# -----------------------------------------------------------------------


class TestStoryStatusTransition:
    def test_valid_transition(self):
        """T12 — Valid transition stores all fields."""
        t = StoryStatusTransition(
            item_id=99001,
            from_group="Backlog",
            to_group="In Progress",
            reason="Phase 8 started",
        )
        assert t.item_id == 99001
        assert t.to_group == "In Progress"

    def test_transition_from_group_none_allowed(self):
        """T13 — from_group=None is valid (unknown current state)."""
        t = StoryStatusTransition(
            item_id=99001, from_group=None, to_group="In Progress", reason="Starting"
        )
        assert t.from_group is None

    def test_non_positive_item_id_raises(self):
        """T14 — Zero or negative item_id raises ValueError."""
        with pytest.raises(ValueError, match="item_id"):
            StoryStatusTransition(item_id=0, from_group=None, to_group="Done", reason="test")

    def test_empty_to_group_raises(self):
        """T15 — Empty to_group raises ValueError."""
        with pytest.raises(ValueError, match="to_group"):
            StoryStatusTransition(item_id=1, from_group=None, to_group="", reason="test")

    def test_transition_is_frozen(self):
        """T16 — StoryStatusTransition is immutable."""
        t = StoryStatusTransition(item_id=1, from_group=None, to_group="Done", reason="test")
        with pytest.raises(AttributeError):
            t.to_group = "Backlog"


# -----------------------------------------------------------------------
# Group 4 — AgentMondayClient (SC-2, SC-3, SC-4, SC-5)
# -----------------------------------------------------------------------


class TestAgentMondayClient:
    @pytest.fixture()
    def identity(self):
        return AgentIdentity(agent_name="Bot Dan", api_token="tok-abc", board_id=18405631030)

    @pytest.fixture()
    def agent_client(self, identity):
        return AgentMondayClient(identity)

    def test_client_exposes_agent_name(self, agent_client):
        """T17 — agent_name property returns identity's agent name."""
        assert agent_client.agent_name == "Bot Dan"

    def test_client_exposes_board_id(self, agent_client):
        """T18 — board_id property returns identity's board ID."""
        assert agent_client.board_id == 18405631030

    @patch("tech_dev_agents.monday.MondayClient.post_update")
    def test_post_phase_comment_delegates_to_client(self, mock_post_update, agent_client):
        """T19 — post_phase_comment renders comment and calls post_update."""
        mock_post_update.return_value = {"id": "55001"}

        comment = PhaseComment(
            phase_number="7",
            phase_name="Test Design",
            deliverables=["test-design.md"],
            decisions=["TDD approach"],
            duration_seconds=300.0,
            next_phase="8",
            next_phase_name="Implementation",
        )
        result = agent_client.post_phase_comment(item_id=99001, comment=comment)

        mock_post_update.assert_called_once_with(item_id=99001, body=comment.render())
        assert result.get("id") == "55001"

    @patch("tech_dev_agents.monday.MondayClient.move_item_to_group")
    def test_move_story_resolves_group_name(self, mock_move, agent_client):
        """T20 — move_story resolves group name to group ID via group_map."""
        mock_move.return_value = {"id": "99001"}

        transition = StoryStatusTransition(
            item_id=99001,
            from_group="Backlog",
            to_group="In Progress",
            reason="Phase 8 started",
        )
        result = agent_client.move_story(transition)

        mock_move.assert_called_once_with(
            item_id=99001,
            group_id=_DEFAULT_GROUP_MAP["In Progress"],
        )

    def test_move_story_unknown_group_raises(self, agent_client):
        """T21 — Unknown group name raises ValueError listing valid groups."""
        transition = StoryStatusTransition(
            item_id=99001,
            from_group=None,
            to_group="Nonexistent Group",
            reason="test",
        )
        with pytest.raises(ValueError, match="Unknown group"):
            agent_client.move_story(transition)

    @patch("tech_dev_agents.monday.requests.post")
    def test_get_story_delegates_to_client(self, mock_post, agent_client):
        """T22 — get_story delegates to MondayClient.get_item."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "data": {
                        "items": [
                            {
                                "id": "99001",
                                "name": "Test Story",
                                "description": "desc",
                                "group": {"id": "group_backlog", "title": "Backlog"},
                                "column_values": [{"id": "status", "text": "Ready"}],
                            }
                        ]
                    }
                }
            ),
        )

        result = agent_client.get_story(99001)
        assert result["id"] == "99001"
        assert result["name"] == "Test Story"

    @patch("tech_dev_agents.monday.requests.post")
    def test_client_uses_agent_token(self, mock_post, agent_client):
        """T23 — HTTP requests use the agent's own API token, not a shared one."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "data": {
                        "items": [
                            {
                                "id": "99001",
                                "name": "Test",
                                "description": None,
                                "group": {"id": "g", "title": "G"},
                                "column_values": [],
                            }
                        ]
                    }
                }
            ),
        )

        agent_client.get_story(99001)
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "tok-abc"


# -----------------------------------------------------------------------
# Group 5 — Mappings & Utilities
# -----------------------------------------------------------------------


class TestMappings:
    def test_phase_group_map_has_key_phases(self):
        """T24 — PHASE_GROUP_MAP contains entries for phases 1, 7, 8, 11, done."""
        assert "1" in PHASE_GROUP_MAP
        assert "7" in PHASE_GROUP_MAP
        assert "8" in PHASE_GROUP_MAP
        assert "11" in PHASE_GROUP_MAP
        assert "done" in PHASE_GROUP_MAP

    def test_phase_names_has_all_phases(self):
        """T25 — PHASE_NAMES contains entries for all 15 known phases."""
        expected = {"1", "2", "3", "4", "5", "6", "6b", "6c", "6d", "7", "8", "8b", "9", "10", "11"}
        assert expected.issubset(set(PHASE_NAMES.keys()))

    def test_format_duration_seconds(self):
        """T26 — format_duration renders seconds correctly."""
        assert format_duration(45.0) == "45s"

    def test_format_duration_minutes_and_seconds(self):
        """T27 — format_duration renders minutes + seconds correctly."""
        assert format_duration(754.0) == "12m 34s"

    def test_format_duration_hours(self):
        """T28 — format_duration renders hours correctly."""
        assert format_duration(3661.0) == "1h 1m 1s"

    def test_format_duration_zero(self):
        """T29 — format_duration handles zero."""
        assert format_duration(0.0) == "0s"
