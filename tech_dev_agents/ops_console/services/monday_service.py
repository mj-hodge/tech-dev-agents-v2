"""Monday.com story data service for the ops console."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tech_dev_agents.ops_console.cache import TTLCache

logger = logging.getLogger(__name__)
from tech_dev_agents.ops_console.models.responses import ActivityEvent, StoryInfo


class MondayService:
    """Fetch story/phase data for agents from Monday.com."""

    def __init__(
        self,
        clients: dict[str, Any],  # agent_name -> AgentMondayClient
        monday_cache_ttl: int = 300,
    ):
        self._clients = clients
        self._cache = TTLCache(monday_cache_ttl)

    async def get_current_story(self, agent_name: str) -> StoryInfo | None:
        """Get the current story for an agent from Monday.com.

        Caches for 5min.
        """
        cached = self._cache.get(f"story:{agent_name}")
        if cached is not None:
            return cached

        client = self._clients.get(agent_name)
        if not client:
            return None

        try:
            # monday_agent.AgentMondayClient.get_story() is synchronous
            # Wrap in asyncio.to_thread to avoid blocking event loop
            board_id = client.board_id
            story_data = await asyncio.to_thread(client.get_story, board_id)

            if not story_data:
                self._cache.set(f"story:{agent_name}", None)
                return None

            # Parse response into StoryInfo
            story = StoryInfo(
                item_id=story_data.get("id", 0),
                name=story_data.get("name", ""),
                phase=story_data.get("column_values", {}).get("phase", None)
                if isinstance(story_data.get("column_values"), dict)
                else None,
                status=story_data.get("column_values", {}).get("status", None)
                if isinstance(story_data.get("column_values"), dict)
                else None,
                group=story_data.get("group", {}).get("title", None)
                if isinstance(story_data.get("group"), dict)
                else None,
            )
            self._cache.set(f"story:{agent_name}", story)
            return story
        except Exception:
            logger.warning("Failed to fetch story for agent %s from Monday.com", agent_name, exc_info=True)
            return None

    async def get_stories_in_progress(self) -> int:
        """Count stories in 'In Progress' group across all agents.

        Caches for 5min.
        """
        cached = self._cache.get("stories_in_progress")
        if cached is not None:
            return cached

        count = 0
        for agent_name in self._clients:
            try:
                story = await self.get_current_story(agent_name)
                if story and story.group and "progress" in story.group.lower():
                    count += 1
            except Exception:
                logger.warning("Failed to check story progress for agent %s", agent_name, exc_info=True)

        self._cache.set("stories_in_progress", count)
        return count

    async def get_activity_events(
        self,
        agent_name: str,
        limit: int = 50,
        event_type: str | None = None,
    ) -> list[ActivityEvent]:
        """Get recent activity events for an agent.

        Returns phase transitions, commits, etc. from Monday.com updates.
        """
        # For MVP, return empty list — full implementation requires
        # Monday.com activity log API queries
        return []
