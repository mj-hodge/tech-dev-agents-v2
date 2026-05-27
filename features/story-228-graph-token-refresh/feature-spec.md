# STORY-228: Feature Spec — MSAL Graph Token Provider

> Phase 6 | Scope: Small | Story: STORY-228

---

## Overview

Implement `GraphTokenProvider` in `tech_dev_agents/ops_console/graph_token_provider.py` to provide automatic Graph API token acquisition and refresh using MSAL's confidential client credentials flow.

## Component Design

### GraphTokenProvider Class

```python
class GraphTokenProvider:
    """Async-callable token provider wrapping MSAL ConfidentialClientApplication."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._msal_app: Optional[msal.ConfidentialClientApplication] = None

    async def __call__(self) -> str:
        """Acquire a token, offloading blocking MSAL call to executor."""
        ...

    def _build_msal_app(self) -> msal.ConfidentialClientApplication:
        """Lazily construct the MSAL app (singleton per provider instance)."""
        ...

    def _acquire_token_sync(self) -> str:
        """Blocking call to MSAL acquire_token_for_client."""
        ...
```

### Factory Function

```python
def create_graph_token_provider() -> Optional[GraphTokenProvider]:
    """Return a GraphTokenProvider if MSAL credentials are configured, else None."""
```

Returns `None` when any of `tenant_id`, `client_id`, or `client_secret` are missing, allowing the TeamsClient to fall back to a static token.

### Key Behaviors

| Behavior | Detail |
|----------|--------|
| Token caching | Handled by MSAL internally — tokens reused until near expiry |
| Async compatibility | Blocking MSAL call offloaded via `loop.run_in_executor(None, ...)` |
| Lazy initialization | MSAL app constructed on first `__call__`, not at import time |
| Error handling | Raises `RuntimeError` if MSAL returns no `access_token` in result |
| Scope | `["https://graph.microsoft.com/.default"]` (application permissions) |

### Integration Point

In `main.py` (FastAPI startup):
1. Call `create_graph_token_provider()`
2. If provider is not `None`, pass it as `token_provider` to `TeamsClient`
3. TeamsClient's existing 401 → retry logic calls the provider on auth failures

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPS_GRAPH_TENANT_ID` | For MSAL | Azure AD tenant ID |
| `OPS_GRAPH_CLIENT_ID` | For MSAL | App registration client ID |
| `OPS_GRAPH_CLIENT_SECRET` | For MSAL | App registration client secret |
| `OPS_GRAPH_API_TOKEN` | Fallback | Static token (used if MSAL not configured) |

## Dependencies

- `msal>=1.24.0` added to `requirements.txt`
