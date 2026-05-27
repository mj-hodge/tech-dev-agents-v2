# Pre-Deploy Gate — STORY-305: Fix Dashboard Cost Display

**Phase:** 11 — Pre-Deploy Gate
**Story:** STORY-305 — Fix dashboard cost display: foundry_cost_usd always zero
**Date:** 2026-04-16
**Verdict:** CONDITIONAL PASS — automated checks clear; manual staging verification required

---

## Automated Checks

| Check | Result |
|-------|--------|
| Unit tests: `pytest tests/ops_console/test_cost_service.py` | 45/45 PASS |
| Full test suite: `pytest tests/` | GREEN (no regressions) |
| Linting: `ruff check tech_dev_agents/ops_console/` | 0 errors |
| Type checking: `mypy tech_dev_agents/ops_console/models.py cost_service.py` | 0 errors |
| Secret scan: `truffleHog` | No secrets detected |
| Dependency audit: `pip-audit` | No new CVEs introduced |

---

## Code Review Gate

- Code review completed: **APPROVED** (see `code-review.md`)
- Security review completed: **APPROVED** (see `security-review.md`)
- No open blocking comments.

---

## Backward Compatibility

| Concern | Assessment |
|---------|-----------|
| `DailyCost` model change | Non-breaking — new fields have `0.0` defaults |
| `CostToday` model change | Non-breaking — new fields have `0.0` defaults |
| API response schema change | Additive only — new keys `foundry_cost_usd`, `openai_cost_usd` added; no keys removed or renamed |
| Existing dashboard consumers | Agent cards already reference `foundry_cost_usd`; they will now receive non-zero values as intended |

---

## Manual Staging Verification Required

The following checks must be performed by an operator against the staging environment before production deployment:

1. **Confirm non-zero foundry values**: Open the ops dashboard staging URL and verify at least one agent card shows a non-zero value in "Foundry Cost". This confirms the Azure Cost Management API returns line items with `aiservices-*` or `foundry-*` resource groups in the staging tenant.

2. **Confirm total cost unchanged**: Compare `total_cost_usd` from `GET /api/costs/today` before and after deployment. The total must be identical (within API polling variance); cost must not be lost or double-counted.

3. **Confirm `oai-*` classification**: If the staging tenant has `oai-*` resource groups, verify `openai_cost_usd > 0` in the API response.

4. **Check for UNKNOWN log warnings**: Review the ops console logs for any `WARNING: unclassified cost resource_group=...` entries that indicate resource groups not handled by the heuristic. If found, add the new prefix to `_classify_cost()` before promoting to production.

---

## Rollback Plan

The change is limited to computed-on-request cost data; no database schema was modified. Rollback procedure:

1. Revert the PR on GitHub.
2. Redeploy the previous image tag.
3. Cost values will return to the pre-fix state (all-zero `foundry_cost_usd`). No data is lost because costs are re-fetched from Azure on every request.

Estimated rollback time: < 5 minutes.

---

## Deploy Checklist

- [ ] Staging manual verification completed (items 1-4 above)
- [ ] No UNKNOWN classification warnings in staging logs
- [ ] Approval from one additional reviewer (ops lead)
- [ ] Deploy to production during low-traffic window (weekday 09:00-11:00 UTC recommended)
