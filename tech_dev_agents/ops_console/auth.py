"""Authentication dependencies for FastAPI — Entra ID SSO + role-scoped API keys.

STORY-514: API keys carry a role (agent / manager / admin). The role is resolved
by which env-var-stored key matched. ``require_auth`` returns the role alongside
the credential; endpoints that need a higher role use ``require_role(min_role)``.

Defense goal: when an agent's terminal guard is bypassed by proxying through
another VM (2026-04-21 incident), the API still rejects the destructive call
because the agent-scoped key is bound to a role that cannot DELETE, even if
the request originates from a manager's machine.
"""

from __future__ import annotations

import logging
import time
from enum import IntEnum
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from tech_dev_agents.health_api import AuthError, validate_api_key


class Role(IntEnum):
    """API-key roles, ordered so higher values are strictly more permissive."""

    AGENT = 1
    MANAGER = 2
    ADMIN = 3


def _resolve_role(provided: str, settings) -> Role | None:
    """Map a presented API key to its role, or None if no key matches.

    Order of checks is specificity-first: admin → manager → agent → legacy.
    The legacy key (OPS_OPS_CONSOLE_API_KEY) is treated as MANAGER so Morris's
    existing wiring keeps working during the rollout window; remove after all
    callers are on role-specific keys.
    """
    admin = getattr(settings, "admin_role_api_key", "") or ""
    manager = getattr(settings, "manager_role_api_key", "") or ""
    agent = getattr(settings, "agent_role_api_key", "") or ""
    legacy = getattr(settings, "ops_console_api_key", "") or ""

    if admin and _secure_eq(provided, admin):
        return Role.ADMIN
    if manager and _secure_eq(provided, manager):
        return Role.MANAGER
    if agent and _secure_eq(provided, agent):
        return Role.AGENT
    if legacy and _secure_eq(provided, legacy):
        logger_ = logging.getLogger(__name__)
        logger_.info(
            "legacy_key_used=true — migrate caller to role-specific key (STORY-514)"
        )
        return Role.MANAGER
    return None


def _secure_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid leaking key length/prefix."""
    try:
        import hmac

        return hmac.compare_digest(a, b)
    except Exception:
        return a == b

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# In-memory JWKS cache (refreshed on cache miss / key rotation)
_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0

# Re-fetch JWKS if cache is older than 24 hours
_JWKS_CACHE_TTL_SECONDS: float = 86400.0


async def _fetch_jwks(tenant_id: str) -> dict[str, Any]:
    """Fetch Microsoft's JWKS for the given tenant."""
    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def get_jwks(tenant_id: str | None = None) -> dict[str, Any] | None:
    """Return cached JWKS. Used by tests to mock."""
    return _jwks_cache


def _is_jwks_expired() -> bool:
    """Return True if the JWKS cache has exceeded its TTL."""
    return (time.monotonic() - _jwks_fetched_at) > _JWKS_CACHE_TTL_SECONDS


async def _get_or_fetch_jwks(tenant_id: str) -> dict[str, Any]:
    """Return cached JWKS or fetch from Microsoft.

    Re-fetches when the cache is empty or older than _JWKS_CACHE_TTL_SECONDS.
    """
    global _jwks_cache, _jwks_fetched_at
    if _jwks_cache is not None and not _is_jwks_expired():
        return _jwks_cache
    _jwks_cache = await _fetch_jwks(tenant_id)
    _jwks_fetched_at = time.monotonic()
    return _jwks_cache


async def _refresh_jwks_for_unknown_kid(tenant_id: str) -> dict[str, Any]:
    """Force re-fetch JWKS when a token has an unknown kid (key rotation)."""
    global _jwks_cache, _jwks_fetched_at
    _jwks_cache = await _fetch_jwks(tenant_id)
    _jwks_fetched_at = time.monotonic()
    return _jwks_cache


def _find_key_by_kid(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    """Find the signing key matching the given kid."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


def _validate_jwt(
    token: str,
    *,
    jwks: dict[str, Any],
    client_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Validate a JWT against the provided JWKS and return decoded claims.

    Raises jwt.exceptions.PyJWTError on any validation failure.
    """
    # Decode header to get kid
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if not kid:
        raise jwt.exceptions.InvalidTokenError("Missing kid in token header")

    key_data = _find_key_by_kid(jwks, kid)
    if key_data is None:
        raise jwt.exceptions.InvalidTokenError(f"Key {kid} not found in JWKS")

    # Build public key from JWK
    from jwt.algorithms import RSAAlgorithm

    public_key = RSAAlgorithm.from_jwk(key_data)

    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"

    decoded = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=issuer,
        options={"verify_exp": True, "verify_aud": True, "verify_iss": True},
    )
    return decoded


async def require_auth(
    request: Request,
    api_key: str | None = Security(api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> str | dict[str, Any]:
    """FastAPI dependency: Entra ID bearer token (preferred) or API key fallback.

    1. If Authorization: Bearer <token> is present, validate JWT against Entra ID JWKS.
    2. Else if X-API-Key header is present, validate as before (MCP tool compat).
    3. Else raise 401.

    Returns decoded JWT claims dict (bearer) or the API key string (fallback).
    """
    settings = request.app.state.settings
    tenant_id = getattr(settings, "entra_tenant_id", "")
    client_id = getattr(settings, "entra_client_id", "")

    # --- Bearer token path ---
    if bearer is not None and bearer.credentials:
        token = bearer.credentials

        if not tenant_id or not client_id:
            raise HTTPException(
                status_code=500,
                detail="Entra ID not configured on server",
            )

        try:
            # Try cached JWKS first, or fall back to patched get_jwks for tests
            jwks = get_jwks(tenant_id)
            if jwks is None:
                jwks = await _get_or_fetch_jwks(tenant_id)

            try:
                claims = _validate_jwt(
                    token,
                    jwks=jwks,
                    client_id=client_id,
                    tenant_id=tenant_id,
                )
            except jwt.exceptions.InvalidTokenError as exc:
                # If kid not found, try refreshing JWKS (key rotation)
                if "not found in JWKS" in str(exc):
                    jwks = await _refresh_jwks_for_unknown_kid(tenant_id)
                    claims = _validate_jwt(
                        token,
                        jwks=jwks,
                        client_id=client_id,
                        tenant_id=tenant_id,
                    )
                else:
                    raise

            # Group membership check — enforce Technology Agents group membership.
            # Fail-closed: deny access if the required group is absent from the token.
            groups = claims.get("groups", [])
            required_group = "04284f3f-51db-46f4-a5d8-3d1bd17efb7c"
            if required_group not in groups:
                logger.warning(
                    "User %s denied: not in Technology Agents group (groups=%s).",
                    claims.get("preferred_username", "unknown"),
                    groups,
                )
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: not a member of the Technology Agents group",
                )

            # Stash a stable identity on request.state so downstream handlers
            # can record audit fields (approver, approved_by) without re-parsing
            # credentials (CRIT-3, HIGH-1).
            request.state.auth_user = (
                claims.get("preferred_username")
                or claims.get("upn")
                or claims.get("oid")
                or "entra-user"
            )
            request.state.auth_claims = claims
            return claims

        except jwt.exceptions.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.exceptions.InvalidAudienceError:
            raise HTTPException(status_code=401, detail="Invalid audience")
        except jwt.exceptions.InvalidIssuerError:
            raise HTTPException(status_code=401, detail="Invalid issuer")
        except jwt.exceptions.PyJWTError as exc:
            logger.warning("JWT validation failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid token")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Unexpected auth error: %s", exc)
            raise HTTPException(status_code=401, detail="Authentication failed")

    # --- API key fallback path (MCP tools) ---
    if api_key:
        role = _resolve_role(api_key, settings)
        if role is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        # Stash role on request.state for require_role() to read without re-parsing.
        request.state.api_role = role
        request.state.api_key_tail = api_key[-6:]  # for audit logging, not secret exposure
        # Stash a stable, non-secret identity for audit fields. Includes role +
        # key tail so distinct keys are distinguishable in the audit log.
        request.state.auth_user = f"api:{role.name.lower()}:{api_key[-6:]}"
        return api_key

    # --- No credentials ---
    raise HTTPException(status_code=401, detail="Missing authentication credentials")


def require_role(min_role: Role):
    """Return a FastAPI dependency that enforces ``min_role`` on the current request.

    Intended to stack with ``require_auth`` at the router level. Usage::

        @router.delete("/dispatch/queue/{story_id}",
                       dependencies=[Depends(require_role(Role.MANAGER))])
        async def cancel_story(...): ...

    Bearer (Entra ID) callers are currently treated as ADMIN — once we have
    group-to-role mapping from token claims, tighten this. API-key callers get
    whichever role their key resolves to via ``_resolve_role``.
    """

    async def _check(request: Request) -> None:
        # Bearer path: if the caller authenticated with Entra ID, they're admin-equivalent.
        # Tighten with group-claim mapping as a follow-up.
        auth_header = request.headers.get("authorization", "").lower()
        if auth_header.startswith("bearer "):
            return  # admin-equivalent, pass

        role: Role | None = getattr(request.state, "api_role", None)
        if role is None:
            raise HTTPException(status_code=401, detail="Missing authentication credentials")
        if int(role) < int(min_role):
            key_tail = getattr(request.state, "api_key_tail", "??")
            logger.warning(
                "auth_denied: role=%s min_role=%s method=%s path=%s key_tail=%s",
                role.name,
                min_role.name,
                request.method,
                request.url.path,
                key_tail,
            )
            raise HTTPException(
                status_code=403,
                detail=f"insufficient role: {role.name.lower()} < {min_role.name.lower()}",
            )

    return _check


# Keep backward-compatible alias
async def require_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    """Legacy API key dependency — delegates to require_auth for dual-auth support."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    expected = request.app.state.settings.ops_console_api_key
    try:
        validate_api_key(provided=api_key, expected=expected)
    except AuthError:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key
