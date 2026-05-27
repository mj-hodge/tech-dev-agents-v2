# Phase 6b: Security Review -- STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Reviewer:** Security Reviewer (Phase 6b)
**Scope:** Medium (bug-fix, no new auth flows, no new external APIs, no frontend changes)
**Input:** `feature-spec.md`, `seed.md`, existing codebase (`auth.py`, `loki_client.py`, routes)

---

## Scope of Review

This story fixes data pipeline bugs in an internal ops dashboard. Changes are confined to:

- MCP TypeScript client envelope unwrapping (client-side parsing)
- Python regex fix for `[DONE]` log line parsing
- New systemd timer for cost collection
- Response model enrichment (additive fields)
- No new authentication flows, no new external API integrations, no frontend changes

The attack surface change is minimal. This review focuses on whether the fixes introduce or expose vulnerabilities.

---

## Threat Model Summary

| Actor | Access | Motivation |
|-------|--------|------------|
| Internal operator (Mark) | Authenticated via X-API-Key | Legitimate use |
| Compromised agent VM | Network access to Loki, local log write | Log injection, cost spoofing |
| Network attacker | Must bypass API key auth | Data exfiltration |

The dashboard is an internal tool behind API key auth, serving 2-5 agent VMs. There is no public internet exposure beyond the authenticated API.

---

## Findings

| ID | Severity | Finding | Recommendation | Status |
|----|----------|---------|----------------|--------|
| SEC-001 | **Low** | **LogQL injection via agent name** -- Agent names are used in Loki queries. The existing `sanitize_label_value()` function in `loki_client.py` strips unsafe characters (`{}|=~!\`"\\`). This is adequate for the current 2-5 hardcoded agent set. | No change needed for this story. If agent names become user-supplied in the future, add an allowlist check. | ACCEPTABLE |
| SEC-002 | **Low** | **Regex denial of service** -- The proposed replacement regexes (`cost=\$?(?P<cost>[\d.]+)` and `(?<!\w)turns=(?P<turns>\d+)`) use bounded character classes with no nested quantifiers. These cannot cause catastrophic backtracking. The existing `_DONE_RE` with `.*?` also has linear-time behavior due to non-overlapping anchors. | No change needed. The new regexes are strictly safer than the old one. | ACCEPTABLE |
| SEC-003 | **Low** | **Cost data spoofing via log injection** -- A compromised agent VM could write crafted `[DONE]` lines with inflated `cost=` values. The regex has no validation that cost values are within a reasonable range. | Add a sanity-cap check in `_parse_done_line()`: reject `cost > 500.0` (no single session should exceed this). Log a warning when capped. This is defense-in-depth; the primary control is VM access security. | RECOMMENDED |
| SEC-004 | **Info** | **API response envelope data exposure** -- The envelope fields (`total`, `fetched_at`, `active_count`) expose metadata about fleet size and query timing. For an internal tool this is expected and useful. | No change needed. These fields are intentional for the ops use case. | ACCEPTABLE |
| SEC-005 | **Medium** | **Error message information leakage** -- Several routes expose internal details in HTTP error responses: `routes/messages.py` returns `f"Graph API error: {exc}"` in 502 responses, which could leak Graph API URLs, token fragments, or internal hostnames. `routes/agents.py` returns `detail=str(e)` for 400 errors, which could expose stack trace fragments. | Sanitize error messages in 4xx/5xx responses. Replace `str(exc)` with generic messages; log the full exception server-side. Example: `HTTPException(status_code=502, detail="Upstream service error")`. | RECOMMENDED |
| SEC-006 | **Info** | **Credential handling** -- API key is read from `app.state.settings` (loaded from environment). Monday.com and Teams tokens are managed via existing service clients, not introduced by this story. No new credential storage is added. | No change needed. Existing credential handling is unchanged. | ACCEPTABLE |
| SEC-007 | **Low** | **Systemd service runs as `hermes` user** -- The cost-collector service runs as `User=hermes`, which is the same user that runs agent workloads. A compromised cost-collector could modify agent files. | Acceptable for internal tooling at current scale. If the fleet grows, consider a dedicated `ops-collector` service account with read-only log access. | ACCEPTABLE |
| SEC-008 | **Info** | **No input validation on query parameters** -- The `days` parameter for cost breakdown (7, 30) is hardcoded in `routes/agents.py`, not user-supplied. The `limit` parameter for messages is already an integer type via FastAPI's `Query()` typing. No injection vector exists. | No change needed. FastAPI's type validation handles this. | ACCEPTABLE |

---

## Summary of Recommendations

1. **SEC-005 (Medium):** Sanitize error details in HTTP 502 and 400 responses to avoid leaking Graph API internals. Log full exceptions server-side only.
2. **SEC-003 (Low):** Add a sanity cap on parsed cost values as defense-in-depth against log injection.

Both recommendations are improvements, not blockers. The story introduces no new attack surface and the existing auth/sanitization controls are adequate.

---

## Verdict

**APPROVED WITH CONDITIONS**

Conditions (non-blocking, should be addressed during Phase 8 implementation):

1. Error messages in `routes/messages.py` (lines returning `f"Graph API error: {exc}"`) and `routes/agents.py` (returning `str(e)`) should use generic messages with server-side logging of the full exception.
2. Consider adding a cost sanity cap in `_parse_done_line()` to reject unreasonable values.

These conditions are proportional to the Medium scope and internal-only nature of this tool. Neither is a blocking defect.
