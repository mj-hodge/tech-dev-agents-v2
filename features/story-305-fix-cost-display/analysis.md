# Analysis — STORY-305: Fix Dashboard Cost Display

**Phase:** 4 — Analysis
**Story:** STORY-305 — Fix dashboard cost display: foundry_cost_usd always zero
**Date:** 2026-04-16

---

## Root Cause Analysis

### Bug Location

`tech_dev_agents/ops_console/cost_service.py` — `_parse_cost_response()`

### Code Path (before fix)

```
CostService.get_daily_costs()
  → _call_azure_cost_api()          # returns raw line items
  → _parse_cost_response(response)   # BUG: maps everything to azure_cost_usd
  → aggregate()                      # foundry_cost_usd sums zero items
  → route handler                    # returns foundry_cost_usd = 0.0
  → agent card template              # renders "$0.00"
```

### Why it happened

The original implementation pre-dated Azure AI Foundry as a distinct billing category. At the time, all Azure spend was legitimately a single `azure_cost_usd` bucket. When the `DailyCost` model was extended with `foundry_cost_usd` (as part of STORY-024), the field was added to the model but `_parse_cost_response()` was not updated to populate it. The field silently defaulted to `0.0`.

### Impact

- All agent cards in the ops dashboard show `$0.00` for foundry costs.
- Finance reporting queries against the API return incorrect cost breakdowns.
- `azure_cost_usd` is inflated because it absorbs Foundry spend that should be classified separately.

---

## Solution Options

### Option 1 — Classify by resource group prefix (selected)

Use the `resourceGroup` field returned by the Azure Cost Management API. Resource groups with the `oai-*` prefix were created by the OpenAI-on-Azure provisioning script; all others that contain `foundry` in the name are Azure AI Foundry resources.

**Pros:** Simple string match, no additional API calls, deterministic for our naming conventions.
**Cons:** Relies on consistent resource group naming; will misclassify resources in non-standard RGs.

### Option 2 — Classify by meter category

Use the `meterCategory` field (`"Azure OpenAI"`, `"AI + Machine Learning"`, etc.).

**Pros:** More semantically correct; does not depend on naming conventions.
**Cons:** Meter category strings are not stable across Azure billing API versions; have changed at least once in the last 18 months.

### Option 3 — Manual mapping table

Maintain a config file mapping resource IDs to cost categories.

**Pros:** Fully explicit; handles edge cases.
**Cons:** High maintenance overhead; breaks when new resources are provisioned without updating the map.

---

## Selected Approach

**Option 1** (resource group prefix) as an interim solution, with a note in the code to revisit with Option 2 when the meter category strings are confirmed stable. This is the simplest change with the lowest regression risk.

The classification heuristic:

```python
def _classify_cost(resource_group: str, meter_category: str = "") -> str:
    rg = (resource_group or "").lower()
    if rg.startswith("oai-"):
        return "openai_cost_usd"
    if "foundry" in rg or "aiservices" in rg:
        return "foundry_cost_usd"
    return "azure_cost_usd"
```

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Misclassification due to non-standard RG names | Low | Document naming convention; add warn log for unclassified resources |
| Regression in total cost sum | Low | Unit tests assert sum(foundry + openai + azure) == total |
| API response schema change | Very low | Existing tests already validate response parsing |

---

## Affected Files

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/models.py` | Add `foundry_cost_usd`, `openai_cost_usd` to `DailyCost` and `CostToday` |
| `tech_dev_agents/ops_console/cost_service.py` | Add `_classify_cost()`, update `_parse_cost_response()` and `aggregate()` |
| `tech_dev_agents/ops_console/routes/cost_routes.py` | Expose new fields in response serialisation |
| `tests/ops_console/test_cost_service.py` | New test class `TestCostClassification`; update fixtures |
| `.env.example` | No changes required (classification is code-only) |
