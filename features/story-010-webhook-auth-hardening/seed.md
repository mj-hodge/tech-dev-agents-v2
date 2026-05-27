# Seed: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 1 — Concept & Seed
> Date: 2026-03-31
> Scope: Medium
> Phase path: 1 → 4 → 5 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → [9,10] → Done
> Depends on: STORY-001 (Container Runtime & Identity), STORY-002 (Teams Bot Foundation), STORY-009 (Graph Permissions)

---

## Problem Statement

The Teams Bot Foundation (STORY-002) established the messaging contract — intent classification, routing, conversation storage — but it intentionally deferred webhook authentication. The bot currently processes any inbound HTTP POST to its messaging endpoint without verifying that the request actually originated from the Microsoft Bot Framework. Without webhook authentication hardening:

1. **Spoofed messages** — An attacker who discovers the bot's endpoint URL can craft arbitrary payloads that the bot treats as legitimate Teams messages, potentially triggering SDLC operations, approval flows, or code execution.
2. **Replay attacks** — Captured legitimate webhook payloads can be replayed to re-trigger completed approvals, duplicate story assignments, or bypass rate limits.
3. **Token trust gap** — The bot has no mechanism to verify that the JWT bearer token accompanying inbound webhooks was issued by Azure AD for the correct audience (the bot's app ID) and has not expired.
4. **Compliance gap** — Microsoft's Bot Framework Security documentation requires bots to validate inbound activity tokens. A bot that skips validation cannot pass security review for production Teams deployment.

This story introduces a **Webhook Authentication module** that:
- Validates the JWT bearer token on every inbound webhook request against Microsoft's OpenID metadata.
- Verifies issuer, audience, and expiration claims.
- Detects and rejects replay attacks using a time-windowed nonce/timestamp mechanism.
- Integrates with the Graph Permission manifest (STORY-009) for scope boundary checks.
- Provides structured audit logging for all authentication decisions.

---

## Acceptance Criteria

- [ ] **AC1: JWT signature validation** — A validator function accepts a raw JWT token string and verifies its signature against Microsoft Bot Framework's OpenID Connect metadata keys. The validator does NOT make real HTTP calls — it accepts an injected key provider.
- [ ] **AC2: Issuer validation** — The validator rejects tokens whose `iss` claim does not match the expected Bot Framework token issuer(s) (`https://api.botframework.com` and/or the Azure AD v2.0 issuer pattern).
- [ ] **AC3: Audience validation** — The validator rejects tokens whose `aud` claim does not match the bot's configured app ID.
- [ ] **AC4: Expiration check** — The validator rejects tokens whose `exp` claim is in the past (with a configurable clock skew tolerance, default 5 minutes).
- [ ] **AC5: Replay protection** — A replay detector tracks recently-seen token identifiers (the `jti` claim or a hash of the token) within a configurable time window (default: 5 minutes). Duplicate submissions within the window are rejected.
- [ ] **AC6: Structured auth result** — The validator returns an `AuthResult` frozen dataclass containing: `is_authenticated: bool`, `failure_reason: str | None`, `claims: dict` (extracted token claims on success), `token_id: str` (jti or hash), and audit-ready `to_audit_dict()` method.
- [ ] **AC7: Auth configuration** — An `AuthConfig` dataclass holds: bot app ID, allowed issuers, clock skew seconds, replay window seconds, and an injectable key provider function.
- [ ] **AC8: Replay cache bounded** — The replay cache is bounded (max entries or time-based eviction) to prevent unbounded memory growth.
- [ ] **AC9: Integration hook** — A middleware function `authenticate_webhook(token, config)` returns `AuthResult` and can be called from the bot's HTTP handler before message processing.
- [ ] **AC10: Unit tests** — Cover: valid token acceptance, expired token rejection, wrong issuer rejection, wrong audience rejection, replay detection, replay cache eviction, auth result serialization, configuration validation.

---

## Functional Scope

| Capability | In Scope |
|-----------|----------|
| JWT token signature verification (with injected keys) | Yes |
| Issuer claim validation | Yes |
| Audience claim validation | Yes |
| Expiration claim validation with clock skew tolerance | Yes |
| Replay detection via jti/token-hash with time-windowed cache | Yes |
| Bounded replay cache with eviction | Yes |
| Structured auth result with audit serialization | Yes |
| Configurable auth parameters (app ID, issuers, tolerances) | Yes |
| Middleware entry point for HTTP handler integration | Yes |
| Graph permission scope validation on inbound tokens | No — STORY-009 handles scope validation separately |
| Actual HTTP calls to OpenID metadata endpoints | No — key provider is injected |
| TLS certificate pinning | No — handled at infrastructure layer |
| Rate limiting | No — separate concern |
| User identity extraction beyond claims | No |

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **No network in validation** | JWT key provider is injected; the module never fetches keys itself. This keeps validation pure and testable. |
| **No real JWT library required for tests** | Tests construct tokens using a test helper; the validator interface accepts decoded claims + a signature verification callback. |
| **Clock injectable** | All time comparisons use an injected clock function for deterministic testing. |
| **Memory bounded** | Replay cache must have a maximum size or TTL-based eviction; no unbounded dicts. |
| **Python stdlib + minimal deps** | Module may use `hashlib` and `hmac` from stdlib. JWT decoding uses a thin abstraction layer, not a direct `pyjwt` dependency. |
| **Frozen dataclasses** | All result types are frozen (immutable) following the project pattern. |

---

## Out of Scope

- OpenID Connect metadata discovery (fetching JWKS from Microsoft endpoints)
- Token acquisition or refresh (handled by STORY-001 identity layer)
- Graph API scope enforcement (STORY-009)
- Rate limiting or DDoS protection
- Multi-tenant bot registration
- OAuth2 consent flow
- Channel-level authorization (all authenticated messages are treated equally)

---

## Dependencies

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| STORY-001: Container Runtime & Identity | Upstream | Provides the bot app ID and app password used for audience validation |
| STORY-002: Teams Bot Foundation | Upstream | Provides the HTTP handler where the auth middleware will be integrated |
| STORY-009: Graph Permissions | Upstream | Provides the permission manifest; webhook auth may pass validated token claims to scope validation |
| STORY-011: Secret Hygiene | Downstream | May consume auth audit output for compliance reporting |

---

## Data Model (Conceptual)

```
AuthConfig (frozen dataclass):
    bot_app_id: str               # Expected audience claim
    allowed_issuers: tuple[str, ...] # Expected issuer claims
    clock_skew_seconds: int       # Tolerance for exp check (default: 300)
    replay_window_seconds: int    # Time window for replay detection (default: 300)
    max_replay_cache_size: int    # Max entries in replay cache (default: 10000)

TokenClaims (frozen dataclass):
    issuer: str                   # iss claim
    audience: str                 # aud claim
    expiration: float             # exp claim (unix timestamp)
    issued_at: float              # iat claim
    token_id: str                 # jti claim (or computed hash)
    service_url: str | None       # serviceurl claim (Bot Framework specific)
    raw_claims: dict              # Full decoded claims dict

AuthResult (frozen dataclass):
    is_authenticated: bool
    failure_reason: str | None    # None on success; descriptive string on failure
    claims: TokenClaims | None    # Populated on success
    token_id: str                 # jti or hash, always populated for audit
    checked_at: str               # ISO 8601 timestamp

    def to_audit_dict() -> dict   # Structured logging output

ReplayEntry:
    token_id: str
    seen_at: float                # Unix timestamp
```

---

## Key Design Decisions for Phase 4

1. **JWT decoding abstraction:** Should the module accept raw JWT strings and decode them internally (using an injected decoder), or should it accept pre-decoded claims? Pre-decoded claims are simpler and more testable, but raw-JWT acceptance is closer to real integration.
2. **Replay cache implementation:** Simple dict with periodic cleanup vs. an OrderedDict/deque with size cap? The cache needs to be fast (O(1) lookup) and bounded.
3. **Signature verification model:** Should signature verification be a callback (`Callable[[str], TokenClaims | None]`) injected into the validator, or should the module handle base64 decoding and delegate only key lookup?
4. **Multiple issuer support:** Azure AD tokens can come from different issuer URLs depending on the token version (v1.0 vs v2.0). How should the allowed issuers list be configured?

---

## Risks

| Risk | Mitigation |
|------|-----------|
| JWT library version mismatch in production | Abstract behind a thin interface; tests use manual token construction |
| Replay cache memory growth | Bounded cache with max size + TTL eviction |
| Clock skew between bot container and Azure AD | Configurable tolerance (default 5 min) |
| Bot Framework changes token format | Issuer list is configurable; claims extraction is generic |

---

## Next Phase

**Phase 4 — Analysis**

Evaluate: JWT decoding abstraction (raw vs pre-decoded), replay cache implementation strategy, signature verification model, multi-issuer configuration, and integration contract with the Teams Bot HTTP handler.
