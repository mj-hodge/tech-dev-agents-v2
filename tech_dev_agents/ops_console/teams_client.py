"""Microsoft Graph API client for Teams messaging."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# App registration ID for Graph API
GRAPH_APP_REGISTRATION_ID = "dc0cba0b"

# Blocker detection patterns
_BLOCKER_RE = re.compile(r"^Blocked:", re.MULTILINE)
_DECISION_RE = re.compile(r"^Decision needed:", re.MULTILINE)


@dataclass
class TeamsMessage:
    """A single Teams chat message."""

    sender: str
    timestamp: str
    content: str
    chat_id: str


def detect_blocker(content: str) -> str | None:
    """Detect blocker/decision-needed badges in message content.

    Returns:
        "blocker" if content contains "Blocked:",
        "decision" if content contains "Decision needed:",
        None otherwise.
    """
    if _BLOCKER_RE.search(content):
        return "blocker"
    if _DECISION_RE.search(content):
        return "decision"
    return None


class TeamsClientError(Exception):
    """Raised when the Graph API returns an error."""


class TeamsClient:
    """Async client for Microsoft Graph API — Teams chat messaging.

    Resolves agent name → email (from registry) → chat ID (from Graph API),
    then sends/reads messages in the 1:1 chat.
    """

    def __init__(
        self,
        graph_api_token: str,
        http_client: httpx.AsyncClient,
        agent_registry: list[dict],
        base_url: str = "https://graph.microsoft.com/v1.0",
    ):
        self._token = graph_api_token
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        # Build agent name → email lookup
        self._agent_emails: dict[str, str] = {}
        for entry in agent_registry:
            name = entry.get("name", "")
            email = entry.get("email")
            if name and email:
                self._agent_emails[name] = email
        # Cache: agent name → chat_id
        self._chat_id_cache: dict[str, str] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def get_agent_email(self, agent_name: str) -> str | None:
        """Resolve agent name to email from registry."""
        return self._agent_emails.get(agent_name)

    async def resolve_chat_id(self, agent_name: str) -> str:
        """Resolve agent name → chat ID via Graph API.

        Searches /me/chats for a 1:1 chat where the participant matches
        the agent's email. Caches the result in memory.

        Raises:
            TeamsClientError: If agent email not found or Graph API fails.
        """
        # Check cache first
        if agent_name in self._chat_id_cache:
            return self._chat_id_cache[agent_name]

        email = self.get_agent_email(agent_name)
        if not email:
            raise TeamsClientError(
                f"No email found for agent '{agent_name}' in registry"
            )

        url = f"{self._base_url}/me/chats"
        try:
            resp = await self._http.get(url, headers=self._headers, timeout=10.0)
            if resp.status_code != 200:
                raise TeamsClientError(
                    f"Graph API /me/chats failed: {resp.status_code} {resp.text}"
                )

            chats = resp.json().get("value", [])
            for chat in chats:
                # Check members for matching email
                members = chat.get("members", [])
                for member in members:
                    member_email = member.get("email", "")
                    if member_email.lower() == email.lower():
                        chat_id = chat["id"]
                        self._chat_id_cache[agent_name] = chat_id
                        return chat_id

            raise TeamsClientError(
                f"No 1:1 chat found for agent '{agent_name}' ({email})"
            )
        except httpx.HTTPError as exc:
            raise TeamsClientError(f"Graph API request failed: {exc}") from exc

    async def send_message(self, agent_name: str, content: str) -> TeamsMessage:
        """Send a message to an agent's Teams chat.

        Args:
            agent_name: The agent identifier.
            content: The message text to send.

        Returns:
            TeamsMessage with the sent message details.

        Raises:
            TeamsClientError: If chat resolution or send fails.
        """
        chat_id = await self.resolve_chat_id(agent_name)
        url = f"{self._base_url}/chats/{chat_id}/messages"

        try:
            resp = await self._http.post(
                url,
                headers=self._headers,
                json={"body": {"content": content}},
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                raise TeamsClientError(
                    f"Graph API send failed: {resp.status_code} {resp.text}"
                )

            data = resp.json()
            return TeamsMessage(
                sender=data.get("from", {}).get("user", {}).get("displayName", "ops-console"),
                timestamp=data.get("createdDateTime", datetime.now().isoformat()),
                content=content,
                chat_id=chat_id,
            )
        except httpx.HTTPError as exc:
            raise TeamsClientError(f"Graph API send request failed: {exc}") from exc

    async def read_messages(
        self, agent_name: str, limit: int = 20
    ) -> list[TeamsMessage]:
        """Read recent messages from an agent's Teams chat.

        Args:
            agent_name: The agent identifier.
            limit: Maximum number of messages to return (default 20).

        Returns:
            List of TeamsMessage objects, newest first.

        Raises:
            TeamsClientError: If chat resolution or read fails.
        """
        chat_id = await self.resolve_chat_id(agent_name)
        url = (
            f"{self._base_url}/chats/{chat_id}/messages"
            f"?$top={limit}&$orderby=createdDateTime desc"
        )

        try:
            resp = await self._http.get(url, headers=self._headers, timeout=10.0)
            if resp.status_code != 200:
                raise TeamsClientError(
                    f"Graph API read failed: {resp.status_code} {resp.text}"
                )

            messages = []
            for msg in resp.json().get("value", []):
                sender = (
                    msg.get("from", {}).get("user", {}).get("displayName", "unknown")
                )
                messages.append(
                    TeamsMessage(
                        sender=sender,
                        timestamp=msg.get("createdDateTime", ""),
                        content=msg.get("body", {}).get("content", ""),
                        chat_id=chat_id,
                    )
                )
            return messages
        except httpx.HTTPError as exc:
            raise TeamsClientError(f"Graph API read request failed: {exc}") from exc
