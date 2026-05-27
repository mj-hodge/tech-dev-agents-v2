# Feature Spec — STORY-305: Fix Dashboard Cost Display

**Phase:** 6 — Feature Specification
**Story:** STORY-305 — Fix dashboard cost display: foundry_cost_usd always zero
**Scope:** Medium
**Date:** 2026-04-16

---

## Overview

Extend the cost tracking models and service to correctly classify Azure cost line items into three independent buckets (`foundry_cost_usd`, `openai_cost_usd`, `azure_cost_usd`) so that the ops dashboard agent cards display accurate per-category spend.

---

## Model Changes

### `DailyCost` (models.py)

```python
@dataclass
class DailyCost:
    date: str
    total_cost_usd: float
    azure_cost_usd: float       # existing — non-AI Azure spend
    foundry_cost_usd: float = 0.0   # NEW — Azure AI Foundry spend
    openai_cost_usd: float = 0.0    # NEW — Azure OpenAI (oai-* RGs) spend
```

Default values of `0.0` ensure backward compatibility: existing callers that do not set these fields continue to work.

### `CostToday` (models.py)

```python
@dataclass
class CostToday:
    total_cost_usd: float
    azure_cost_usd: float
    foundry_cost_usd: float = 0.0   # NEW
    openai_cost_usd: float = 0.0    # NEW
    agent_costs: dict[str, float] = field(default_factory=dict)
```

---

## Service Changes

### `_classify_cost()` (cost_service.py)

New private method. Classifies a single Azure cost line item into one of three cost buckets based on resource group name.

```python
@staticmethod
def _classify_cost(resource_group: str, meter_category: str = "") -> str:
    """
    Classify an Azure cost line item into a cost bucket.

    Interim heuristic — uses resource group prefix.
    TODO: revisit with meter_category once Azure billing API v2 strings are stable.

    Returns one of: "openai_cost_usd", "foundry_cost_usd", "azure_cost_usd"
    """
    rg = (resource_group or "").lower()
    if rg.startswith("oai-"):
        return "openai_cost_usd"
    if "foundry" in rg or "aiservices" in rg:
        return "foundry_cost_usd"
    return "azure_cost_usd"
```

### `_parse_cost_response()` (cost_service.py)

Updated to call `_classify_cost()` for each line item and increment the appropriate field.

```python
# Before
record.azure_cost_usd += item["cost"]

# After
bucket = self._classify_cost(
    item.get("resourceGroup", ""),
    item.get("meterCategory", ""),
)
setattr(record, bucket, getattr(record, bucket) + item["cost"])
```

### `aggregate()` (cost_service.py)

Updated to sum `foundry_cost_usd` and `openai_cost_usd` independently across all daily records, in addition to the existing `azure_cost_usd` aggregation.

```python
totals = CostToday(
    total_cost_usd=sum(r.total_cost_usd for r in records),
    azure_cost_usd=sum(r.azure_cost_usd for r in records),
    foundry_cost_usd=sum(r.foundry_cost_usd for r in records),   # NEW
    openai_cost_usd=sum(r.openai_cost_usd for r in records),     # NEW
    agent_costs=self._aggregate_agent_costs(records),
)
```

---

## Route Handler Changes

### `GET /api/costs/today` (cost_routes.py)

Response schema extended with new fields:

```json
{
  "total_cost_usd": 12.34,
  "azure_cost_usd": 3.10,
  "foundry_cost_usd": 7.44,
  "openai_cost_usd": 1.80,
  "agent_costs": { ... }
}
```

### `GET /api/costs/daily` (cost_routes.py)

Each daily record in the array now includes `foundry_cost_usd` and `openai_cost_usd`.

---

## Backward Compatibility

- All new fields have default values of `0.0`.
- Existing consumers of `azure_cost_usd` continue to receive a value (non-AI spend only post-fix, but still a valid number).
- No database schema changes — costs are not persisted; they are computed on request from the Azure Cost Management API.
- No breaking changes to existing API consumers.

---

## Configuration

No new environment variables are required. The classification logic is entirely code-based.

`.env.example` is unchanged.

---

## Non-Goals

- This story does NOT implement per-agent cost breakdown by category (only total per agent).
- This story does NOT add meter-category-based classification (deferred pending API stability).
- This story does NOT add historical trend charts for foundry vs openai spend (deferred to STORY-024).
