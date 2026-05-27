"""Microsoft Teams adapter using MSAL client-credentials auth + Graph API.

STORY-229: Replaced M365 CLI delegated-user tokens with MSAL application
permissions (client credentials flow).  All Graph API calls use explicit
``/users/{bot_user_id}/`` paths instead of ``/me/``.

Polls for new messages every 1 second.  No Bot Framework, no webhooks.

Required env vars: TEAMS_CLIENT_ID, TEAMS_CLIENT_SECRET, TEAMS_TENANT_ID,
TEAMS_BOT_USER_ID.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any

import aiohttp

try:
    import msal  # type: ignore
    _MSAL_AVAILABLE = True
except ImportError:
    _MSAL_AVAILABLE = False

logger = logging.getLogger(__name__)

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[teams-m365] %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Hermes base imports
# ---------------------------------------------------------------------------

try:
    from gateway.platforms.base import (
        BasePlatformAdapter, MessageEvent, MessageType,
        PlatformConfig, Platform, SendResult,
    )
    _HERMES_BASE_AVAILABLE = True
except ImportError:
    try:
        from hermes.gateway.platforms.base import (
            BasePlatformAdapter, MessageEvent, MessageType,
            PlatformConfig, Platform, SendResult,
        )
        _HERMES_BASE_AVAILABLE = True
    except ImportError:
        _HERMES_BASE_AVAILABLE = False
        BasePlatformAdapter = object
        PlatformConfig = None
        Platform = None

# ---------------------------------------------------------------------------
# Loki integration (conditional)
# ---------------------------------------------------------------------------

try:
    _tda_root = "/opt/tech-dev-agents"
    if os.path.isdir(_tda_root) and _tda_root not in sys.path:
        sys.path.insert(0, _tda_root)
    from tech_dev_agents.loki_logging import setup_loki_logging
    _LOKI_AVAILABLE = True
except ImportError:
    _LOKI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
DEFAULT_POLL_INTERVAL = int(os.environ.get("TEAMS_POLL_INTERVAL", "1"))


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def check_teams_requirements() -> list[str]:
    """Return unmet requirements for MSAL client-credentials auth."""
    missing: list[str] = []
    if not _HERMES_BASE_AVAILABLE:
        missing.append("hermes gateway.platforms.base not importable")
    if not _MSAL_AVAILABLE:
        missing.append("msal package not installed (pip install msal)")
    required_env = [
        "TEAMS_CLIENT_ID",
        "TEAMS_CLIENT_SECRET",
        "TEAMS_TENANT_ID",
        "TEAMS_BOT_USER_ID",
    ]
    for var in required_env:
        if not os.environ.get(var):
            missing.append(f"Environment variable {var} is not set")
    return missing


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class TeamsAdapter(BasePlatformAdapter):
    """Microsoft Teams adapter using MSAL client-credentials + direct Graph API.

    Polls for messages via Graph API.  Sends via Graph API.
    Auth tokens come from MSAL ``ConfidentialClientApplication``
    (client-credentials flow — application permissions, not delegated).

    Environment variables
    ----------------------
    TEAMS_CLIENT_ID         Entra app-registration client ID
    TEAMS_CLIENT_SECRET     App-registration client secret
    TEAMS_TENANT_ID         Entra tenant ID
    TEAMS_BOT_USER_ID       Bot's Entra object ID (used for /users/{id}/ calls + skip own messages)
    TEAMS_POLL_INTERVAL     Seconds between polls (default: 1)
    """

    def __init__(self, config: "PlatformConfig") -> None:
        logger.info("TeamsAdapter.__init__ (MSAL + Graph API mode)")
        super().__init__(config, Platform.TEAMS)

        # Auth config (MSAL client credentials)
        self._client_id: str = os.environ.get("TEAMS_CLIENT_ID", "")
        self._client_secret: str = os.environ.get("TEAMS_CLIENT_SECRET", "")
        self._tenant_id: str = os.environ.get("TEAMS_TENANT_ID", "")
        self._msal_app: Any = None

        self._bot_user_id: str = os.environ.get("TEAMS_BOT_USER_ID", "")
        self._poll_interval: int = DEFAULT_POLL_INTERVAL
        self._poll_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._token: str = ""
        self._token_expires: float = 0
        self._running = False

        # chat_id -> id of last processed message
        self._last_seen: dict[str, str] = {}
        # chat_id -> {user_id, user_name}
        self._chat_users: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Token management (MSAL client credentials)
    # ------------------------------------------------------------------

    def _build_msal_app(self) -> None:
        """Initialise the MSAL confidential client (idempotent)."""
        if self._msal_app is not None:
            return
        authority = f"https://login.microsoftonline.com/{self._tenant_id}"
        self._msal_app = msal.ConfidentialClientApplication(
            self._client_id,
            authority=authority,
            client_credential=self._client_secret,
        )

    async def _get_token(self) -> str:
        """Return a valid Graph API access token, refreshing via MSAL when near expiry."""
        if self._token and time.monotonic() < self._token_expires - 60:
            return self._token

        self._build_msal_app()

        loop = asyncio.get_event_loop()
        result: dict = await loop.run_in_executor(
            None,
            lambda: self._msal_app.acquire_token_for_client(scopes=GRAPH_SCOPES),
        )

        if "access_token" not in result:
            error = (
                result.get("error_description")
                or result.get("error")
                or str(result)
            )
            raise RuntimeError(f"MSAL token acquisition failed: {error}")

        self._token = result["access_token"]
        expires_in: int = result.get("expires_in", 3600)
        self._token_expires = time.monotonic() + expires_in
        logger.info("Graph access token acquired (expires in %ds)", expires_in)
        return self._token

    async def _get_headers(self) -> dict:
        """Return auth headers, refreshing token via MSAL if needed."""
        await self._get_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Graph API calls
    # ------------------------------------------------------------------

    async def _graph_get(self, path: str) -> Any:
        """GET from Graph API (auto-retries on 401 with token refresh)."""
        for attempt in range(2):
            headers = await self._get_headers()
            url = f"{GRAPH_BASE}{path}"
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 401 and attempt == 0:
                    logger.debug("Graph 401 — refreshing token and retrying")
                    self._token = ""  # force MSAL re-acquisition
                    self._token_expires = 0
                    continue
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"Graph GET {path}: {resp.status} {body[:300]}")
                return await resp.json()

    async def _graph_post(self, path: str, body: dict) -> Any:
        """POST to Graph API (auto-retries on 401 with token refresh)."""
        for attempt in range(2):
            headers = await self._get_headers()
            url = f"{GRAPH_BASE}{path}"
            async with self._session.post(url, headers=headers, json=body) as resp:
                if resp.status == 401 and attempt == 0:
                    logger.debug("Graph POST 401 — refreshing token and retrying")
                    self._token = ""  # force MSAL re-acquisition
                    self._token_expires = 0
                    continue
                if resp.status >= 400:
                    resp_body = await resp.text()
                    raise RuntimeError(f"Graph POST {path}: {resp.status} {resp_body[:300]}")
                text = await resp.text()
                return json.loads(text) if text.strip() else {}

    async def _graph_patch(self, path: str, body: dict) -> None:
        """PATCH to Graph API."""
        headers = await self._get_headers()
        url = f"{GRAPH_BASE}{path}"
        async with self._session.patch(url, headers=headers, json=body) as resp:
            if resp.status >= 400:
                resp_body = await resp.text()
                logger.warning("Graph PATCH %s: %s %s", path, resp.status, resp_body[:200])

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    async def _set_presence(self, availability: str, activity: str) -> None:
        """Set the bot's Teams presence. Refreshes token first to avoid 401."""
        for attempt in range(2):
            try:
                await self._graph_post(f"/users/{self._bot_user_id}/presence/setPresence", {
                    "sessionId": self._client_id,
                    "availability": availability,
                    "activity": activity,
                    "expirationDuration": "PT4H",
                })
                logger.info("Presence set to %s/%s", availability, activity)
                return
            except Exception as e:
                if attempt == 0 and "401" in str(e):
                    self._token = ""
                    self._token_expires = 0
                    continue
                logger.warning("Could not set presence: %s", e)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _get_chats(self) -> list[dict]:
        """List chats the bot is in."""
        data = await self._graph_get(f"/users/{self._bot_user_id}/chats?$top=50")
        return data.get("value", [])

    async def _get_messages(self, chat_id: str) -> list[dict]:
        """Get recent messages from a chat (newest first)."""
        try:
            data = await self._graph_get(
                f"/users/{self._bot_user_id}/chats/{chat_id}/messages"
                f"?$top=5&$orderby=createdDateTime desc"
            )
            return data.get("value", [])
        except Exception as e:
            logger.debug("Could not fetch messages for %s: %s", chat_id[:20], e)
            return []

    async def _presence_monitor_loop(self) -> None:
        """Monitor active sessions and update presence accordingly.

        Busy when: processing a message OR claude-sdk subprocess is running.
        """
        last_state = "Available"
        self._is_busy = False  # set by _process_message
        while self._running:
            try:
                # Check if claude-sdk is running as a background process
                has_sdk_running = False
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: subprocess.run(
                    ["pgrep", "-f", "claude_sdk_tool"],
                    capture_output=True, timeout=3,
                ))
                has_sdk_running = result.returncode == 0

                is_active = self._is_busy or has_sdk_running
                desired = "Busy" if is_active else "Available"
                if desired != last_state:
                    activity = "InACall" if is_active else "Available"
                    await self._set_presence(desired, activity)
                    last_state = desired
            except Exception as e:
                logger.debug("Presence monitor error: %s", e)
            await asyncio.sleep(5)

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        logger.info("Poll loop started (interval=%ds)", self._poll_interval)

        while self._running:
            try:
                chats = await self._get_chats()

                for chat in chats:
                    chat_id = chat.get("id", "")
                    if not chat_id:
                        continue

                    messages = await self._get_messages(chat_id)
                    last_seen = self._last_seen.get(chat_id)

                    # First poll for this chat — just mark position, don't process
                    if last_seen is None:
                        if messages:
                            self._last_seen[chat_id] = messages[0].get("id", "")
                        continue

                    # Find new messages
                    new_msgs = []
                    for msg in messages:
                        if msg.get("id") == last_seen:
                            break
                        new_msgs.append(msg)

                    if new_msgs:
                        self._last_seen[chat_id] = messages[0].get("id", "")

                    # Process oldest first
                    for msg in reversed(new_msgs):
                        await self._process_message(chat_id, msg)

            except Exception as exc:
                logger.error("Poll error: %s", exc, exc_info=True)

            await asyncio.sleep(self._poll_interval)

    async def _process_message(self, chat_id: str, msg: dict) -> None:
        """Process a single message."""
        from_info = msg.get("from") or {}
        user_info = from_info.get("user") or {}
        sender_id = user_info.get("id", "")
        sender_name = user_info.get("displayName", "Unknown")

        # Skip own messages
        if self._bot_user_id and sender_id == self._bot_user_id:
            return
        if not sender_id:
            return

        # Extract text
        body = msg.get("body") or {}
        raw = body.get("content", "")
        content = _strip_html(raw) if body.get("contentType") == "html" else raw.strip()
        if not content:
            return

        self._chat_users[chat_id] = {"user_id": sender_id, "user_name": sender_name}

        logger.info(
            "Inbound chat=%s sender=%s: %r",
            chat_id[:20], sender_name, content[:120],
        )

        source = self.build_source(
            chat_id=chat_id, user_id=sender_id,
            user_name=sender_name, chat_type="dm",
        )
        event = MessageEvent(
            text=content, message_type=MessageType.TEXT,
            source=source, raw_message=msg,
            message_id=msg.get("id", ""),
        )

        self._is_busy = True
        await self._set_presence("Busy", "InACall")
        try:
            await self.handle_message(event)
        finally:
            self._is_busy = False
            await self._set_presence("Available", "Available")

    # ------------------------------------------------------------------
    # BasePlatformAdapter interface
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Start polling."""
        logger.info("connect() - MSAL + Graph API adapter")

        if _LOKI_AVAILABLE and os.environ.get("LOKI_URL"):
            try:
                loki_logger = setup_loki_logging("teams-m365-adapter")
                for h in loki_logger.handlers:
                    logger.addHandler(h)
                logger.info("Loki logging attached")
            except Exception as exc:
                logger.warning("Loki setup failed: %s", exc)

        missing = check_teams_requirements()
        if missing:
            for m in missing:
                logger.error("Requirement not met: %s", m)
            return False

        try:
            self._session = aiohttp.ClientSession()
            await self._get_token()

            # Set presence
            await self._set_presence("Available", "Available")

            # Initialize chat positions
            chats = await self._get_chats()
            for chat in chats:
                cid = chat.get("id", "")
                if cid:
                    msgs = await self._get_messages(cid)
                    if msgs:
                        self._last_seen[cid] = msgs[0].get("id", "")
            logger.info("Initialized %d chat(s)", len(self._last_seen))

            self._running = True
            self._poll_task = asyncio.ensure_future(self._poll_loop())
            self._presence_task = asyncio.ensure_future(self._presence_monitor_loop())
            self._mark_connected()
            logger.info("Adapter connected - polling every %ds", self._poll_interval)
            return True

        except Exception as exc:
            logger.error("connect() failed: %s", exc, exc_info=True)
            if self._session:
                await self._session.close()
            return False

    async def disconnect(self) -> None:
        """Stop polling."""
        logger.info("Disconnecting...")
        self._running = False

        await self._set_presence("Offline", "OffWork")

        for task in (self._poll_task, getattr(self, '_presence_task', None)):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._session:
            await self._session.close()
            self._session = None

    def _markdown_to_teams_html(self, text: str) -> str:
        """Convert markdown formatting to Teams-compatible HTML."""
        # Fenced code blocks: ```lang\n...\n``` -> <pre>...</pre>
        text = re.sub(
            r"```(?:\w*)\n(.*?)```",
            lambda m: f"<pre>{html.escape(m.group(1).strip())}</pre>",
            text, flags=re.DOTALL,
        )
        # Inline code: `...` -> <code>...</code>
        text = re.sub(
            r"`([^`]+)`",
            lambda m: f"<code>{html.escape(m.group(1))}</code>",
            text,
        )
        # Bold: **...** -> <b>...</b>
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        # Italic: *...* -> <i>...</i>
        text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
        # Newlines -> <br>
        text = text.replace("\n", "<br>")
        return text

    async def send(
        self, chat_id: str, content: str,
        reply_to: str | None = None, metadata: dict | None = None,
    ) -> "SendResult":
        """Send a message via Graph API with HTML formatting."""
        try:
            formatted = self._markdown_to_teams_html(content)
            data = await self._graph_post(
                f"/users/{self._bot_user_id}/chats/{chat_id}/messages",
                {"body": {"contentType": "html", "content": formatted}},
            )
            msg_id = data.get("id", "")
            logger.info("Sent to %s (%d chars)", chat_id[:20], len(content))
            return SendResult(success=True, message_id=msg_id)
        except Exception as exc:
            logger.error("send() failed: %s", exc, exc_info=True)
            return SendResult(success=False, message_id="")

    async def send_typing(self, chat_id: str, metadata: dict | None = None) -> None:
        """Send typing indicator."""
        try:
            # Graph API doesn't have a direct typing indicator for chats,
            # but we can use the Teams-specific endpoint
            pass
        except Exception:
            pass

    async def send_image(
        self, chat_id: str, image_data: bytes,
        caption: str | None = None, metadata: dict | None = None,
    ) -> "SendResult":
        """Not supported."""
        return SendResult(success=False, message_id="")

    async def get_chat_info(self, chat_id: str) -> dict:
        """Return basic chat info."""
        cached = self._chat_users.get(chat_id, {})
        return {
            "chat_id": chat_id, "chat_type": "dm",
            "user_id": cached.get("user_id", ""),
            "user_name": cached.get("user_name", ""),
        }
