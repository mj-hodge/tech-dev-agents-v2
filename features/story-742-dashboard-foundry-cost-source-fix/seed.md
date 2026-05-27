# STORY-742: Dashboard Foundry Cost Source Fix

**Frontend:** false

## Problem

The fleet-wide Foundry cost panel (`/api/fleet/foundry-cost`) displays inflated
totals because the `refresh_foundry_cost.py` cron script queries **all** Azure
subscription costs grouped by `ResourceId` without filtering for AI Foundry
resources.  Non-Foundry costs (VMs, storage, networking, disks, etc.) are
classified into the `"other"` bucket by `classify_resource_id()`, making the
chart unreliable for operators monitoring model inference spend.

### Root Cause

`_query_cost_management()` in `deployment/ops-console/scripts/refresh_foundry_cost.py`
sends a Cost Management query with:
- `grouping: [{"type": "Dimension", "name": "ResourceId"}]`
- **No filter** on resource type or provider namespace

Every Azure resource in the subscription is returned.  The classifier only
recognises `claude-opus-*`, `claude-sonnet-*`, `claude-haiku-*` patterns —
everything else falls through to `"other"`.

### Contrast with Per-Agent Path (Working Correctly)

The per-agent cost path in `azure_cost_client.py` groups by `ResourceGroup` and
filters through `agent_map`, so only mapped resource groups contribute to agent
cost.  The fleet-wide cron path has no equivalent filter.

## Scope: Small

Single-file fix + test.  No schema migration.  No frontend change required
(the `"other"` bucket already exists in the UI — it will simply shrink to
reflect actual unclassified Foundry models rather than all Azure infra costs).

## Test Criteria
The fix must be regression-protected:
- **Unit test** for `refresh_foundry_cost.py` query body assembly: asserts the request payload includes a `ResourceType` dimension filter equal to `microsoft.machinelearningservices/workspaces`.
- **Negative test:** with the filter removed, the test fails — proving the assertion catches drift.
- **Bucket test:** given a fixture of mixed-type cost rows (Foundry workspaces, VMs, storage), the post-fix classifier yields zero rows in the `"other"` bucket from non-Foundry sources.
- All tests deterministic (no live Azure calls) and complete in < 5 seconds.

## Validation
Phase 8 is NOT complete until ALL of the following are demonstrated in the PR description:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite passes; zero regressions |
| 2 | `pytest tests/refresh_foundry_cost/ -v` (or wherever the new tests live) | All new tests GREEN |
| 3 | Local dry-run: `python scripts/refresh_foundry_cost.py --dry-run` | Output shows only Foundry resources; `"other"` bucket is empty or contains only legitimate unclassified Foundry models |
| 4 | After deploy, the dashboard's Foundry cost panel shows realistic per-day numbers (no inflated totals from non-Foundry resources) | Operator visual check, screenshot in PR if possible |

## Acceptance Criteria

1. The Cost Management query in `refresh_foundry_cost.py` includes a filter that
   restricts results to AI Foundry resources only
   (`Microsoft.MachineLearningServices` provider namespace).
2. The `"other"` bucket contains only unrecognised AI model deployments, not VMs
   or storage.
3. `classify_resource_id()` behaviour is unchanged (no regression to model
   classification).
4. Existing unit tests pass; new test covers the filter presence in the query
   body.

## Files to Modify

| File | Change |
|------|--------|
| `deployment/ops-console/scripts/refresh_foundry_cost.py` | Add `filter` clause to Cost Management query body |
| `tests/deployment/test_refresh_foundry_cost.py` (new) | Verify query filter + aggregation with mixed resource types |
