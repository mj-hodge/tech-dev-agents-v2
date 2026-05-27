# Ops Review — STORY-305: Fix Cost Display

**Phase:** 6d — Ops Review
**Story:** STORY-305
**Date:** 2026-04-15
**Scope:** Small/Medium

---

## Summary

STORY-305 is a service-layer bug fix with no infrastructure changes. No new environment variables, deployments, or operational runbooks are required.

**Overall verdict: APPROVED**

---

## Operational Impact

| Area | Impact | Notes |
|------|--------|-------|
| New infrastructure | None | Service layer change only |
| Environment variables | None | All existing vars unchanged |
| Database migrations | None | No schema changes |
| Cache behaviour | Unchanged | 5-minute TTL on cost data unchanged |
| Azure Cost API usage | Unchanged | Same API calls, same rate limits |
| Monitoring / alerting | None required | Existing cost endpoint metrics cover this |

---

## Deployment

The fix is deployed as part of a normal ops-console service update:

1. Pull updated code to ops-console VM
2. Restart ops-console service: `systemctl restart ops-console`
3. Verify agent card costs are non-zero on next page refresh (allow up to 5 minutes for cache to expire)

No coordination with agent VMs required.

---

## Rollback

Revert the `azure_cost_client.py` and `cost_service.py` changes and restart ops-console. Agent cards will return to showing `$0.00` for Azure costs (the original broken state), but no data is lost.

---

## Monitoring

After deploy, verify in Loki/Grafana:
- `ops_console_cost_azure_usd_total` counter is non-zero (if metric exists)
- No new errors in ops-console logs related to cost parsing
