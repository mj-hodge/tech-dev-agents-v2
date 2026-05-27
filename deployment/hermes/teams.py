"""Microsoft Teams platform adapter using Graph API change notifications."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

# Ensure our logs reach stdout even if the host app doesn't configure this logger
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[teams] %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Optional imports — fail gracefully so check_teams_requirements() can report
# ---------------------------------------------------------------------------

try:
    import msal  # type: ignore

    _MSAL_AVAILABLE = True
except ImportError:
    _MSAL_AVAILABLE = False

try:
    import jwt  # type: ignore
    from jwt import PyJWKClient  # type: ignore

    _PYJWT_AVAILABLE = True
except ImportError:
    _PYJWT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Hermes base imports — resolved at runtime inside the installed Hermes venv
# ---------------------------------------------------------------------------

try:
    from gateway.platforms.base import (  # type: ignore
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        PlatformConfig,
        Platform,
        SendResult,
    )

    _HERMES_BASE_AVAILABLE = True
except ImportError:
    try:
        # Fallback: try with hermes prefix (installed package mode)
        from hermes.gateway.platforms.base import (  # type: ignore
            BasePlatformAdapter,
            MessageEvent,
            MessageType,
            PlatformConfig,
            Platform,
            SendResult,
        )

        _HERMES_BASE_AVAILABLE = True
    except ImportError:
        _HERMES_BASE_AVAILABLE = False
        # Provide stubs so the module still loads for requirement-checking
        BasePlatformAdapter = object  # type: ignore
        PlatformConfig = None  # type: ignore
        Platform = None  # type: ignore

# ---------------------------------------------------------------------------
# Loki integration (conditional — no-op when LOKI_URL is unset)
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
BF_SCOPES = ["https://api.botframework.com/.default"]
SUBSCRIPTION_LIFETIME_MINUTES = 55
SUBSCRIPTION_RENEW_MINUTES = 50
BF_OPENID_CONFIGURATION_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
DEFAULT_BF_SERVICE_URL_PREFIXES = ("https://smba.trafficmanager.net/",)


# ---------------------------------------------------------------------------
# Requirement check
# ---------------------------------------------------------------------------


def check_teams_requirements() -> list[str]:
    """Return a list of unmet requirements for the Teams adapter.

    Returns an empty list when all requirements are satisfied.
    """
    missing: list[str] = []

    if not _MSAL_AVAILABLE:
        missing.append("msal package is not installed (pip install msal)")

    if not _HERMES_BASE_AVAILABLE:
        missing.append(
            "hermes.gateway.platforms.base could not be imported — "
            "ensure the Hermes venv is active"
        )

    required_env = [
        "TEAMS_CLIENT_ID",
        "TEAMS_CLIENT_SECRET",
        "TEAMS_TENANT_ID",
        "TEAMS_NOTIFICATION_HOST",
    ]
    for var in required_env:
        if not os.environ.get(var):
            missing.append(f"Environment variable {var} is not set")

    return missing


# ---------------------------------------------------------------------------
# HTML stripping helper
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode HTML entities from a string."""
    # Remove HTML tags
    no_tags = re.sub(r"<[^>]+>", "", text)
    # Decode entities
    return html.unescape(no_tags).strip()


# ---------------------------------------------------------------------------
# Teams adapter
# ---------------------------------------------------------------------------


class TeamsAdapter(BasePlatformAdapter):
    """Microsoft Teams adapter — Bot Framework receive + send.

    Default (BF-only) mode:
      - Receives messages via Bot Framework ``/api/messages`` endpoint
      - Sends messages via Bot Framework proactive messaging
      - No admin Graph permissions required

    Optional Graph subscription mode (set TEAMS_SUBSCRIPTION_RESOURCE):
      - Also receives messages via Graph change-notification webhooks
      - Requires ``Chat.Read.All`` (admin consent) for getAllMessages

    Required environment variables
    --------------------------------
    TEAMS_CLIENT_ID            Entra app-registration client ID
    TEAMS_CLIENT_SECRET        App-registration client secret
    TEAMS_TENANT_ID            Entra tenant ID
    TEAMS_BOT_USER_ID          Entra object ID of the bot user (skip self-messages)
    TEAMS_NOTIFICATION_HOST    Public FQDN used for the webhook URL (no trailing slash)
    TEAMS_WEBHOOK_PORT         Port for the local aiohttp notification listener (default 3978)
    TEAMS_SUBSCRIPTION_RESOURCE (Optional) Graph subscription resource path, e.g.
                               /users/{TEAMS_BOT_USER_ID}/chats/getAllMessages
                               Leave unset for BF-only mode (no admin permissions).
    """

    def __init__(self, config: "PlatformConfig") -> None:
        logger.info("TeamsAdapter.__init__ called")
        super().__init__(config, Platform.TEAMS)

        # Auth config
        self._client_id: str = os.environ.get("TEAMS_CLIENT_ID", "")
        self._client_secret: str = os.environ.get("TEAMS_CLIENT_SECRET", "")
        self._tenant_id: str = os.environ.get("TEAMS_TENANT_ID", "")

        # Runtime config
        self._bot_user_id: str = os.environ.get("TEAMS_BOT_USER_ID", "")
        self._notification_host: str = os.environ.get(
            "TEAMS_NOTIFICATION_HOST", ""
        ).rstrip("/")
        self._webhook_port: int = int(
            os.environ.get("TEAMS_WEBHOOK_PORT", "3978")
        )
        # Graph change-notification subscription is opt-in.
        # Default is BF-only mode (no admin Graph permissions needed).
        # Set TEAMS_SUBSCRIPTION_RESOURCE to enable Graph notifications
        # (requires Chat.Read.All — admin consent).
        configured_resource = os.environ.get(
            "TEAMS_SUBSCRIPTION_RESOURCE", ""
        ).strip()
        self._subscription_resource = configured_resource  # empty = BF-only
        self._membership_fail_open = (
            os.environ.get("TEAMS_MEMBERSHIP_FAIL_OPEN", "false").lower()
            in ("1", "true", "yes")
        )

        # State — Graph API
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._msal_app: Any = None
        self._subscription_id: str = ""
        self._client_state: str = secrets.token_hex(32)
        self._http_runner: web.AppRunner | None = None
        self._renew_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._session: aiohttp.ClientSession | None = None

        # Chat membership cache: chat_id -> is_bot_member
        self._known_bot_chats: dict[str, bool] = {}

        # Bot Framework state (for sending messages)
        self._bf_token: str = ""
        self._bf_token_expires_at: float = 0.0
        # graph chat_id -> sender AAD user_id (populated on ingest)
        self._chat_users: dict[str, str] = {}
        # user AAD id -> {service_url, conversation_id, bot_id} (populated from BF activities)
        self._bf_conversations: dict[str, dict[str, str]] = {}
        # graph chat_id -> {service_url, conversation_id, bot_id}
        self._bf_conversations_by_chat: dict[str, dict[str, str]] = {}
        self._bf_jwks_client: Any = None
        self._bf_issuer: str = ""
        self._bf_service_url_prefixes: tuple[str, ...] = tuple(
            x.strip()
            for x in os.environ.get(
                "TEAMS_BF_ALLOWED_SERVICE_URL_PREFIXES",
                ",".join(DEFAULT_BF_SERVICE_URL_PREFIXES),
            ).split(",")
            if x.strip()
        ) or DEFAULT_BF_SERVICE_URL_PREFIXES

    # ------------------------------------------------------------------
    # Token management
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
        """Return a valid Graph API access token, refreshing when near expiry."""
        if self._token and time.monotonic() < self._token_expires_at - 60:
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
        self._token_expires_at = time.monotonic() + expires_in
        logger.info("Graph access token acquired (expires in %ds)", expires_in)
        return self._token

    async def _get_bf_token(self) -> str:
        """Return a valid Bot Framework access token."""
        if self._bf_token and time.monotonic() < self._bf_token_expires_at - 60:
            return self._bf_token

        self._build_msal_app()

        loop = asyncio.get_event_loop()
        result: dict = await loop.run_in_executor(
            None,
            lambda: self._msal_app.acquire_token_for_client(scopes=BF_SCOPES),
        )

        if "access_token" not in result:
            error = (
                result.get("error_description")
                or result.get("error")
                or str(result)
            )
            raise RuntimeError(f"Bot Framework token acquisition failed: {error}")

        self._bf_token = result["access_token"]
        expires_in: int = result.get("expires_in", 3600)
        self._bf_token_expires_at = time.monotonic() + expires_in
        logger.info("BF token acquired (expires in %ds)", expires_in)
        return self._bf_token

    # ------------------------------------------------------------------
    # Graph API helpers
    # ------------------------------------------------------------------

    async def _graph_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        expect_json: bool = True,
    ) -> Any:
        """Send an authenticated request to the Graph API."""
        token = await self._get_token()
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        assert self._session is not None
        async with self._session.request(
            method, url, headers=headers, json=json_body
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(
                    f"Graph API {method} {url} returned {resp.status}: {body[:500]}"
                )
            if expect_json:
                return await resp.json()
            return await resp.text()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    async def _cleanup_stale_subscriptions(self) -> None:
        """Delete stale chat-message subscriptions for this app."""
        try:
            data = await self._graph_request("GET", "/subscriptions")
            for sub in data.get("value", []):
                resource = sub.get("resource", "")
                if resource.endswith("/chats/getAllMessages"):
                    sub_id = sub["id"]
                    logger.info(
                        "Deleting stale subscription %s (resource=%s)",
                        sub_id,
                        resource,
                    )
                    try:
                        await self._graph_request(
                            "DELETE",
                            f"/subscriptions/{sub_id}",
                            expect_json=False,
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not delete subscription %s: %s", sub_id, e
                        )
        except Exception as e:
            logger.warning("Could not list subscriptions: %s", e)

    async def _create_subscription(self) -> None:
        """Create a Graph change-notification subscription for chat messages."""
        await self._cleanup_stale_subscriptions()

        expiration = datetime.now(timezone.utc) + timedelta(
            minutes=SUBSCRIPTION_LIFETIME_MINUTES
        )
        expiration_str = expiration.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        notification_url = f"{self._notification_host}/api/notifications"

        payload = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": self._subscription_resource,
            "expirationDateTime": expiration_str,
            "clientState": self._client_state,
        }

        data = await self._graph_request(
            "POST", "/subscriptions", json_body=payload
        )
        self._subscription_id = data["id"]
        logger.info(
            "Subscription created id=%s expires=%s",
            self._subscription_id,
            expiration_str,
        )

    async def _renew_subscription(self) -> None:
        """Extend the expiry of the existing subscription."""
        if not self._subscription_id:
            logger.warning("No subscription to renew — creating a new one")
            await self._create_subscription()
            return

        expiration = datetime.now(timezone.utc) + timedelta(
            minutes=SUBSCRIPTION_LIFETIME_MINUTES
        )
        expiration_str = expiration.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload = {"expirationDateTime": expiration_str}

        try:
            await self._graph_request(
                "PATCH",
                f"/subscriptions/{self._subscription_id}",
                json_body=payload,
            )
            logger.info("Subscription renewed until %s", expiration_str)
        except RuntimeError as exc:
            if "404" in str(exc):
                logger.warning(
                    "Subscription gone (404) — recreating"
                )
                self._subscription_id = ""
                await self._create_subscription()
            else:
                raise

    async def _subscription_renew_loop(self) -> None:
        """Background task: renew the subscription every SUBSCRIPTION_RENEW_MINUTES."""
        while True:
            await asyncio.sleep(SUBSCRIPTION_RENEW_MINUTES * 60)
            try:
                await self._renew_subscription()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Subscription renewal failed: %s — will retry next cycle",
                    exc,
                )

    # ------------------------------------------------------------------
    # Chat membership check
    # ------------------------------------------------------------------

    async def _is_bot_chat(self, chat_id: str) -> bool:
        """Check if the bot is a member of this chat via Graph API (cached).

        Uses GET /chats/{chat_id}/members and checks for the bot's AAD user ID
        among the member list.  Results are cached for the adapter lifetime.
        In BF-only mode, skip Graph membership check — BF only delivers
        messages for chats the bot is a member of.
        """
        if not self._subscription_resource:
            return True  # BF-only mode — trust BF delivery scope

        if not self._bot_user_id:
            return True  # No bot user ID configured — accept all

        if chat_id in self._known_bot_chats:
            return self._known_bot_chats[chat_id]

        try:
            data = await self._graph_request(
                "GET", f"/chats/{chat_id}/members"
            )
            members = data.get("value", [])
            is_bot = any(
                (m.get("userId") or "") == self._bot_user_id
                for m in members
            )
            self._known_bot_chats[chat_id] = is_bot
            if is_bot:
                logger.debug(
                    "Chat %s… confirmed as bot chat (cached)", chat_id[:20]
                )
            else:
                logger.debug(
                    "Chat %s… is not a bot chat — ignoring", chat_id[:20]
                )
            return is_bot
        except Exception as exc:
            fail_mode = (
                "allowing" if self._membership_fail_open else "blocking"
            )
            logger.warning(
                "Could not check membership for chat %s…: %s — %s",
                chat_id[:20],
                exc,
                fail_mode,
            )
            return self._membership_fail_open

    async def _get_bf_jwt_verifier(self) -> tuple[Any, str]:
        """Get (and cache) BF JWK verifier state."""
        if self._bf_jwks_client is not None and self._bf_issuer:
            return self._bf_jwks_client, self._bf_issuer
        if not _PYJWT_AVAILABLE:
            raise RuntimeError("pyjwt is not installed")
        assert self._session is not None
        async with self._session.get(BF_OPENID_CONFIGURATION_URL) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(
                    f"Failed to fetch BF OpenID config: {resp.status} {body[:200]}"
                )
            metadata = await resp.json()
        jwks_uri = metadata.get("jwks_uri")
        issuer = metadata.get("issuer")
        if not jwks_uri or not issuer:
            raise RuntimeError("BF OpenID config missing jwks_uri/issuer")
        self._bf_jwks_client = PyJWKClient(jwks_uri)
        self._bf_issuer = issuer
        return self._bf_jwks_client, self._bf_issuer

    async def _validate_bf_auth(self, request: web.Request) -> bool:
        """Validate Bot Framework JWT for /api/messages requests."""
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            logger.warning("Missing Bearer token on /api/messages request")
            return False
        token = auth[7:].strip()
        if not token:
            logger.warning("Empty Bearer token on /api/messages request")
            return False
        try:
            jwks_client, expected_issuer = await self._get_bf_jwt_verifier()

            def _decode() -> dict:
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                return jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=self._client_id,
                    issuer=expected_issuer,
                    options={"require": ["exp", "iss", "aud"]},
                )

            loop = asyncio.get_event_loop()
            claims = await loop.run_in_executor(None, _decode)
            service_url = (
                (claims.get("serviceurl") or claims.get("serviceUrl") or "")
                .strip()
            )
            if service_url and not any(
                service_url.startswith(prefix)
                for prefix in self._bf_service_url_prefixes
            ):
                logger.warning(
                    "Rejected BF token with untrusted service URL: %s",
                    service_url,
                )
                return False
            return True
        except Exception as exc:
            logger.warning("BF JWT validation failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Webhook server
    # ------------------------------------------------------------------

    async def _handle_bf_activity(self, request: web.Request) -> web.Response:
        """POST /api/messages — Bot Framework messaging endpoint.

        Captures conversation references from incoming BF activities so we can
        use them later for proactive (reply) messaging.  We do NOT process the
        message content here — that comes via the Graph change-notification
        pipeline.  This endpoint exists solely to learn the service URL and
        conversation ID that Bot Framework assigns.
        """
        if not await self._validate_bf_auth(request):
            return web.Response(status=401, text="Unauthorized")

        try:
            activity: dict = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        activity_type = (activity.get("type") or "").lower()
        service_url = (activity.get("serviceUrl") or "").rstrip("/")
        conversation = activity.get("conversation") or {}
        conv_id = conversation.get("id", "")
        graph_chat_id = ((activity.get("channelData") or {}).get("teamsChatId") or "")
        from_info = activity.get("from") or {}
        user_aad_id = from_info.get("aadObjectId", "")

        if service_url and conv_id and user_aad_id:
            ref = {
                "service_url": service_url,
                "conversation_id": conv_id,
                "bot_id": (activity.get("recipient") or {}).get("id", ""),
            }
            self._bf_conversations[user_aad_id] = ref
            self._bf_conversations_by_chat[conv_id] = ref
            if graph_chat_id:
                self._bf_conversations_by_chat[graph_chat_id] = ref
            logger.info(
                "Stored BF conversation ref for user %s…: conv=%s… svc=%s",
                user_aad_id[:12],
                conv_id[:30],
                service_url,
            )

        # Process direct BF inbound text messages as first-class input.
        if activity_type == "message":
            text = (activity.get("text") or "").strip()
            if text and user_aad_id:
                chat_id = graph_chat_id or conv_id
                sender_name = (from_info.get("name") or "Unknown").strip()

                # Cache sender for send() lookups.
                if chat_id:
                    self._chat_users[chat_id] = user_aad_id

                logger.info(
                    "Inbound BF message chat=%s… sender=%s (%s…): %r",
                    (chat_id or conv_id)[:30],
                    sender_name,
                    user_aad_id[:12],
                    text[:120],
                )

                source = self.build_source(
                    chat_id=chat_id or conv_id,
                    user_id=user_aad_id,
                    user_name=sender_name,
                    chat_type="dm",
                )

                event = MessageEvent(
                    text=text,
                    message_type=MessageType.TEXT,
                    source=source,
                    raw_message=activity,
                    message_id=activity.get("id") or "",
                )
                await self.handle_message(event)

        return web.Response(status=200, text="OK")

    async def _handle_notification(self, request: web.Request) -> web.Response:
        """POST /api/notifications — Graph change-notification endpoint."""
        # Validation handshake
        validation_token = request.rel_url.query.get("validationToken")
        if validation_token:
            logger.info("Responding to subscription validation challenge")
            return web.Response(
                status=200,
                content_type="text/plain",
                text=validation_token,
            )

        # Parse notification payload
        try:
            body: dict = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        # Process each notification asynchronously (Graph expects fast 202 response)
        notifications: list[dict] = body.get("value", [])
        asyncio.ensure_future(self._process_notifications(notifications))

        return web.Response(status=202)

    async def _process_notifications(self, notifications: list[dict]) -> None:
        """Process a batch of Graph change notifications."""
        logger.info("Processing %d notification(s)", len(notifications))
        for notification in notifications:
            # Validate clientState to prevent spoofing
            received_state = notification.get("clientState")
            if received_state != self._client_state:
                logger.warning("clientState mismatch — dropping notification")
                continue

            resource_data: dict = notification.get("resourceData", {})
            message_id: str = resource_data.get("id", "")

            # Extract chat_id from resourceData — may be direct or in @odata.id
            chat_id: str = resource_data.get("chatId", "")
            if not chat_id:
                # Parse from @odata.id: chats('19:xxx@unq.gbl.spaces')/messages('...')
                odata_id: str = resource_data.get("@odata.id", "")
                chat_match = re.search(r"chats\('([^']+)'\)", odata_id)
                if chat_match:
                    chat_id = chat_match.group(1)

            if not chat_id or not message_id:
                logger.warning(
                    "Notification missing chatId or messageId: %s",
                    resource_data,
                )
                continue

            try:
                await self._ingest_message(chat_id, message_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Error processing message chat=%s msg=%s: %s",
                    chat_id,
                    message_id,
                    exc,
                    exc_info=True,
                )

    async def _ingest_message(self, chat_id: str, message_id: str) -> None:
        """Fetch a single message and dispatch it to Hermes."""
        # Security filter: only process messages from chats where Dan is a member
        if not await self._is_bot_chat(chat_id):
            return

        msg: dict = await self._graph_request(
            "GET", f"/chats/{chat_id}/messages/{message_id}"
        )

        # Extract sender
        from_info: dict = msg.get("from") or {}
        user_info: dict = from_info.get("user") or {}
        sender_id: str = user_info.get("id", "")
        sender_name: str = user_info.get("displayName", "Unknown")

        # Skip messages from the bot itself
        if self._bot_user_id and sender_id == self._bot_user_id:
            logger.debug("Skipping self-message from bot user")
            return

        # Skip system/bot messages with no sender
        if not sender_id:
            logger.debug("Skipping message with no sender id")
            return

        # Extract body text (Graph returns HTML content)
        body_info: dict = msg.get("body") or {}
        content_type: str = body_info.get("contentType", "text")
        raw_content: str = body_info.get("content", "")

        if content_type == "html":
            content = _strip_html(raw_content)
        else:
            content = raw_content.strip()

        if not content:
            logger.debug("Skipping empty message")
            return

        logger.info(
            "Inbound message chat=%s… sender=%s (%s…): %r",
            chat_id[:20],
            sender_name,
            sender_id[:12],
            content[:120],
        )

        # Cache sender AAD ID so we can map chat_id -> user for BF replies
        if sender_id:
            self._chat_users[chat_id] = sender_id

        source = self.build_source(
            chat_id=chat_id,
            user_id=sender_id,
            user_name=sender_name,
            chat_type="dm",
        )

        event = MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=msg,
            message_id=message_id,
        )

        await self.handle_message(event)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """GET /health — liveness probe."""
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(
                {
                    "status": "ok",
                    "platform": "teams",
                    "subscription_id": self._subscription_id,
                }
            ),
        )

    async def _start_webhook_server(self) -> None:
        """Start the aiohttp webhook server."""
        app = web.Application()
        app.router.add_post("/api/notifications", self._handle_notification)
        app.router.add_post("/api/messages", self._handle_bf_activity)
        app.router.add_get("/health", self._handle_health)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, "0.0.0.0", self._webhook_port)
        await site.start()
        logger.info("Webhook server listening on port %d", self._webhook_port)

    # ------------------------------------------------------------------
    # BasePlatformAdapter interface
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Authenticate, start the webhook server, and create a Graph subscription."""
        logger.info("connect() called — starting Teams adapter")
        logger.info(
            "  client_id    = %s…",
            self._client_id[:8] if self._client_id else "(empty)",
        )
        logger.info(
            "  tenant_id    = %s…",
            self._tenant_id[:8] if self._tenant_id else "(empty)",
        )
        logger.info("  secret       = (%d chars)", len(self._client_secret))
        logger.info(
            "  notif_host   = %s", self._notification_host or "(empty)"
        )
        logger.info("  webhook_port = %d", self._webhook_port)
        if self._subscription_resource:
            logger.info(
                "  subscription = %s", self._subscription_resource
            )
            if "getAllMessages" in self._subscription_resource:
                logger.warning(
                    "Graph subscription uses getAllMessages (requires "
                    "Chat.Read.All admin consent)."
                )
        else:
            logger.info(
                "  subscription = (none — BF-only mode, no admin Graph permissions)"
            )

        # Wire up Loki logging if available and configured
        if _LOKI_AVAILABLE and os.environ.get("LOKI_URL"):
            try:
                loki_logger = setup_loki_logging("teams-adapter")
                for h in loki_logger.handlers:
                    logger.addHandler(h)
                logger.info("Loki logging attached")
            except Exception as exc:
                logger.warning("Loki setup failed (non-fatal): %s", exc)

        missing = check_teams_requirements()
        if missing:
            for msg in missing:
                logger.error("Requirement not met: %s", msg)
            return False

        try:
            self._session = aiohttp.ClientSession()

            # Always acquire a token — needed for BF send and any Graph calls
            await self._get_token()
            logger.info("Graph token acquired")

            await self._start_webhook_server()

            # Only create a Graph change-notification subscription if explicitly
            # configured.  Default (BF-only) mode receives messages via
            # /api/messages and needs no admin Graph permissions.
            if self._subscription_resource:
                await self._create_subscription()
                self._renew_task = asyncio.ensure_future(
                    self._subscription_renew_loop()
                )
                logger.info(
                    "Adapter connected — listening via Graph subscription + BF"
                )
            else:
                logger.info(
                    "Adapter connected — BF-only mode (no Graph subscription)"
                )

            self._mark_connected()
            return True

        except Exception as exc:  # noqa: BLE001
            logger.error("connect() failed: %s", exc, exc_info=True)
            await self._cleanup()
            return False

    async def disconnect(self) -> None:
        """Stop listeners and cancel the renewal background task."""
        logger.info("Disconnecting…")

        if self._renew_task and not self._renew_task.done():
            self._renew_task.cancel()
            try:
                await self._renew_task
            except asyncio.CancelledError:
                pass
            self._renew_task = None

        # Delete the subscription from Graph so we don't receive stale notifications
        if self._subscription_id:
            try:
                await self._graph_request(
                    "DELETE",
                    f"/subscriptions/{self._subscription_id}",
                    expect_json=False,
                )
                logger.info("Subscription %s deleted", self._subscription_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not delete subscription: %s", exc)
            self._subscription_id = ""

        await self._cleanup()

    async def _cleanup(self) -> None:
        """Tear down the HTTP runner and aiohttp session."""
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None

        if self._session:
            await self._session.close()
            self._session = None

    async def _bf_send_activity(
        self,
        service_url: str,
        conversation_id: str,
        activity: dict,
    ) -> dict:
        """POST an activity to a Bot Framework conversation."""
        token = await self._get_bf_token()
        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        assert self._session is not None
        async with self._session.request(
            "POST", url, headers=headers, json=activity
        ) as resp:
            body_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(
                    f"BF POST {url} returned {resp.status}: {body_text[:500]}"
                )
            return json.loads(body_text) if body_text.strip() else {}

    async def _send_via_chat_id_fallback(
        self,
        chat_id: str,
        content: str,
    ) -> "SendResult":
        """Try direct BF send using known Teams chat_id as conversation_id."""
        service_urls = [
            x.strip().rstrip("/")
            for x in os.environ.get(
                "TEAMS_BF_FALLBACK_SERVICE_URLS",
                "https://smba.trafficmanager.net/amer,"
                "https://smba.trafficmanager.net/emea,"
                "https://smba.trafficmanager.net/apac",
            ).split(",")
            if x.strip()
        ]
        activity = {"type": "message", "text": content}
        last_error = ""
        for service_url in service_urls:
            try:
                logger.info(
                    "Fallback send attempt chat=%s… via %s",
                    chat_id[:20],
                    service_url,
                )
                data = await self._bf_send_activity(
                    service_url, chat_id, activity
                )
                message_id = data.get("id", "")
                logger.info(
                    "Fallback send succeeded via %s id=%s",
                    service_url,
                    message_id,
                )
                return SendResult(success=True, message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning(
                    "Fallback send failed via %s: %s", service_url, exc
                )
        logger.error(
            "Fallback BF send failed for chat=%s…: %s",
            chat_id[:20],
            last_error or "all service URLs failed",
        )
        return SendResult(success=False, message_id="")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict | None = None,
    ) -> "SendResult":
        """Send a text message to a Teams chat via Bot Framework.

        Uses conversation references captured from incoming BF activities
        at ``/api/messages``.  The user must have messaged the bot at least
        once so that a conversation reference exists.
        """
        try:
            # Fast path: direct chat-id keyed conversation reference
            ref = self._bf_conversations_by_chat.get(chat_id)
            if ref:
                activity = {"type": "message", "text": content}
                data = await self._bf_send_activity(
                    ref["service_url"],
                    ref["conversation_id"],
                    activity,
                )
                message_id: str = data.get("id", "")
                logger.info("Message sent via BF (chat ref) id=%s", message_id)
                return SendResult(success=True, message_id=message_id)

            # Find the user's AAD ID for this chat
            user_aad_id = self._chat_users.get(chat_id)
            if not user_aad_id:
                logger.warning(
                    "No user AAD ID cached for chat=%s… — trying fallback send",
                    chat_id[:20],
                )
                return await self._send_via_chat_id_fallback(chat_id, content)

            # Look up the BF conversation reference
            ref = self._bf_conversations.get(user_aad_id)
            if not ref:
                logger.warning(
                    "No BF conversation reference for user %s… — "
                    "has the bot registration messaging endpoint been set? "
                    "The user needs to message the bot so BF sends an "
                    "activity to /api/messages first. Trying fallback send.",
                    user_aad_id[:12],
                )
                return await self._send_via_chat_id_fallback(chat_id, content)

            activity = {
                "type": "message",
                "text": content,
            }

            logger.info(
                "Sending via BF to conv=%s… svc=%s (%d chars)",
                ref["conversation_id"][:30],
                ref["service_url"],
                len(content),
            )
            data = await self._bf_send_activity(
                ref["service_url"],
                ref["conversation_id"],
                activity,
            )
            message_id: str = data.get("id", "")
            logger.info("Message sent via BF id=%s", message_id)
            return SendResult(success=True, message_id=message_id)

        except Exception as exc:
            logger.error(
                "send() failed for chat=%s…: %s", chat_id[:20], exc,
                exc_info=True,
            )
            return SendResult(success=False, message_id="")

    async def send_typing(
        self,
        chat_id: str,
        metadata: dict | None = None,
    ) -> None:
        """No-op: Graph API does not support typing indicators for app-only auth."""
        logger.debug("send_typing() is a no-op for app-only auth")

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: str | None = None,
        reply_to: str | None = None,
        metadata: dict | None = None,
    ) -> "SendResult":
        """Send an image as a text message with URL via Bot Framework."""
        text = caption or ""
        if image_url:
            text = f"{caption or ''}\n{image_url}".strip()
        return await self.send(
            chat_id, text, reply_to=reply_to, metadata=metadata
        )

    async def get_chat_info(self, chat_id: str) -> dict:
        """Fetch basic info about a Teams chat."""
        data = await self._graph_request("GET", f"/chats/{chat_id}")
        chat_type: str = data.get("chatType", "unknown")
        # Use topic as display name; fall back to chat_id for one-on-one chats
        name: str = data.get("topic") or chat_id
        return {
            "name": name,
            "type": chat_type,
            "chat_id": chat_id,
        }
