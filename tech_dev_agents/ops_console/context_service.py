"""Agent context aggregation service for SC-9 context panel."""

from __future__ import annotations

import logging
from typing import Any

from tech_dev_agents.ops_console.teams_client import (
    TeamsClient,
    TeamsClientError,
    detect_blocker,
)

logger = logging.getLogger(__name__)


class ContextService:
    """Aggregates agent context from multiple sources for the context panel.

    Sources: Teams (messages, deep link), Monday.com (story/phase),
    Loki (commits), and blocker detection.
    """

    def __init__(
        self,
        teams_client: TeamsClient,
        monday_service: Any,  # MondayService
        loki_client: Any | None = None,  # LokiClient (optional)
    ):
        self._teams = teams_client
        self._monday = monday_service
        self._loki = loki_client

    def teams_chat_link(self, agent_name: str) -> str | None:
        """Return a Teams deep link for the agent's 1:1 chat.

        Format: https://teams.microsoft.com/l/chat/0/0?users={email}
        Returns None if agent has no email in registry.
        """
        email = self._teams.get_agent_email(agent_name)
        if not email:
            return None
        return f"https://teams.microsoft.com/l/chat/0/0?users={email}"

    async def current_story(self, agent_name: str) -> dict | None:
        """Get current story + phase from Monday.com.

        Returns dict with story info or None if unavailable.
        """
        try:
            story = await self._monday.get_current_story(agent_name)
            if story is None:
                return None
            return {
                "item_id": story.item_id,
                "name": story.name,
                "phase": story.phase,
                "status": story.status,
                "group": story.group,
            }
        except Exception:
            logger.warning(
                "Failed to fetch current story for %s", agent_name, exc_info=True
            )
            return None

    async def last_commits(self, agent_name: str, n: int = 5) -> list[dict]:
        """Get last N commits for the agent.

        TODO: Implement Loki log query for commit lines.
        Currently returns empty list as stub.
        """
        # TODO: Query Loki for [COMMIT] log lines matching agent_name
        # Example LogQL: {agent="{agent_name}"} |= "[COMMIT]" | limit {n}
        return []

    async def last_message(self, agent_name: str) -> dict | None:
        """Get the most recent message from the agent's Teams chat.

        Returns dict with sender, timestamp, content or None.
        """
        try:
            messages = await self._teams.read_messages(agent_name, limit=1)
            if not messages:
                return None
            msg = messages[0]
            return {
                "sender": msg.sender,
                "timestamp": msg.timestamp,
                "content": msg.content,
            }
        except TeamsClientError:
            logger.warning(
                "Failed to fetch last message for %s", agent_name, exc_info=True
            )
            return None

    async def blocker_status(self, agent_name: str) -> str | None:
        """Check the last agent message for blocker/decision-needed patterns.

        Returns "blocker", "decision", or None.
        """
        try:
            messages = await self._teams.read_messages(agent_name, limit=1)
            if not messages:
                return None
            return detect_blocker(messages[0].content)
        except TeamsClientError:
            logger.warning(
                "Failed to check blocker status for %s", agent_name, exc_info=True
            )
            return None

    async def get_full_context(self, agent_name: str) -> dict:
        """Aggregate all context sources into a single response dict.

        Returns:
            Dict with keys: teams_chat_link, current_story, last_commits,
            last_message, blocker_status, agent_name.
        """
        # Run independent fetches concurrently
        import asyncio

        story_task = asyncio.create_task(self.current_story(agent_name))
        commits_task = asyncio.create_task(self.last_commits(agent_name))
        message_task = asyncio.create_task(self.last_message(agent_name))
        blocker_task = asyncio.create_task(self.blocker_status(agent_name))

        story, commits, message, blocker = await asyncio.gather(
            story_task, commits_task, message_task, blocker_task
        )

        return {
            "agent_name": agent_name,
            "teams_chat_link": self.teams_chat_link(agent_name),
            "current_story": story,
            "last_commits": commits,
            "last_message": message,
            "blocker_status": blocker,
        }
