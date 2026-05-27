"""Teams Webhook Auth Hardening & Replay Protection for STORY-010.

This module provides JWT-based webhook authentication for the Teams bot's
inbound HTTP endpoint. All cryptographic operations are delegated to an
injected token verifier callback; the module handles claims validation,
replay detection, and audit logging.

Key components:
- AuthConfig: configuration for webhook authentication parameters
- TokenClaims: decoded and validated claims from a JWT token
- AuthResult: outcome of webhook authentication with audit serialization
- ReplayCache: time-windowed replay detection cache with bounded memory
- authenticate_webhook(): main entry point for authenticating inbound webhooks
- build_default_auth_config(): factory for sensible Bot Framework defaults
- build_token_claims(): utility for constructing TokenClaims from raw dicts
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

TokenVerifier = Callable[[str], "TokenClaims | None"]
"""Callback that decodes and verifies a raw JWT token.

Input: raw JWT string (from Authorization: Bearer <token> header).
Output: TokenClaims if signature is valid, None if verification fails.
May raise exceptions for malformed tokens.
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthConfig:
    """Configuration for webhook authentication.

    Attributes:
        bot_app_id: Expected 'aud' claim value (the bot's Entra ID app ID).
        allowed_issuers: Tuple of expected 'iss' claim values.
        clock_skew_seconds: Tolerance for expiration check (default: 300s / 5 min).
        replay_window_seconds: Time window for replay detection (default: 300s / 5 min).
        max_replay_cache_size: Maximum entries in the replay cache (default: 10,000).
    """

    bot_app_id: str
    allowed_issuers: tuple[str, ...]
    clock_skew_seconds: int = 300
    replay_window_seconds: int = 300
    max_replay_cache_size: int = 10_000


@dataclass(frozen=True)
class TokenClaims:
    """Decoded and validated claims from a JWT token.

    Attributes:
        issuer: 'iss' claim.
        audience: 'aud' claim.
        expiration: 'exp' claim (unix timestamp).
        issued_at: 'iat' claim (unix timestamp).
        token_id: 'jti' claim or SHA-256 hash of the token.
        service_url: 'serviceurl' claim (Bot Framework specific).
        raw_claims: Full decoded claims dict for downstream use.
    """

    issuer: str
    audience: str
    expiration: float
    issued_at: float
    token_id: str
    service_url: str | None
    raw_claims: dict[str, Any]


@dataclass(frozen=True)
class AuthResult:
    """Outcome of webhook authentication.

    Attributes:
        is_authenticated: True if all checks passed.
        failure_reason: None on success; descriptive string on failure.
        claims: Populated only on success.
        token_id: Always populated (jti or hash) for audit trail.
        checked_at: ISO 8601 timestamp of the check.
    """

    is_authenticated: bool
    failure_reason: str | None
    claims: TokenClaims | None
    token_id: str
    checked_at: str

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for structured logging.

        All values are JSON-serializable scalars or None.
        """
        return {
            "is_authenticated": self.is_authenticated,
            "failure_reason": self.failure_reason,
            "token_id": self.token_id,
            "checked_at": self.checked_at,
            "issuer": self.claims.issuer if self.claims else None,
            "audience": self.claims.audience if self.claims else None,
        }


# ---------------------------------------------------------------------------
# Replay cache
# ---------------------------------------------------------------------------


class ReplayCache:
    """Time-windowed replay detection cache with bounded memory.

    Tracks recently-seen token identifiers and rejects duplicates
    within the configured time window.
    """

    def __init__(
        self,
        window_seconds: int = 300,
        max_size: int = 10_000,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._window_seconds = window_seconds
        self._max_size = max_size
        self._clock = clock or _default_clock
        self._entries: dict[str, float] = {}  # token_id -> seen_at

    def check_and_record(self, token_id: str) -> bool:
        """Check if token_id was seen recently. Record it.

        Returns True if this is a replay (duplicate within window).
        Returns False if this is a new token or the previous entry expired.
        """
        now = self._clock()

        # Check for existing entry within window
        if token_id in self._entries:
            seen_at = self._entries[token_id]
            if now - seen_at < self._window_seconds:
                return True  # Replay detected
            # Entry expired — fall through to re-record

        # Record the token
        self._entries[token_id] = now

        # Evict if over max size
        if len(self._entries) > self._max_size:
            self._evict_expired(now)
            # If still over max after expiry eviction, remove oldest 10%
            if len(self._entries) > self._max_size:
                self._evict_oldest()

        return False

    def size(self) -> int:
        """Current number of entries in the cache."""
        return len(self._entries)

    def _evict_expired(self, now: float | None = None) -> None:
        """Remove entries older than window_seconds."""
        if now is None:
            now = self._clock()
        cutoff = now - self._window_seconds
        expired_keys = [k for k, v in self._entries.items() if v < cutoff]
        for key in expired_keys:
            del self._entries[key]

    def _evict_oldest(self) -> None:
        """Remove the oldest 10% of entries by timestamp."""
        if not self._entries:
            return
        count_to_remove = max(1, len(self._entries) // 10)
        sorted_keys = sorted(self._entries, key=lambda k: self._entries[k])
        for key in sorted_keys[:count_to_remove]:
            del self._entries[key]


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


_DEFAULT_ISSUERS: tuple[str, ...] = (
    "https://api.botframework.com",
    "https://login.microsoftonline.com/botframework.com/v2.0",
)


def build_default_auth_config(bot_app_id: str) -> AuthConfig:
    """Build an AuthConfig with sensible defaults for Bot Framework.

    Args:
        bot_app_id: The bot's Entra ID application (client) ID.

    Returns:
        AuthConfig with standard Bot Framework issuers and 5-minute tolerances.
    """
    return AuthConfig(
        bot_app_id=bot_app_id,
        allowed_issuers=_DEFAULT_ISSUERS,
        clock_skew_seconds=300,
        replay_window_seconds=300,
        max_replay_cache_size=10_000,
    )


def build_token_claims(
    raw_claims: dict[str, Any],
    token_string: str = "",
) -> TokenClaims:
    """Build TokenClaims from a raw decoded JWT claims dict.

    Extracts standard claims (iss, aud, exp, iat, jti, serviceurl).
    If jti is absent, computes SHA-256 of token_string as the token_id.

    Args:
        raw_claims: Decoded JWT claims dictionary.
        token_string: The original JWT string (used for hash if jti is absent).

    Returns:
        TokenClaims with extracted fields.
    """
    token_id = raw_claims.get("jti", "")
    if not token_id and token_string:
        token_id = hashlib.sha256(token_string.encode()).hexdigest()
    if not token_id:
        token_id = "unknown"

    return TokenClaims(
        issuer=str(raw_claims.get("iss", "")),
        audience=str(raw_claims.get("aud", "")),
        expiration=float(raw_claims.get("exp", 0)),
        issued_at=float(raw_claims.get("iat", 0)),
        token_id=token_id,
        service_url=raw_claims.get("serviceurl"),
        raw_claims=dict(raw_claims),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def authenticate_webhook(
    token: str,
    config: AuthConfig,
    token_verifier: TokenVerifier,
    *,
    clock: Callable[[], float] | None = None,
    replay_cache: ReplayCache | None = None,
) -> AuthResult:
    """Authenticate an inbound webhook request.

    Validates the JWT bearer token through a sequence of checks:
    1. Empty token check
    2. Signature verification (via injected verifier)
    3. Issuer validation
    4. Audience validation
    5. Expiration validation (with clock skew tolerance)
    6. Replay detection (if replay_cache provided)

    Args:
        token: Raw JWT string from the Authorization header.
        config: Authentication configuration.
        token_verifier: Callback that decodes and verifies the token signature.
        clock: Injectable clock returning unix timestamp (default: time.time).
        replay_cache: Optional replay detection cache.

    Returns:
        AuthResult indicating authentication success or failure with audit data.
    """
    clock_fn = clock or _default_clock
    now = clock_fn()
    checked_at = _timestamp_to_iso(now)

    # Step 1: Empty token check
    if not token or not token.strip():
        return AuthResult(
            is_authenticated=False,
            failure_reason="missing or empty token",
            claims=None,
            token_id="unknown",
            checked_at=checked_at,
        )

    # Step 2: Signature verification
    claims: TokenClaims | None = None
    try:
        claims = token_verifier(token)
    except Exception as exc:
        return AuthResult(
            is_authenticated=False,
            failure_reason=f"token verification error: {exc}",
            claims=None,
            token_id="unknown",
            checked_at=checked_at,
        )

    if claims is None:
        return AuthResult(
            is_authenticated=False,
            failure_reason="token signature verification failed",
            claims=None,
            token_id="unknown",
            checked_at=checked_at,
        )

    token_id = claims.token_id
    if not token_id:
        token_id = hashlib.sha256(token.encode()).hexdigest()

    # Step 3: Issuer validation
    if claims.issuer not in config.allowed_issuers:
        return AuthResult(
            is_authenticated=False,
            failure_reason=f"invalid issuer: {claims.issuer}",
            claims=None,
            token_id=token_id,
            checked_at=checked_at,
        )

    # Step 4: Audience validation
    if claims.audience != config.bot_app_id:
        return AuthResult(
            is_authenticated=False,
            failure_reason=f"invalid audience: {claims.audience}",
            claims=None,
            token_id=token_id,
            checked_at=checked_at,
        )

    # Step 5: Expiration validation (with clock skew tolerance)
    if now > claims.expiration + config.clock_skew_seconds:
        return AuthResult(
            is_authenticated=False,
            failure_reason="token expired",
            claims=None,
            token_id=token_id,
            checked_at=checked_at,
        )

    # Step 6: Replay detection
    if replay_cache is not None:
        if replay_cache.check_and_record(token_id):
            return AuthResult(
                is_authenticated=False,
                failure_reason=f"replay detected for token {token_id}",
                claims=None,
                token_id=token_id,
                checked_at=checked_at,
            )

    # All checks passed
    return AuthResult(
        is_authenticated=True,
        failure_reason=None,
        claims=claims,
        token_id=token_id,
        checked_at=checked_at,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_clock() -> float:
    """Default clock using time.time() for unix timestamps."""
    import time

    return time.time()


def _timestamp_to_iso(timestamp: float) -> str:
    """Convert a unix timestamp to an ISO 8601 string."""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AuthConfig",
    "AuthResult",
    "ReplayCache",
    "TokenClaims",
    "TokenVerifier",
    "authenticate_webhook",
    "build_default_auth_config",
    "build_token_claims",
]
