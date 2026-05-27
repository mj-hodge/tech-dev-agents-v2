# STORY-736: Test Design — Fix Foundry Cost Display ($0 → honest status)

**Scope:** Small  
**Coverage target:** 50% (critical paths)  
**Phase path:** 1 → 7 → 8 → Done  

---

## Summary

Tests verify the replacement of the silent-zero fallback in `cost_service.get_today_cost()` with an
explicit `foundry_cost_status` field, and the corresponding frontend rendering changes in `AgentCard`
and `FoundryCostPanel`.

**Total tests:** 16 (8 backend + 8 frontend)  
**RED state:** 12 FAIL, 4 PASS (regressions)  
**Confirmed:** `pytest` 6 FAIL/2 PASS, `vitest --run` 6 FAIL/2 PASS — all failures are AssertionError, no import errors.

---

## Test Files

| File | Type | Tests | State |
|------|------|-------|-------|
| `tests/ops_console/test_story736_cost_status.py` | pytest async | 8 | 6 FAIL / 2 PASS |
| `frontend/src/__tests__/AgentCard.story736.test.tsx` | Vitest (jsdom) | 6 | 5 FAIL / 1 PASS |
| `frontend/src/__tests__/FoundryCostPanel.story736.test.tsx` | Vitest (jsdom) | 2 | 1 FAIL / 1 PASS |

---

## Scaffolding Added (minimal — enables tests to import without errors)

1. **`tech_dev_agents/ops_console/models/responses.py`** — added `foundry_cost_status: str | None = None`
   to `CostToday`. Default `None` means tests for correct values fail (RED), not error.

2. **`frontend/src/types/api.ts`** — added `foundry_cost_status?: string | null` to `AgentSummary`.
   Optional field so existing callers are unaffected; test fixtures can pass it in.

---

## Group A — `CostToday.foundry_cost_status` per Azure state

**File:** `tests/ops_console/test_story736_cost_status.py`  
**SC:** SC-6 (backend), SC-4 (ok path)  

| Test | Azure state | Expected `foundry_cost_status` | RED reason |
|------|-------------|-------------------------------|------------|
| A01 `test_get_today_cost_azure_none_returns_status_unavailable` | `azure=None` (not configured) | `"unavailable"` | Field is `None`; `get_today_cost` doesn't set it |
| A02 `test_get_today_cost_azure_exception_returns_status_unavailable` | Azure raises exception | `"unavailable"` | Same — except block doesn't set status |
| A03 `test_get_today_cost_azure_empty_returns_status_no_usage` | Azure returns `[]` | `"no_usage"` | Distinguishes "outage" from "no spend today" |
| A04 `test_get_today_cost_azure_real_cost_returns_status_ok` | Azure returns real cost >0 | `"ok"` | Happy path — currently returns `None` |
| A05 `test_get_today_cost_status_output_varies_with_azure_config` | `None` vs real azure | Different values | Output-variance gate — both return `None` today |

### Arrange/Act/Assert (A01 representative)

```python
# Arrange
service = CostService(loki=mock_loki(5.0), azure=None)

# Act
result = await service.get_today_cost("dan")

# Assert
assert result.foundry_cost_status == "unavailable"
```

---

## Group B — Regression and model-existence checks

| Test | What it verifies | Phase 7 state |
|------|-----------------|---------------|
| B01 `test_cost_today_model_has_foundry_cost_status_field` | Field exists on `CostToday` model | **PASS** (scaffolding added) |
| B02 `test_get_today_cost_azure_exception_preserves_foundry_cost_zero` | `foundry_cost_usd` still 0.0 on exception (regression guard — only status changes) | **PASS** (existing behavior) |
| B03 `test_get_today_cost_azure_none_cost_status_is_not_none` | `foundry_cost_status is not None` when azure=None | **FAIL** (currently `None`) |

---

## Group D — AgentCard cost_status rendering (SC-2, SC-7)

**File:** `frontend/src/__tests__/AgentCard.story736.test.tsx`  
**Approach:** Vitest + `@testing-library/react` (follows existing codebase pattern)  

| Test | Scenario | Expected render | RED reason |
|------|----------|-----------------|------------|
| D01 | `cost_status="unavailable"`, foundry=0 | `"—"` in Azure Spend area | Renders `$0.00` instead |
| D02 | `cost_status="unavailable"` | Tooltip contains `"unavailable"` | Tooltip only shows cost breakdown |
| D03 | `cost_status="unavailable"`, sdk>0 | No `⚠` warning icon | Warning fires regardless of status today |
| D04 | `cost_status="stale"` | Clock/stale indicator visible | No such element exists |
| D05 (**PASS**) | `cost_status="no_usage"`, foundry=0, sdk=0 | `$0.00` displayed normally | Correct — legitimate zero, no change needed |
| D06 | Case A: `cost_status="ok"`, foundry=0, sdk>0 → warning shows; Case B: `cost_status="unavailable"`, foundry=0, sdk>0 → no warning | Conditional on status | Case B currently shows warning — fails |

### D01 Arrange/Act/Assert

```tsx
// Arrange: agent with foundry_cost_status='unavailable', foundry=0
renderCard({ ...baseAgent, today_foundry_usd: 0, today_sdk_usd: 10.25, foundry_cost_status: 'unavailable' });

// Assert: "—" in Azure Spend, not "$0.00"
const azureSpend = screen.getByText(/Azure Spend/i).closest('span');
expect(azureSpend?.parentElement?.textContent).toMatch(/—/);
expect(azureSpend?.parentElement?.textContent).not.toMatch(/\$0\.00/);
```

---

## Group F — FoundryCostPanel zero-data handling (SC-5)

**File:** `frontend/src/__tests__/FoundryCostPanel.story736.test.tsx`  
**Mock:** `useFoundryCost` hook mocked via `vi.mock('../hooks/useFoundryCost')`  

| Test | Input | Expected | RED reason |
|------|-------|----------|------------|
| F736-01 | 7 days, all `total_usd=0` | "cost data unavailable" message; no chart | Panel renders all-zero chart instead |
| F736-02 (**PASS**) | 7 days with real costs (545+487+...) | Chart + total visible | Correct existing behavior |

### Output Variance
F736-01 and F736-02 use meaningfully different inputs — the chart's total is `$0` vs `$2,161`. This pair satisfies the output-variance gate.

---

## API Mock Verification (Route Mock Verification Gate)

This story adds no new backend endpoints — all tests are unit-level with mocked services. No `page.route()` mocks used. Gate: N/A.

---

## Gate Checklist

- [x] **Gate 1 (Null/None boundary):** A01 covers `azure=None`; B03 covers the None-status case
- [x] **Gate 2a (External API isolation):** No write paths to external APIs; get_today_cost is read-only
- [x] **Gate 2b (External API degradation):** A02 covers `Exception` from Azure CM call
- [x] **Gate 8 (Migration verification):** No ORM model changes — only response model field addition; `alembic check` N/A
- [x] **Gate 9 (Failure recovery):** B02 confirms no data corruption on exception (foundry value preserved at 0.0)
- [x] **Gate 10 (Error observability):** A02 exercises the except block — Phase 8 must verify logger.warning fires (the test verifies status, Phase 8 adds the observability assertion)
- [x] **Output-variance gate:** A05 (backend), F736-01/F736-02 (frontend)
- [x] **UI reachability:** `AgentCard` is reachable via existing tests in `AgentCard.test.tsx` — no new nav paths
- [x] **UX state coverage:** SC-7 covered by D04 (stale state); SC-2 by D01-D03 (unavailable state)

---

## RED State Confirmed

```
pytest tests/ops_console/test_story736_cost_status.py
  FAIL: A01, A02, A03, A04, A05, B03  (6 failures — AssertionError on cost_status value)
  PASS: B01, B02                       (2 pass — scaffolding + regression guard)

npx vitest run src/__tests__/AgentCard.story736.test.tsx src/__tests__/FoundryCostPanel.story736.test.tsx
  FAIL: D01, D02, D03, D04, D06, F736-01  (6 failures — component doesn't use cost_status yet)
  PASS: D05, F736-02                       (2 pass — correct existing behavior)
```

All failures are `AssertionError` — no import errors, no type errors, no crashes.

---

## What Phase 8 Must Implement

### Backend (`cost_service.py`)
1. Populate `foundry_cost_status` on `CostToday` before returning:
   - `self._azure is None` → `"unavailable"`
   - Azure call raises → `"unavailable"` (in except block)
   - Azure returns `[]` → `"no_usage"`
   - Azure returns rows with real cost → `"ok"`
   
### Frontend (`AgentCard.tsx`)
1. Read `agent.foundry_cost_status`
2. When `"unavailable"`: render `"—"`, update tooltip, suppress `⚠` warning
3. When `"stale"`: render value + clock indicator (`data-testid="foundry-stale"`)
4. When `"ok"` AND foundry=0 AND sdk>0: render existing `⚠` warning

### Frontend (`FoundryCostPanel.tsx`)
1. Detect when `totalSpend === 0` (all entries zero)
2. Replace chart with `<p>cost data unavailable</p>` (or similar)
3. Keep chart when `totalSpend > 0`

---

## Out of Scope for Tests

- SC-1 (real Azure credentials) — manual integration test
- SC-3 (`/api/health` endpoint) — covered by existing `test_routes_health_cost_mgmt.py`
- SC-4 fleet aggregation endpoint — covered by existing `test_routes_fleet.py`
- SC-8 env-var docs — docs review (no test)
- `FleetOverviewBar` cost_mgmt_reachable banner — deferred; requires adding `cost_mgmt_reachable` to `FleetOverviewResponse` model (medium complexity, not critical-path for this small story)
