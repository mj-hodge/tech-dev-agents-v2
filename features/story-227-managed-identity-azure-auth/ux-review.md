# STORY-227: UX Review (Phase 6c)

**Date:** 2026-04-15
**Reviewer:** UX Review Agent
**Verdict:** APPROVED

---

## User Impact Assessment

### Primary User: Mark (Engineering Manager)
**Before:** Must run `az ad sp create-for-rbac`, extract tenant/client/secret, manually add to .env, restart service. 30+ min per incident.
**After:** Runs 4 az CLI commands once (provided in feature-spec Section 3). Identity auto-discovered by code. No .env changes needed for new VMs.

### Secondary User: Agents (ops-console consumers)
**Before:** No change in API behavior.
**After:** No change in API behavior. `/api/fleet` returns same data.

## Friction Analysis

| Step | Friction Level | Notes |
|------|---------------|-------|
| Run az CLI commands | Low | Copy-paste from feature-spec |
| Verify identity attached | Low | `az vm identity show` confirms |
| Remove old env vars | Low | One-time cleanup per VM |
| Rollback if needed | Low | Re-add env vars (EnvironmentCredential fallback) |

## UX Improvements

1. **Zero-config for new VMs:** `deploy-agent.sh` handles identity automatically
2. **No secret rotation:** Managed identity tokens rotate automatically
3. **Clear error messages:** DefaultAzureCredential provides descriptive errors when no credential found
4. **Runbook included:** NEW-AGENT-PROCESS.md has step-by-step for existing VMs

## Recommendation

**APPROVED.** Significant UX improvement for Mark (eliminates recurring credential incidents). No user-facing API changes.
