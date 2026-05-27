"""MSAL-based Graph API token provider — STORY-228.

Wraps ``msal.ConfidentialClientApplication`` as an async callable compatible
with the v2 TeamsClient's ``token_provider`` parameter.  Tokens are acquired
via the OAuth2 client-credentials flow and cached by MSAL's built-in token
cache (no manual expiry tracking needed).

The blocking MSAL call is offloaded to ``asyncio.get_event_loop().run_in_executor``
so it never blocks the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Graph API scopes for client-credentials flow (always /.default)
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphTokenProvider:
    """Async-callable MSAL token provider for Microsoft Graph API.

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID.
    client_id:
        App registration client ID.
    client_secret:
        App registration client secret.

    Usage::

        provider = GraphTokenProvider(tenant_id, client_id, client_secret)
        token = await provider()  # returns access_token string

        # Or pass directly to TeamsClient:
        client = TeamsClient(..., token_provider=provider)
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._msal_app: Any | None = None

    def _build_msal_app(self) -> Any:
        """Lazily initialise the MSAL confidential client."""
        if self._msal_app is None:
            import msal  # deferred import — only needed at runtime

            authority = f"https://login.microsoftonline.com/{self._tenant_id}"
            self._msal_app = msal.ConfidentialClientApplication(
                self._client_id,
                authority=authority,
                client_credential=self._client_secret,
            )
        return self._msal_app

    def _acquire_token_sync(self) -> str:
        """Acquire token synchronously (called inside executor)."""
        app = self._build_msal_app()
        result: dict = app.acquire_token_for_client(scopes=GRAPH_SCOPES)

        if "access_token" not in result:
            error = result.get("error_description") or result.get("error", "unknown")
            raise RuntimeError(f"MSAL token acquisition failed: {error}")

        expires_in: int = result.get("expires_in", 3600)
        logger.info(
            "Graph access token acquired (expires in %ds)", expires_in,
        )
        return result["access_token"]

    async def __call__(self) -> str:
        """Acquire a Graph API access token asynchronously.

        MSAL's internal cache handles expiry — it returns a cached token when
        one is still valid, and only contacts Azure AD when refresh is needed.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._acquire_token_sync)


def create_graph_token_provider(
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> GraphTokenProvider | None:
    """Factory that returns a provider only when all credentials are present.

    Returns ``None`` if any credential is empty, allowing the caller to
    fall back to a static token.
    """
    if all([tenant_id, client_id, client_secret]):
        return GraphTokenProvider(tenant_id, client_id, client_secret)
    logger.warning(
        "MSAL credentials incomplete — Graph token auto-refresh disabled. "
        "Set OPS_GRAPH_TENANT_ID, OPS_GRAPH_CLIENT_ID, OPS_GRAPH_CLIENT_SECRET."
    )
    return None
