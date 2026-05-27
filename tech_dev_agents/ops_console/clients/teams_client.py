"""Microsoft Graph API client for Teams messaging — STORY-016 v2.

Resolves agent names to Teams chat IDs via the Graph API, then sends/reads
messages in 1:1 chats.  All HTTP goes through an injected ``httpx.AsyncClient``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Coroutine

import httpx

logger = logging.getLogger(__name__)

# Blocker detection patterns
_BLOCKER_RE = re.compile(r"Blocked:", re.IGNORECASE)
_DECISION_RE = re.compile(r"Decision needed:", re.IGNORECASE)


class TeamsAuthError(Exception):
    """Raised when authentication with the Graph API fails."""


class TeamsClient:
    """Async client for Microsoft Graph API — Teams chat messaging.

    Parameters
    ----------
    http_client:
        An ``httpx.AsyncClient`` (or compatible async mock) used for all
        outgoing HTTP requests.
    registry_path:
        Filesystem path to ``agent-registry.json``.
    graph_base_url:
        Base URL of the Microsoft Graph API (default v1.0).
    access_token:
        Current OAuth2 access token.  An empty string triggers
        ``TeamsAuthError`` on any API call.
    token_provider:
        Optional async callable that returns a fresh token string.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        registry_path: str,
        graph_base_url: str = "https://graph.microsoft.com/v1.0",
        access_token: str = "",
        token_provider: Callable[[], Coroutine[Any, Any, str]] | None = None,
    ) -> None:
        self._http = http_client
        self._registry_path = registry_path
        self._base_url = graph_base_url.rstrip("/")
        self._access_token = access_token
        self._token_provider = token_provider

        # Lazily loaded from registry file
        self._agent_emails: dict[str, str] | None = None
        # Cache: agent name -> chat_id
        self._chat_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_registry(self) -> dict[str, str]:
        """Load agent name -> email mapping from registry JSON."""
        if self._agent_emails is None:
            with open(self._registry_path) as f:
                data = json.load(f)
            self._agent_emails = {}
            for entry in data:
                name = entry.get("name", "")
                email = entry.get("email")
                if name and email:
                    self._agent_emails[name] = email
        return self._agent_emails

    def _require_token(self) -> str:
        """Return current token or raise ``TeamsAuthError``."""
        if not self._access_token:
            raise TeamsAuthError(
                "No Graph API access token configured. "
                "Run graph-token.sh to authenticate."
            )
        return self._access_token

    def _headers(self, token: str | None = None) -> dict[str, str]:
        tok = token or self._require_token()
        return {
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Token refresh (can be mocked in tests)
    # ------------------------------------------------------------------

    async def refresh_access_token(self) -> str:
        """Refresh the access token via the configured provider.

        Returns the new token string.  Override or mock in tests.
        """
        if self._token_provider:
            new_token = await self._token_provider()
            self._access_token = new_token
            return new_token
        raise TeamsAuthError("No token provider configured; cannot refresh.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve_chat_id(self, agent_name: str) -> str:
        """Resolve *agent_name* -> Teams chat ID via Graph ``/me/chats``.

        Raises
        ------
        ValueError
            If the agent name is not in the registry.
        TeamsAuthError
            If no access token is configured.
        """
        if agent_name in self._chat_id_cache:
            return self._chat_id_cache[agent_name]

        registry = self._load_registry()
        email = registry.get(agent_name)
        if not email:
            raise ValueError(
                f"Agent '{agent_name}' not found in registry"
            )

        # Use httpx.URL to prevent percent-encoding of $ in OData params
        raw_url = httpx.URL(f"{self._base_url}/me/chats?$top=50&$expand=members")
        resp = await self._http.get(raw_url, headers=self._headers())

        if resp.status_code != 200:
            body = resp.text[:500]
            raise RuntimeError(
                f"Graph API /me/chats failed: {resp.status_code} {body}"
            )

        data = resp.json()
        chats = data.get("value", [])
        logger.info(
            "resolve_chat_id: looking for %s in %d chats (url=%s, status=%d)",
            email, len(chats), str(resp.url)[:120], resp.status_code,
        )
        for chat in chats:
            members = chat.get("members", [])
            for member in members:
                member_email = member.get("email", "")
                if member_email.lower() == email.lower():
                    chat_id = chat["id"]
                    self._chat_id_cache[agent_name] = chat_id
                    logger.info("resolve_chat_id: found chat %s for %s", chat_id[:40], email)
                    return chat_id

        # Log all member emails for debugging
        all_emails = []
        for chat in chats[:10]:
            for m in chat.get("members", []):
                e = m.get("email", "")
                if e:
                    all_emails.append(e)
        logger.warning(
            "resolve_chat_id: %s not found. Sample emails in chats: %s",
            email, all_emails[:20],
        )

        raise ValueError(
            f"No 1:1 chat found for agent '{agent_name}' ({email})"
        )

    async def send_message(self, agent_name: str, content: str) -> dict:
        """Send a message to an agent's Teams chat.

        On a 401 response, refreshes the token and retries exactly once.

        Returns
        -------
        dict  with at least ``{"id": "..."}`` from Graph API.
        """
        self._require_token()
        chat_id = await self.resolve_chat_id(agent_name)
        url = f"{self._base_url}/chats/{chat_id}/messages"
        body = {"body": {"content": content}}

        resp = await self._http.post(url, headers=self._headers(), json=body)

        # 401 → refresh and retry once
        if resp.status_code == 401:
            new_token = await self.refresh_access_token()
            resp = await self._http.post(
                url, headers=self._headers(token=new_token), json=body
            )

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Graph API send failed: {resp.status_code}"
            )

        return resp.json()

    async def read_messages(
        self, agent_name: str, count: int = 10
    ) -> list[dict]:
        """Read recent messages from an agent's Teams chat.

        Returns
        -------
        list of dicts with keys ``sender``, ``timestamp``, ``content``.
        """
        self._require_token()
        chat_id = await self.resolve_chat_id(agent_name)
        url = f"{self._base_url}/chats/{chat_id}/messages"

        resp = await self._http.get(
            url,
            headers=self._headers(),
            params={"$top": count},
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Graph API read failed: {resp.status_code}"
            )

        messages: list[dict] = []
        for msg in resp.json().get("value", []):
            sender = (
                msg.get("from", {}).get("user", {}).get("displayName", "unknown")
            )
            messages.append(
                {
                    "sender": sender,
                    "timestamp": msg.get("createdDateTime", ""),
                    "content": msg.get("body", {}).get("content", ""),
                }
            )
        return messages

    # ------------------------------------------------------------------
    # Blocker detection (synchronous — no HTTP)
    # ------------------------------------------------------------------

    def detect_blocker(self, content: str) -> dict:
        """Check message text for blocker / decision-needed markers.

        Returns ``{"has_blocker": True/False}``.
        """
        if _BLOCKER_RE.search(content) or _DECISION_RE.search(content):
            return {"has_blocker": True}
        return {"has_blocker": False}
