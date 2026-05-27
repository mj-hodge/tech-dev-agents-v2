# Site Reliability: STORY-010 Teams Webhook Auth Hardening & Replay Protection

> Phase 10 — Site Reliability / Operations
> Date: 2026-03-31
> Story: STORY-010
> Scope: Medium

## Operational Profile

| Attribute | Value |
|-----------|-------|
| Runtime model | Library module called on every inbound webhook HTTP request |
| State storage | ReplayCache: in-memory dict, bounded to 10,000 entries (~1 MB max) |
| External dependencies | None in the module itself; token verifier is injected by the caller |
| Concurrency model | Single-threaded async; no locks. Thread-safe for read-only config. |
| Expected invocation frequency | Once per inbound Teams webhook (1-100 requests/minute typical) |
| Latency per call | Sub-millisecond (dict lookup + numeric comparisons) |

## Failure Modes

| Failure | Detection | Impact | Recovery |
|---------|-----------|--------|----------|
| Token verifier callback fails | Auth failure with "verification error" in audit log | All webhooks rejected (HTTP 401); bot appears unresponsive | Fix verifier or key provider; restart container |
| Wrong bot_app_id in config | 100% auth failure with "invalid audience" in logs | No webhooks processed | Fix AuthConfig; redeploy |
| Allowed issuers list incomplete | Legitimate tokens from unlisted issuer rejected | Partial webhook rejection | Add missing issuer to allowed_issuers; redeploy |
| Clock drift > clock_skew_seconds | "token expired" failures for valid tokens | Webhooks rejected despite valid tokens | Sync NTP on container host; or increase clock_skew_seconds |
| Replay cache memory at max | Oldest 10% evicted; brief gap in replay protection | Theoretical replay window during eviction | Self-healing — eviction creates space automatically |
| Container restart | Replay cache lost | Brief window where replays of in-flight tokens could succeed | Self-healing — cache rebuilds naturally; window is seconds |

## Restart Behavior

### Startup Sequence

1. Import `webhook_auth` module (no initialization side effects)
2. Build `AuthConfig` via `build_default_auth_config(bot_app_id)` — reads app ID from environment/secrets
3. Instantiate `ReplayCache(window_seconds=300, max_size=10_000)` — shared across all handler invocations
4. Wire `authenticate_webhook()` into the HTTP handler middleware
5. Begin accepting webhook traffic

### Idempotency Guarantees

- `authenticate_webhook()` is deterministic for the same inputs (given the same clock and cache state)
- `build_default_auth_config()` returns the same config on every call (constant data + provided app ID)
- `build_token_claims()` is a pure function — same inputs always produce same output
- `ReplayCache` is the only stateful component; it is explicitly instantiated and managed

### Graceful Shutdown

No special shutdown procedure needed. The replay cache is in-memory and ephemeral. No persistent state to flush.

## Monitoring

| Signal | Source | Alert Threshold |
|--------|--------|----------------|
| Auth failure rate | `to_audit_dict()` logged on every request | > 10% failure rate over 5 minutes |
| Signature verification failures | `failure_reason` = "signature verification failed" | Any occurrence (may indicate key rotation issue) |
| Issuer validation failures | `failure_reason` contains "invalid issuer" | Any occurrence (unexpected token source) |
| Replay detections | `failure_reason` contains "replay" | > 5 per minute (may indicate active attack) |
| Token expiration failures | `failure_reason` = "token expired" | Sustained rate (may indicate clock drift) |
| Replay cache size | `replay_cache.size()` (if exposed as metric) | > 8,000 entries (80% of default max) |

## Runbook

### All webhooks failing (100% auth failure)

1. Check audit logs for the `failure_reason` — it tells you exactly which check failed
2. If "invalid audience": verify `bot_app_id` in config matches Azure AD app registration
3. If "invalid issuer": verify `allowed_issuers` includes Microsoft's current token endpoints
4. If "signature verification failed": check that the token verifier has access to current JWKS keys; Microsoft rotates keys periodically
5. If "token expired" on all requests: check NTP sync on the container host

### Replay attack suspected

1. Check audit logs for "replay detected" entries — note the `token_id` values
2. High replay rate from same source IP may indicate captured token being replayed
3. The replay cache automatically blocks duplicates within the 5-minute window
4. If attack persists, consider blocking the source IP at the network/WAF layer (outside this module's scope)

### After Azure AD key rotation

1. Microsoft rotates JWKS signing keys periodically
2. The token verifier (injected, not this module) must cache keys with TTL and refresh
3. If key rotation causes validation failures, the verifier's key cache needs refreshing
4. This module does not cache keys — it delegates entirely to the verifier callback

### Scaling to multiple instances

1. Current replay cache is per-instance (in-memory)
2. For multi-instance deployment, replace ReplayCache with a Redis-backed implementation
3. The `authenticate_webhook()` function accepts any `ReplayCache` — swap the implementation without changing the auth logic
4. Alternatively, accept the small replay gap between instances if the risk is tolerable
