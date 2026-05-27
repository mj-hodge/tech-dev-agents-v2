"""Tests for MondayService — T19-T21."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.ops_console.models.responses import StoryInfo
from tech_dev_agents.ops_console.services.monday_service import MondayService


def _make_mock_client(story_data=None):
    """Create a mock AgentMondayClient."""
    client = MagicMock()
    client.board_id = 18405631030
    client.get_story.return_value = story_data
    return client


class TestMondayService:
    """T19-T21: Monday.com story data."""

    @pytest.mark.asyncio
    async def test_get_current_story_returns_story_info(self):
        """T19: get_current_story returns StoryInfo with item_id, name, phase, status from Monday.com client."""
        story_data = {
            "id": 12345,
            "name": "STORY-016: Agent Operations Console",
            "column_values": {"phase": "Phase 8: Implementation", "status": "In Progress"},
            "group": {"title": "In Progress"},
        }
        mock_client = _make_mock_client(story_data)

        service = MondayService(clients={"dan": mock_client})
        result = await service.get_current_story("dan")

        assert isinstance(result, StoryInfo)
        assert result.item_id == 12345
        assert result.name == "STORY-016: Agent Operations Console"
        assert result.phase == "Phase 8: Implementation"
        assert result.status == "In Progress"
        assert result.group == "In Progress"

    @pytest.mark.asyncio
    async def test_get_current_story_no_active_story(self):
        """T20: get_current_story returns None when agent has no in-progress story."""
        mock_client = _make_mock_client(story_data=None)

        service = MondayService(clients={"dan": mock_client})
        result = await service.get_current_story("dan")

        assert result is None

    @pytest.mark.asyncio
    async def test_stories_in_progress_counts_across_agents(self):
        """T21: get_stories_in_progress returns correct count across all configured agents."""
        dan_client = _make_mock_client({
            "id": 1,
            "name": "STORY-016",
            "column_values": {},
            "group": {"title": "In Progress"},
        })
        derrick_client = _make_mock_client({
            "id": 2,
            "name": "STORY-017",
            "column_values": {},
            "group": {"title": "In Progress"},
        })
        idle_client = _make_mock_client({
            "id": 3,
            "name": "STORY-018",
            "column_values": {},
            "group": {"title": "Done"},
        })

        service = MondayService(
            clients={"dan": dan_client, "derrick": derrick_client, "idle": idle_client}
        )
        count = await service.get_stories_in_progress()

        assert count == 2  # dan and derrick are "In Progress", idle is "Done"
