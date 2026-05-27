# Feature Spec: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 5 — Specification
> Date: 2026-03-31
> Story: STORY-010
> Epic: Autonomous Dev Agent (v1)
> Scope: Medium
> Depends on: STORY-001 (Container Runtime), STORY-002 (Teams Bot), STORY-009 (Graph Permissions)

---

## 1. Module Overview

A single Python module `tech_dev_agents/webhook_auth.py` providing JWT-based webhook authentication for the Teams bot's inbound HTTP endpoint. All cryptographic operations are delegated to an injected callback; the module handles claims validation, replay detection, and audit logging.

---

## 2. Data Model

### 2.1 AuthConfig

```python
@dataclass(frozen=True)
class AuthConfig:
    """Configuration for webhook authentication."""
    bot_app_id: str                          # Expected 'aud' claim
    allowed_issuers: tuple[str, ...]         # Expected 'iss' claim values
    clock_skew_seconds: int = 300            # Tolerance for exp check (5 min)
    replay_window_seconds: int = 300         # Replay detection window (5 min)
    max_replay_cache_size: int = 10_000      # Max entries before forced eviction
```

**Default issuers:** `("https://api.botframework.com", "https://login.microsoftonline.com/botframework.com/v2.0")`

### 2.2 TokenClaims

```python
@dataclass(frozen=True)
class TokenClaims:
    """Decoded and validated claims from a JWT token."""
    issuer: str                   # 'iss' claim
    audience: str                 # 'aud' claim
    expiration: float             # 'exp' claim (unix timestamp)
    issued_at: float              # 'iat' claim (unix timestamp)
    token_id: str                 # 'jti' claim or SHA-256 hash of token
    service_url: str | None       # 'serviceurl' claim (Bot Framework specific)
    raw_claims: dict[str, Any]    # Full decoded claims dict for downstream use
```

### 2.3 AuthResult

```python
@dataclass(frozen=True)
class AuthResult:
    """Outcome of webhook authentication."""
    is_authenticated: bool
    failure_reason: str | None     # None on success; descriptive string on failure
    claims: TokenClaims | None     # Populated only on success
    token_id: str                  # Always populated (for audit trail)
    checked_at: str                # ISO 8601 timestamp of the check

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for structured logging."""
```

**Audit dict fields:** `is_authenticated`, `failure_reason`, `token_id`, `checked_at`, `issuer` (from claims if present), `audience` (from claims if present).

---

## 3. ReplayCache

### 3.1 Interface

```python
class ReplayCache:
    """Time-windowed replay detection cache with bounded memory."""

    def __init__(
        self,
        window_seconds: int = 300,
        max_size: int = 10_000,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None: ...

    def check_and_record(self, token_id: str) -> bool:
        """Check if token_id was seen recently. Record it. Return True if replay."""

    def size(self) -> int:
        """Current number of entries in the cache."""

    def _evict_expired(self) -> None:
        """Remove entries older than window_seconds."""
```

### 3.2 Implementation Details

- Internal storage: `dict[str, float]` mapping `token_id → seen_at_timestamp`.
- On `check_and_record()`:
  1. Get current time from injectable clock.
  2. If `token_id` exists AND `now - seen_at < window_seconds`: return `True` (replay detected).
  3. If `token_id` exists but entry is expired: overwrite with new timestamp, return `False`.
  4. Record `token_id → now`.
  5. If `len(cache) > max_size`: call `_evict_expired()`. If still over max, evict oldest 10% of entries.
  6. Return `False`.
- Eviction strategy: lazy (triggered when cache exceeds max size). No background timer.

---

## 4. Token Verifier Protocol

```python
TokenVerifier = Callable[[str], TokenClaims | None]
```

- Input: raw JWT string (from `Authorization: Bearer <token>` header)
- Output: `TokenClaims` if the token's cryptographic signature is valid; `None` if verification fails
- May raise exceptions for malformed tokens — `authenticate_webhook` catches all exceptions

In production, the verifier would:
1. Decode the JWT header to extract the `kid` (key ID).
2. Fetch the matching public key from Microsoft's cached JWKS.
3. Verify the signature using the public key.
4. Return decoded claims as `TokenClaims`.

In tests, the verifier is a lambda or mock that returns pre-built `TokenClaims`.

---

## 5. Main Entry Point: authenticate_webhook()

```python
def authenticate_webhook(
    token: str,
    config: AuthConfig,
    token_verifier: TokenVerifier,
    *,
    clock: Callable[[], float] | None = None,
    replay_cache: ReplayCache | None = None,
) -> AuthResult:
```

### 5.1 Validation Sequence

1. **Empty token check** — If `token` is empty or whitespace, return failure: `"missing or empty token"`.
2. **Signature verification** — Call `token_verifier(token)`. If returns `None`, return failure: `"token signature verification failed"`. If raises an exception, return failure: `"token verification error: {exception}"`.
3. **Issuer validation** — If `claims.issuer` not in `config.allowed_issuers`, return failure: `"invalid issuer: {claims.issuer}"`.
4. **Audience validation** — If `claims.audience != config.bot_app_id`, return failure: `"invalid audience: {claims.audience}"`.
5. **Expiration validation** — If `now > claims.expiration + config.clock_skew_seconds`, return failure: `"token expired"`. (Note: clock skew is added to expiration, giving the token extra time.)
6. **Replay detection** — If `replay_cache` is provided and `replay_cache.check_and_record(claims.token_id)` returns `True`, return failure: `"replay detected for token {claims.token_id}"`.
7. **Success** — Return `AuthResult(is_authenticated=True, claims=claims, ...)`.

### 5.2 Token ID Extraction

If `claims.token_id` is empty or absent (the jti claim was not in the token), compute `hashlib.sha256(token.encode()).hexdigest()` and use that as the `token_id` in the `AuthResult`. This happens before step 6 (replay detection).

### 5.3 Failure Result Construction

On any failure, the `AuthResult` includes:
- `is_authenticated = False`
- `failure_reason` = the descriptive string
- `claims = None`
- `token_id` = the jti or hash (if claims were decoded) or `"unknown"` (if verification failed before claims extraction)
- `checked_at` = ISO 8601 timestamp

---

## 6. Factory Functions

### 6.1 build_default_auth_config()

```python
def build_default_auth_config(bot_app_id: str) -> AuthConfig:
    """Build an AuthConfig with sensible defaults for Bot Framework."""
```

Returns an `AuthConfig` with:
- `bot_app_id` = provided
- `allowed_issuers` = `("https://api.botframework.com", "https://login.microsoftonline.com/botframework.com/v2.0")`
- `clock_skew_seconds` = 300
- `replay_window_seconds` = 300
- `max_replay_cache_size` = 10_000

### 6.2 build_token_claims()

```python
def build_token_claims(
    raw_claims: dict[str, Any],
    token_string: str = "",
) -> TokenClaims:
    """Build TokenClaims from a raw decoded JWT claims dict."""
```

Extracts standard claims (`iss`, `aud`, `exp`, `iat`, `jti`, `serviceurl`) from the dict. If `jti` is absent, computes SHA-256 of `token_string`. This is a utility for both production code and tests.

---

## 7. Audit Serialization

`AuthResult.to_audit_dict()` returns:

```python
{
    "is_authenticated": bool,
    "failure_reason": str | None,
    "token_id": str,
    "checked_at": str,
    "issuer": str | None,       # From claims if available
    "audience": str | None,     # From claims if available
}
```

All values are JSON-serializable. No frozensets (unlike STORY-009) — all fields are scalar or None.

---

## 8. Integration Contract

### 8.1 HTTP Handler Integration (STORY-002)

The Teams bot HTTP handler extracts the `Authorization` header, strips the `Bearer ` prefix, and calls:

```python
result = authenticate_webhook(
    token=bearer_token,
    config=auth_config,
    token_verifier=production_verifier,
    replay_cache=shared_replay_cache,
)
if not result.is_authenticated:
    log_audit(result.to_audit_dict())
    return HTTP 401
```

The `shared_replay_cache` is instantiated once at process startup and shared across all webhook handler invocations.

### 8.2 Downstream Consumers

| Consumer | What it receives |
|----------|-----------------|
| STORY-002 Bot Handler | `AuthResult.is_authenticated` for pass/reject decision |
| STORY-009 Scope Validation | `AuthResult.claims.raw_claims` for scope extraction (if needed) |
| STORY-011 Secret Hygiene | `AuthResult.to_audit_dict()` for compliance reporting |
| Structured Logging (Loki) | `AuthResult.to_audit_dict()` on every auth decision |

---

## 9. Implementation Plan

| Order | File | Description | Depends on |
|-------|------|-------------|-----------|
| 1 | `tech_dev_agents/webhook_auth.py` | All data classes, ReplayCache, authenticate_webhook(), factory functions | None |
| 2 | `tests/test_webhook_auth.py` | Unit tests for all acceptance criteria | webhook_auth.py |

### Single-file module rationale
The module is small (< 250 lines estimated) and self-contained. All classes and functions are cohesive — splitting into multiple files adds import complexity without benefit.

---

## 10. Error Handling

| Error Condition | Behavior |
|----------------|----------|
| `token_verifier` returns `None` | Auth failure: "token signature verification failed" |
| `token_verifier` raises any exception | Auth failure: "token verification error: {str(exc)}" — exception is caught, not propagated |
| Empty/whitespace token | Auth failure: "missing or empty token" |
| Claims missing `iss`/`aud`/`exp` | Handled by `build_token_claims()` — uses defaults or empty strings; validation will then fail on mismatch |
| Replay cache at max size | Evict expired entries; if still over, evict oldest 10%; cache never blocks or raises |

---

## 11. Security Considerations

- **No secrets in auth results:** `AuthResult` never contains the raw token or key material.
- **Token ID for audit only:** The `token_id` (jti or hash) is safe to log; it cannot be used to reconstruct the token.
- **Replay cache is in-memory:** Acceptable for single-instance bot. Multi-instance deployment would need a shared cache (Redis) — out of scope for v1.
- **Clock skew tolerance:** 5 minutes is generous but standard for distributed systems. Configurable if tighter control needed.
- **No token in error messages:** Failure reasons describe the validation step, not the token content.
