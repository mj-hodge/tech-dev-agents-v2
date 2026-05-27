# Incident Post-Mortem: Foundry Auth Failure — 2026-04-30

> **STORY-771** — Morris Foundry endpoint authentication failure.

## Timeline

| Time (UTC) | Event |
|------------|-------|
| ~2026-04-30 01:57 | Earliest observed failure — Morris Teams bridge returns HTTP 500 |
| 2026-04-30 morning | Mark reports: Teams messages to Morris return "Failed to authenticate to backend endpoint" |
| 2026-04-30 | Investigation begins (STORY-771) |
| 2026-04-30 | Root cause identified: Azure AD SP client secret expired |
| 2026-04-30 | Health check script created (`check_foundry_auth.py`) with 7-day expiry alerting |

## Root Cause

The Azure AD service principal client secret used by Morris's M365/Teams bridge to authenticate against the Foundry inference endpoint **expired**. Azure AD client secrets have a configurable expiry (default options: 6 months, 1 year, 2 years, or custom). No automated rotation or expiry monitoring was in place.

## Impact

- **Duration:** Unknown start → 2026-04-30 (at least one day)
- **Affected:** Morris's Teams interaction — Mark could not message Morris via Teams
- **Workaround:** Morris's `sdk_dispatch_post_ratelimit.py` fallback path (`claude --permission-mode bypassPermissions`) partially compensated, but Teams round-trip was broken
- **Blast radius:** Morris fleet shepherding via Teams, overnight heartbeat cron (if it uses the same credential path)

## Why Auto-Renewal Did Not Exist

1. No rotation cron was configured for the SP secret
2. No expiry monitoring existed (no alerting when credential approached expiry)
3. The credential was set up as a one-time provisioning step with no documented renewal procedure

## Corrective Actions

| Action | Status | Owner |
|--------|--------|-------|
| Create `check_foundry_auth.py` health check with 7-day expiry alerting | ✅ Done (STORY-771) | Dev team |
| Document rotation runbook (`state/morris/foundry-auth-runbook.md`) | ✅ Done (STORY-771) | Dev team |
| Mark rotates the expired credential in Azure Portal | ⏳ Pending | Mark |
| Add `check_foundry_auth.py` to Morris cron (daily run) | ⏳ Pending | Mark |
| Verify Teams round-trip works after credential rotation | ⏳ Pending | Mark |
| Revoke old expired credential | ⏳ Pending | Mark |

## Lessons Learned

1. **Every credential needs an expiry alarm.** Silent expiry is the default failure mode for Azure AD secrets.
2. **Runbooks must exist before credentials are provisioned.** The renewal procedure should be documented at provisioning time, not after an outage.
3. **Health checks should cover auth dependencies.** Morris's heartbeat checked process liveness but not credential validity.

## References

- Diagnosis: `features/story-771-morris-foundry-auth-investigation/diagnosis.md`
- Runbook: `state/morris/foundry-auth-runbook.md`
- Health check: `deployment/morris/scripts/check_foundry_auth.py`
- Test: `tests/deployment/test_check_foundry_auth_771.py`
