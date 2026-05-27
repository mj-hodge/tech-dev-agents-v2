# Ops Review: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 6d — Ops Review
> Date: 2026-03-31
> Story: STORY-010
> Scope: Medium

---

## Operational Characteristics

| Attribute | Value |
|-----------|-------|
| Runtime model | Library module called on every inbound webhook request |
| State | ReplayCache is in-memory, bounded, ephemeral (lost on restart) |
| External dependencies | None — key provider is injected; no network calls in the module itself |
| Memory footprint | ReplayCache: max 10,000 entries x ~100 bytes = ~1 MB worst case |
| Latency impact | Sub-millisecond per request (dict lookup + claims comparison) |
| Concurrency | Single-threaded async; no locks needed for v1 |

## Deployment Considerations

| Concern | Assessment |
|---------|-----------|
| Container restart | Replay cache is lost; brief window (~seconds) where replays of very recent tokens could succeed. Acceptable for v1. |
| Rolling deployment | New instance starts with empty cache; old instance retains cache. No coordination needed — overlap window is short. |
| Multi-instance scaling | Replay cache is per-instance. For multi-instance, tokens could bypass replay detection by hitting different instances. Needs shared cache (Redis) for multi-instance — documented as out of scope for v1. |
| Configuration changes | AuthConfig is immutable; changes require redeployment. No hot-reload needed for security config. |
| Monitoring | `to_audit_dict()` on every request enables Grafana alerting on auth failures |

## Failure Modes

| Failure | Impact | Detection | Recovery |
|---------|--------|-----------|----------|
| Token verifier callback fails | All webhooks rejected (HTTP 401) | Auth failure spike in logs | Fix verifier or key provider; restart |
| Wrong bot_app_id in config | All legitimate tokens rejected (audience mismatch) | 100% auth failure rate at startup | Fix config; redeploy |
| Clock drift > 5 minutes | Legitimate tokens rejected as expired | Auth failures with "token expired" reason | Sync NTP; adjust clock_skew_seconds if needed |
| Replay cache full, eviction fails | Oldest entries evicted; slight gap in replay protection | Cache size metric (if monitored) | Self-healing — eviction clears space |

## Findings

| ID | Severity | Finding | Recommendation |
|----|----------|---------|---------------|
| O-1 | Low | No health check integration for auth subsystem | Could add an auth health indicator to the existing health endpoint. Deferred — bot health check from STORY-001 is sufficient for v1. |

## Verdict

**APPROVED** — Low operational risk. In-memory replay cache is appropriate for single-instance deployment. Memory footprint is bounded and negligible. No blocking findings.
