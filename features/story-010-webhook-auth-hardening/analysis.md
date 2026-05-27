# Analysis: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 4 — Analysis
> Date: 2026-03-31
> Scope: Medium
> Depends on: STORY-001, STORY-002, STORY-009

---

## Decision 1: JWT Decoding Abstraction — Raw vs Pre-Decoded

### Context
The module needs to validate JWT tokens from inbound webhook requests. Two approaches:
- **Option A: Accept raw JWT strings** — module decodes the JWT internally using an injected decoder/verifier callback. Closer to real integration.
- **Option B: Accept pre-decoded claims** — caller decodes the JWT and passes a `TokenClaims` object. Simpler and more testable.

### Analysis
Option A (raw JWT) has a cleaner public API — the caller passes the `Authorization: Bearer <token>` value and gets back an `AuthResult`. However, this couples the module to JWT parsing logic. Option B (pre-decoded) requires the caller to handle JWT decoding, splitting responsibilities awkwardly.

**Hybrid approach:** The module accepts a raw JWT string but delegates decoding + signature verification to an injected callback: `token_verifier: Callable[[str], TokenClaims | None]`. The callback returns `TokenClaims` on valid signature, `None` on invalid. This keeps the module testable (tests inject a fake verifier) while maintaining a clean public API.

### Decision
**Hybrid: Accept raw JWT string, inject a `token_verifier` callback.** The callback handles decoding and cryptographic verification. The module handles claims validation (issuer, audience, expiry) and replay detection. Tests inject a simple lambda that constructs `TokenClaims` directly.

---

## Decision 2: Replay Cache Implementation

### Context
The replay cache must:
- O(1) lookup for token IDs
- Bounded memory (max entries or TTL)
- Thread-safe (single-threaded async, but defensive)
- Auto-evict expired entries

### Analysis
Three options considered:

| Option | Pros | Cons |
|--------|------|------|
| `dict` + periodic sweep | Simple | Manual cleanup scheduling; unbounded between sweeps |
| `OrderedDict` + size cap | O(1) lookup; insertion-ordered for eviction | Time-based eviction requires scanning |
| `dict` with TTL + size cap | O(1) lookup; lazy eviction on access + periodic trim | Slightly more complex but best balance |

The best approach is a **dict with lazy TTL eviction**: on each `check_replay()` call, first check if the token ID exists and is within the window. Periodically (every N calls or when size exceeds max), sweep expired entries. This avoids the overhead of a background timer while keeping memory bounded.

### Decision
**Dict with lazy TTL eviction + hard size cap.** Implementation as a `ReplayCache` class with `check_and_record(token_id, now) -> bool` method. Eviction runs when cache size exceeds `max_replay_cache_size`. Entries older than `replay_window_seconds` are evicted.

---

## Decision 3: Signature Verification Model

### Context
Should the `token_verifier` callback handle all cryptographic operations, or should the module assist with JWT structure parsing?

### Analysis
Keeping all crypto operations in the callback maximizes modularity. The module's responsibility is **claims-level validation** (business logic), not cryptographic verification. In production, the callback would use `pyjwt` or `msal` to verify against Microsoft's JWKS. In tests, the callback is a simple function that returns pre-built claims.

The callback signature: `Callable[[str], TokenClaims | None]`
- Input: raw JWT string (the `Bearer` token)
- Output: `TokenClaims` if signature is valid, `None` if verification fails
- The callback may also raise exceptions for malformed tokens — the module catches these and returns an auth failure.

### Decision
**Callback handles all cryptographic operations.** Module catches exceptions from the callback and treats them as signature verification failures. Module is responsible for issuer, audience, expiry, and replay checks only.

---

## Decision 4: Multiple Issuer Support

### Context
Azure AD tokens may come from different issuers:
- Bot Framework v3.1: `https://api.botframework.com`
- Azure AD v1.0: `https://sts.windows.net/{tenant-id}/`
- Azure AD v2.0: `https://login.microsoftonline.com/{tenant-id}/v2.0`
- Government/sovereign clouds: different base URLs

### Analysis
The simplest approach is a tuple of allowed issuer strings in `AuthConfig`. The validator checks if the token's `iss` claim matches **any** of the allowed issuers. For v2.0 tenant-specific issuers, the caller can include the specific tenant URL in the tuple.

Wildcard matching (e.g., `https://login.microsoftonline.com/*/v2.0`) adds complexity without clear benefit — the bot knows its own tenant ID at configuration time.

### Decision
**Tuple of exact issuer strings.** Default includes `https://api.botframework.com` and `https://login.microsoftonline.com/botframework.com/v2.0`. The caller can extend the tuple for tenant-specific issuers.

---

## Decision 5: Token ID for Replay Detection

### Context
Not all JWT tokens carry a `jti` (JWT ID) claim. Bot Framework tokens typically do, but it's not guaranteed.

### Analysis
If `jti` is present, use it directly. If not, compute a SHA-256 hash of the full token string as the identifier. This ensures every token has a unique replay key. The hash approach is collision-resistant and deterministic.

### Decision
**Use `jti` if present; fall back to SHA-256 hash of the raw token string.** The `token_id` field in `AuthResult` always contains the identifier used for replay tracking.

---

## Recommended Architecture

### Module: `tech_dev_agents/webhook_auth.py`

```
AuthConfig (frozen dataclass):
    bot_app_id: str
    allowed_issuers: tuple[str, ...]
    clock_skew_seconds: int = 300
    replay_window_seconds: int = 300
    max_replay_cache_size: int = 10000

TokenClaims (frozen dataclass):
    issuer: str
    audience: str
    expiration: float
    issued_at: float
    token_id: str
    service_url: str | None
    raw_claims: dict[str, Any]

AuthResult (frozen dataclass):
    is_authenticated: bool
    failure_reason: str | None
    claims: TokenClaims | None
    token_id: str
    checked_at: str
    def to_audit_dict() -> dict[str, Any]

ReplayCache:
    __init__(window_seconds, max_size)
    check_and_record(token_id, now) -> bool  # True if replay detected
    _evict_expired(now) -> None

authenticate_webhook(token, config, token_verifier, *, clock, replay_cache) -> AuthResult
```

### Function: `authenticate_webhook()`

The main entry point. Sequence:
1. Call `token_verifier(token)` — if it returns `None` or raises, return auth failure ("signature verification failed")
2. Check `claims.issuer` against `config.allowed_issuers` — reject if not found
3. Check `claims.audience` against `config.bot_app_id` — reject if mismatch
4. Check `claims.expiration` against current time + clock skew — reject if expired
5. Check `replay_cache.check_and_record(claims.token_id)` — reject if replay
6. Return authenticated `AuthResult` with full claims

### Integration Point

The Teams Bot HTTP handler (STORY-002) calls `authenticate_webhook()` before passing the activity to `AgentBot.handle_message()`. On failure, the handler returns HTTP 401/403.

### Test File
- `tests/test_webhook_auth.py` — 12-15 tests covering all acceptance criteria
