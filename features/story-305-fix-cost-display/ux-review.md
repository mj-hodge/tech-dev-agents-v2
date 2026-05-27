# UX Review — STORY-305: Fix Cost Display

**Phase:** 6c — UX Review
**Story:** STORY-305
**Date:** 2026-04-15
**Scope:** Small/Medium

---

## Summary

STORY-305 fixes a display bug where agent card Azure costs showed `$0.00`. After this fix, agent cards show the real Azure AI Foundry spend for each agent.

**Overall verdict: APPROVED**

---

## UX Impact

### Before

Agent cards in the ops-console fleet overview displayed:

```
Today's cost: $0.00  (Azure Foundry)
               $X.XX  (SDK / Loki)
```

The Azure Foundry figure was always zero, creating confusion about whether Azure billing was working.

### After

Agent cards correctly display the Azure AI Foundry cost aggregated from the Azure Cost Management API:

```
Today's cost: $Y.YY  (Azure Foundry)
               $X.XX  (SDK / Loki)
```

### Latency

No change to page load time. The Azure Cost API call cadence is unchanged; cost data is cached for 5 minutes (existing TTL).

### Data Freshness

Azure Cost Management data typically lags 24 hours. The UI already displays a "data freshness" indicator. No change required.

---

## Accessibility

No UI component changes. No accessibility impact.

---

## Verdict

**APPROVED — no conditions.** The fix corrects a data accuracy bug with no negative UX side effects.
