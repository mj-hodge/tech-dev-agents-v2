"""Microsoft Teams adapter using M365 CLI auth + Graph API direct calls.

Uses M365 CLI token for auth, but calls Graph API directly via aiohttp
for speed (~200ms vs ~3s for CLI subprocess).  Polls for new messages
every 1 second.  No Bot Framework, no webhooks, no admin permissions.

Required: `m365` CLI installed and authenticated as the bot user.
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
DEFAULT_POLL_INTERVAL = int(os.environ.get("TEAMS_POLL_INTERVAL", "1"))
M365_CMD = "m365"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_m365_token() -> str:
    """Extract the current access token from M365 CLI."""
    result = subprocess.run(
        [M365_CMD, "util", "accesstoken", "get", "--resource", "https://graph.microsoft.com"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not get M365 token: {result.stderr.strip()}")
    return result.stdout.strip().strip('"')


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def check_teams_requirements() -> list[str]:
    """Return unmet requirements."""
    missing: list[str] = []
    if not _HERMES_BASE_AVAILABLE:
        missing.append("hermes gateway.platforms.base not importable")
    try:
        result = subprocess.run(
            [M365_CMD, "status", "--output", "json"],
            capture_output=True, text=True, timeout=10,
        )
        status = json.loads(result.stdout.strip()) if result.returncode == 0 else {}
        if not status.get("connectedAs"):
            missing.append("m365 CLI not logged in")
    except Exception as e:
        missing.append(f"m365 CLI not available: {e}")
    return missing


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class TeamsAdapter(BasePlatformAdapter):
    """Microsoft Teams adapter using M365 CLI auth + direct Graph API.

    Polls for messages via Graph API.  Sends via Graph API.
    Auth tokens come from M365 CLI (auto-refreshing).

    Environment variables
    ----------------------
    TEAMS_BOT_USER_ID       Bot's Entra object ID (skip own messages)
    TEAMS_POLL_INTERVAL     Seconds between polls (default: 1)
    """

    def __init__(self, config: "PlatformConfig") -> None:
        logger.info("TeamsAdapter.__init__ (M365 + Graph API mode)")
        super().__init__(config, Platform.TEAMS)

        self._bot_user_id: str = os.environ.get("TEAMS_BOT_USER_ID", "")
        self._poll_interval: int = DEFAULT_POLL_INTERVAL
        self._poll_task: asyncio.Task | None = None
        self._token_refresh_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._token: str = ""
        self._token_expires: float = 0
        self._running = False

        # chat_id -> id of last processed message
        self._last_seen: dict[str, str] = {}
        # chat_id -> {user_id, user_name}
        self._chat_users: dict[str, dict] = {}

        # Get bot identity
        try:
            result = subprocess.run(
                [M365_CMD, "status", "--output", "json"],
                capture_output=True, text=True, timeout=10,
            )
            status = json.loads(result.stdout.strip()) if result.returncode == 0 else {}
            self._connected_as = status.get("connectedAs", "")
            logger.info("M365 CLI connected as: %s", self._connected_as)
        except Exception:
            self._connected_as = ""

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _refresh_token(self) -> str:
        """Get a fresh Graph API token from M365 CLI."""
        loop = asyncio.get_event_loop()
        token = await loop.run_in_executor(None, _get_m365_token)
        self._token = token
        self._token_expires = time.monotonic() + 1800  # refresh every 30 min
        logger.debug("Graph token refreshed (%d chars)", len(token))
        return token

    async def _token_refresh_loop(self) -> None:
        """Background loop to keep the token fresh."""
        while self._running:
            await asyncio.sleep(1800)  # refresh every 30 min
            try:
                await self._refresh_token()
            except Exception as e:
                logger.error("Token refresh failed: %s", e)

    async def _get_headers(self) -> dict:
        """Return auth headers, refreshing token if needed."""
        if not self._token or time.monotonic() > self._token_expires:
            await self._refresh_token()
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
                    await self._refresh_token()
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
                    await self._refresh_token()
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
                await self._graph_post("/me/presence/setPresence", {
                    "sessionId": os.environ.get("TEAMS_CLIENT_ID", "dc0cba0b-f12d-40da-88f0-adcda94075be"),
                    "availability": availability,
                    "activity": activity,
                    "expirationDuration": "PT4H",
                })
                logger.info("Presence set to %s/%s", availability, activity)
                return
            except Exception as e:
                if attempt == 0 and "401" in str(e):
                    await self._refresh_token()
                    continue
                logger.warning("Could not set presence: %s", e)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _get_chats(self) -> list[dict]:
        """List chats the bot is in."""
        data = await self._graph_get("/me/chats?$top=50")
        return data.get("value", [])

    async def _get_messages(self, chat_id: str) -> list[dict]:
        """Get recent messages from a chat (newest first)."""
        try:
            data = await self._graph_get(
                f"/me/chats/{chat_id}/messages?$top=5&$orderby=createdDateTime desc"
            )
            return data.get("value", [])
        except Exception as e:
            logger.debug("Could not fetch messages for %s: %s", chat_id[:20], e)
            return []

    # STORY-304: Poll-based presence monitor removed.
    # Presence is now event-driven: ops-console pushes via POST /internal/presence
    # on dispatch claim/complete/fail. Morris uses heartbeat-driven presence.

    # ------------------------------------------------------------------
    # Morris heartbeat presence (STORY-304 AC-5)
    # ------------------------------------------------------------------

    async def _get_sdk_count(self) -> int:
        """Count running claude_sdk_tool processes."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: subprocess.run(
                ["pgrep", "-fc", "claude_sdk_tool"],
                capture_output=True, text=True, timeout=3,
            ))
            return int(result.stdout.strip()) if result.returncode == 0 else 0
        except Exception:
            return 0

    async def _get_unread_inbox_count(self) -> int:
        """Count unread messages across active chats."""
        try:
            count = 0
            chats = await self._get_chats()
            for chat in chats:
                chat_id = chat.get("id", "")
                if not chat_id:
                    continue
                msgs = await self._get_messages(chat_id)
                last_seen = self._last_seen.get(chat_id)
                if last_seen is None:
                    continue
                for msg in msgs:
                    if msg.get("id") == last_seen:
                        break
                    # Skip own messages
                    from_info = msg.get("from") or {}
                    user_info = from_info.get("user") or {}
                    sender_id = user_info.get("id", "")
                    if self._bot_user_id and sender_id == self._bot_user_id:
                        continue
                    count += 1
            return count
        except Exception:
            return 0

    async def _morris_heartbeat_tick(self) -> None:
        """Single tick of Morris heartbeat presence logic.

        Morris doesn't take dispatch tickets, so presence is determined locally:
        - sdk_count + unread_inbox > 0 → Busy
        - else → Available
        """
        sdk_count = await self._get_sdk_count()
        unread = await self._get_unread_inbox_count()
        is_active = (sdk_count + unread) > 0
        if is_active:
            await self._set_presence("Busy", "InACall")
        else:
            await self._set_presence("Available", "Available")

    async def _morris_heartbeat_loop(self) -> None:
        """Background loop for Morris presence heartbeat. Runs every 15s."""
        while self._running:
            try:
                await self._morris_heartbeat_tick()
            except Exception as e:
                logger.debug("Morris heartbeat error: %s", e)
            await asyncio.sleep(15)

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

        # STORY-304: Presence is now pushed by ops-console on dispatch events.
        # Direct message handling no longer toggles presence.
        await self.handle_message(event)

    # ------------------------------------------------------------------
    # BasePlatformAdapter interface
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Start polling."""
        logger.info("connect() - M365 + Graph API adapter")

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
            await self._refresh_token()

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
            self._token_refresh_task = asyncio.ensure_future(self._token_refresh_loop())
            # STORY-304: presence polling task removed — presence is event-driven now
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

        for task in (self._poll_task, self._token_refresh_task):
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
                f"/me/chats/{chat_id}/messages",
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
