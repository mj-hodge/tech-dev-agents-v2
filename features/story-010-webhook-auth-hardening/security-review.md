# Security Review: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 6b — Security Review
> Date: 2026-03-31
> Story: STORY-010
> Scope: Medium

---

## Threat Model

| Threat | Severity | Mitigation in Spec | Status |
|--------|----------|-------------------|--------|
| Spoofed webhook payload from attacker | Critical | JWT signature verification via injected verifier | Addressed |
| Replayed legitimate webhook | High | Time-windowed replay cache with jti/hash tracking | Addressed |
| Expired token accepted | High | Expiration check with configurable clock skew | Addressed |
| Wrong audience (token for different app) | High | Audience claim validated against bot_app_id | Addressed |
| Token from unauthorized issuer | High | Issuer validated against allowed_issuers tuple | Addressed |
| Replay cache memory exhaustion (DoS) | Medium | Bounded cache with max_size + lazy eviction | Addressed |
| Token content leaked in logs | Medium | AuthResult.to_audit_dict() excludes raw token; only token_id logged | Addressed |
| Clock skew exploitation | Low | 5-minute tolerance is standard; configurable for tighter control | Addressed |

## Review Checklist

| Check | Status | Notes |
|-------|--------|-------|
| No raw tokens in error messages or logs | PASS | Spec explicitly states failure reasons describe the step, not content |
| No secrets stored in data classes | PASS | AuthConfig holds app ID (not a secret), issuers (public), and numeric thresholds |
| Replay detection uses secure identifier | PASS | jti claim preferred; SHA-256 hash fallback is collision-resistant |
| Cache bounded to prevent OOM | PASS | max_replay_cache_size with eviction; oldest 10% removed when full |
| Exception handling prevents information leak | PASS | Verifier exceptions caught; only str(exc) included, not stack traces |
| Frozen dataclasses prevent mutation | PASS | AuthConfig, TokenClaims, AuthResult all frozen |
| Clock injection prevents time-of-check attacks in tests | PASS | Deterministic testing via injectable clock |
| Multi-issuer support for Azure AD versions | PASS | Tuple of allowed issuers covers v1/v2 endpoints |

## Findings

| ID | Severity | Finding | Recommendation | Status |
|----|----------|---------|---------------|--------|
| S-1 | Low | Replay cache is in-memory only; lost on restart | Acceptable for v1 single-instance. Document that multi-instance needs shared cache (Redis). | Acknowledged — documented in spec Section 11 |
| S-2 | Low | No `nbf` (not-before) claim validation | Bot Framework tokens typically don't carry `nbf`. Add if future token formats include it. | Deferred — non-blocking |

## Verdict

**APPROVED** — The spec addresses all identified threats for a single-instance Teams bot. Two low-severity findings acknowledged and documented. No blocking issues.
