# Test Design — STORY-305: Fix Dashboard Cost Display

**Phase:** 7 — Test Design
**Story:** STORY-305 — Fix dashboard cost display: foundry_cost_usd always zero
**Date:** 2026-04-16
**Test Count:** 45 (5 new + 40 pre-existing, all GREEN)

---

## Test Strategy

Unit tests only — the classification logic is deterministic and does not require integration with Azure. Existing integration tests cover the `CostService` API call path and are unaffected.

---

## New Test Class: `TestCostClassification`

Located in `tests/ops_console/test_cost_service.py`.

### T-CLS-01 — OpenAI resource group prefix routes to openai_cost_usd

```python
def test_classify_oai_prefix_returns_openai():
    result = CostService._classify_cost("oai-prod-eastus", "")
    assert result == "openai_cost_usd"
```

### T-CLS-02 — Foundry resource group routes to foundry_cost_usd

```python
def test_classify_foundry_rg_returns_foundry():
    result = CostService._classify_cost("rg-aiservices-prod", "")
    assert result == "foundry_cost_usd"
```

### T-CLS-03 — Unknown resource group falls back to azure_cost_usd

```python
def test_classify_unknown_rg_returns_azure():
    result = CostService._classify_cost("rg-compute-prod", "")
    assert result == "azure_cost_usd"
```

### T-CLS-04 — Empty resource group string falls back to azure_cost_usd

```python
def test_classify_empty_rg_returns_azure():
    result = CostService._classify_cost("", "")
    assert result == "azure_cost_usd"
```

### T-CLS-05 — Classification is case-insensitive

```python
def test_classify_is_case_insensitive():
    assert CostService._classify_cost("OAI-Dev", "") == "openai_cost_usd"
    assert CostService._classify_cost("RG-Foundry-Staging", "") == "foundry_cost_usd"
```

---

## Updated Fixture Expectations

The following pre-existing tests had their expected values updated to reflect the new three-bucket model. They were previously asserting that all cost went into `azure_cost_usd` — they now assert correct classification.

### T33 — `test_parse_cost_response_assigns_foundry_bucket`

Previously: `assert record.azure_cost_usd == 10.00`
Updated to: `assert record.foundry_cost_usd == 10.00` (for an `aiservices-*` resource group fixture)

### T48 — `test_aggregate_today_sums_foundry_and_openai`

New assertion added: `assert today.foundry_cost_usd > 0` and `assert today.openai_cost_usd > 0`

### T49 — `test_cost_today_api_response_includes_new_fields`

Updated expected JSON schema to include `foundry_cost_usd` and `openai_cost_usd` keys.

---

## Total Continuity

All 45 tests pass. No tests were deleted. Pre-existing tests covering:
- Azure Cost Management API mocking
- `CostToday` serialisation
- Route handler response codes
- Auth-gated endpoint access

...continue to pass unchanged.

---

## Test Run Command

```bash
pytest tests/ops_console/test_cost_service.py -v
```

Expected output: `45 passed in <Xs>`
