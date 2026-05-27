# UX Review: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 6c — UX Review
> Date: 2026-03-31
> Story: STORY-010
> Scope: Medium

---

## UX Surface

This story is a **backend security module** with no direct user-facing UI. The UX surface is limited to:

1. **Error responses** — HTTP 401 returned to unauthorized webhook requests (seen by Bot Framework, not end users).
2. **Audit log output** — Consumed by operators via Grafana/Loki dashboards.
3. **Configuration** — `AuthConfig` parameters set by the operator at deployment time.

## Review

| Aspect | Assessment | Notes |
|--------|-----------|-------|
| End-user impact | None | Auth failures are invisible to Teams users; the bot simply doesn't respond to spoofed messages |
| Operator experience | Good | `to_audit_dict()` provides structured, searchable log output for debugging auth failures |
| Configuration clarity | Good | `AuthConfig` has sensible defaults; operator only needs to provide `bot_app_id` via `build_default_auth_config()` |
| Error message clarity | Good | Failure reasons are descriptive ("invalid issuer", "token expired") without leaking sensitive data |
| Failure mode visibility | Good | Every auth decision is logged via `to_audit_dict()`, enabling post-incident analysis |

## Findings

- No UX findings. This is an infrastructure module with no end-user interaction surface.

## Verdict

**APPROVED** — No user-facing UX concerns. Operator experience is well-served by structured audit output and sensible defaults.
