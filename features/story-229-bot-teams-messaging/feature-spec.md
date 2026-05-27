# STORY-229: Feature Spec — Bot-to-Bot Teams Messaging via App Token

**Phase:** 6 (Design)
**Date:** 2026-04-15
**Scope:** Medium

## Overview

Replace M365 CLI delegated-user-token authentication in `deployment/vm/teams_m365_deployed.py` with MSAL client-credentials (application permissions). All Graph API calls switch from `/me/` to `/users/{bot_user_id}/`.

## Detailed Design

### 1. Token Management (replaces M365 CLI)

```python
# New imports (top of file)
import msal  # replaces subprocess calls to m365 CLI

# New constants
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

# Remove: M365_CMD, _get_m365_token()

class TeamsAdapter(BasePlatformAdapter):
    def __init__(self, config):
        # Remove: m365 status subprocess call, self._connected_as
        # Add:
        self._client_id = os.environ.get("TEAMS_CLIENT_ID", "")
        self._client_secret = os.environ.get("TEAMS_CLIENT_SECRET", "")
        self._tenant_id = os.environ.get("TEAMS_TENANT_ID", "")
        self._msal_app: Any = None

    def _build_msal_app(self) -> None:
        """Initialise MSAL confidential client (idempotent)."""
        if self._msal_app is not None:
            return
        authority = f"https://login.microsoftonline.com/{self._tenant_id}"
        self._msal_app = msal.ConfidentialClientApplication(
            self._client_id,
            authority=authority,
            client_credential=self._client_secret,
        )

    async def _get_token(self) -> str:
        """Return valid Graph API token, refreshing via MSAL when near expiry."""
        if self._token and time.monotonic() < self._token_expires - 60:
            return self._token
        self._build_msal_app()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._msal_app.acquire_token_for_client(scopes=GRAPH_SCOPES),
        )
        if "access_token" not in result:
            error = result.get("error_description") or result.get("error") or str(result)
            raise RuntimeError(f"MSAL token acquisition failed: {error}")
        self._token = result["access_token"]
        self._token_expires = time.monotonic() + result.get("expires_in", 3600)
        return self._token
```

### 2. Graph API Endpoint Migration

All `/me/` references become `/users/{self._bot_user_id}/`:

```python
async def _get_headers(self) -> dict:
    """Return auth headers, refreshing token via MSAL if needed."""
    if not self._token or time.monotonic() > self._token_expires - 60:
        await self._get_token()
    return {
        "Authorization": f"Bearer {self._token}",
        "Content-Type": "application/json",
    }

async def _get_chats(self) -> list[dict]:
    data = await self._graph_get(f"/users/{self._bot_user_id}/chats?$top=50")
    return data.get("value", [])

async def _get_messages(self, chat_id: str) -> list[dict]:
    data = await self._graph_get(
        f"/users/{self._bot_user_id}/chats/{chat_id}/messages"
        f"?$top=5&$orderby=createdDateTime desc"
    )
    return data.get("value", [])

async def _set_presence(self, availability: str, activity: str) -> None:
    await self._graph_post(f"/users/{self._bot_user_id}/presence/setPresence", {
        "sessionId": self._client_id,
        "availability": availability,
        "activity": activity,
        "expirationDuration": "PT4H",
    })

async def send(self, chat_id, content, reply_to=None, metadata=None):
    formatted = self._markdown_to_teams_html(content)
    data = await self._graph_post(
        f"/users/{self._bot_user_id}/chats/{chat_id}/messages",
        {"body": {"contentType": "html", "content": formatted}},
    )
```

### 3. Requirement Check Update

```python
def check_teams_requirements() -> list[str]:
    missing = []
    if not _HERMES_BASE_AVAILABLE:
        missing.append("hermes gateway.platforms.base not importable")
    try:
        import msal  # noqa: F401
    except ImportError:
        missing.append("msal package not installed (pip install msal)")
    required_env = ["TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_TENANT_ID", "TEAMS_BOT_USER_ID"]
    for var in required_env:
        if not os.environ.get(var):
            missing.append(f"Environment variable {var} is not set")
    return missing
```

### 4. Token Refresh Loop Simplification

The 30-minute background refresh loop is **removed**. MSAL handles caching internally. The `_get_token()` method checks expiry on every call and only hits Azure AD when the cached token is within 60s of expiry.

The `_token_refresh_loop` and `_token_refresh_task` are eliminated. The `connect()` method no longer starts a refresh task.

### 5. `__init__` Changes

**Remove:**
- `M365_CMD` constant
- `_get_m365_token()` function
- `_connected_as` field and the `m365 status` subprocess in `__init__`

**Add:**
- `_client_id`, `_client_secret`, `_tenant_id` from env vars
- `_msal_app: Any = None`
- `_MSAL_AVAILABLE` module-level flag (same pattern as hermes/teams.py)

**Rename:**
- `_refresh_token()` → removed (replaced by `_get_token()` with built-in expiry check)
- `_token_expires` semantics unchanged (monotonic timestamp)

### 6. Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `TEAMS_CLIENT_ID` | Yes | Entra app registration client ID |
| `TEAMS_CLIENT_SECRET` | Yes | App registration client secret |
| `TEAMS_TENANT_ID` | Yes | Entra tenant ID |
| `TEAMS_BOT_USER_ID` | Yes | Bot's Entra object ID (for `/users/{id}/` calls and self-message filtering) |
| `TEAMS_POLL_INTERVAL` | No | Seconds between polls (default: 1) |

**Removed:** No M365 CLI env vars needed.

## Files Changed

| File | Type | Description |
|------|------|-------------|
| `deployment/vm/teams_m365_deployed.py` | Modified | Full auth mechanism swap: M365 CLI → MSAL client credentials |
| `tests/deployment/test_teams_m365_deployed.py` | New | 10 unit tests covering all auth paths |

## Out of Scope

- `deployment/hermes/teams.py` — already uses MSAL
- Shared auth module extraction — deferred per analysis
- Entra admin consent automation — manual one-time step, documented in deployment README
