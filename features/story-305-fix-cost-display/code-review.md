# Code Review — STORY-305: Fix Dashboard Cost Display

**Phase:** 8b — Code Review
**Story:** STORY-305 — Fix dashboard cost display: foundry_cost_usd always zero
**Reviewer:** Code Review Agent
**Date:** 2026-04-16
**Verdict:** APPROVED

---

## Summary

The fix is minimal and well-contained. Three files changed: the data models, the cost service, and the route handler. No new dependencies. No schema changes. All 45 tests GREEN.

---

## models.py

**Change:** Two new optional fields (`foundry_cost_usd: float = 0.0`, `openai_cost_usd: float = 0.0`) added to `DailyCost` and `CostToday`.

**Observations:**
- Default values of `0.0` ensure backward compatibility — existing code that constructs these dataclasses without the new fields continues to work without modification.
- Field ordering places the new fields after existing fields, avoiding positional argument breakage for any callers using positional construction.
- The field names are consistent with the existing `azure_cost_usd` naming convention.

**No issues.**

---

## cost_service.py

**Change 1 — `_classify_cost()` static method**

- Clean static method with a clear docstring noting the interim heuristic and the TODO to revisit with `meterCategory`.
- Logic is straightforward: three conditions, one return per branch. No nested conditionals.
- `.lower()` applied before comparisons — case-insensitive correctly.
- `resource_group` parameter accepts `None` via `(resource_group or "")` guard — safe against missing API fields.
- The TODO comment is appropriate: it documents the limitation without blocking the fix.

**Change 2 — `_parse_cost_response()` update**

- `setattr(record, bucket, getattr(record, bucket) + item["cost"])` is a readable dynamic field update.
- Alternative: explicit `if/elif` branching. The `setattr`/`getattr` approach is marginally more compact; either style is acceptable.
- `item.get("resourceGroup", "")` correctly handles API responses where the field may be absent.

**Change 3 — `aggregate()` update**

- New sum lines follow the existing pattern exactly — consistent style.
- Sum correctness: `foundry + openai + azure` will equal the old `azure_cost_usd` total only if all previously unclassified items now correctly classify. This is validated by T48.

**No issues.**

---

## cost_routes.py

**Change:** Added `foundry_cost_usd` and `openai_cost_usd` to the response dict in both `GET /api/costs/today` and `GET /api/costs/daily`.

- Serialisation uses `dataclasses.asdict()` — the new fields are included automatically. The explicit dict construction in the route handler only needed two new key references.
- No change to HTTP status codes or error handling paths.

**No issues.**

---

## Test Quality

- `TestCostClassification` covers boundary cases: `oai-*` prefix, `foundry`/`aiservices` substring, unknown RG, empty string, case sensitivity.
- Updated fixture expectations (T33, T48, T49) correctly exercise the full parse-and-aggregate pipeline, not just the classifier in isolation.
- No test is duplicated from the new unit tests — each tests a distinct code path.

---

## Checklist

| Item | Status |
|------|--------|
| Logic is correct and well-tested | PASS |
| Backward-compatible model changes | PASS |
| No new dependencies | PASS |
| Interim heuristic is documented with TODO | PASS |
| Consistent naming and style | PASS |
| Route handler exposes new fields | PASS |
| All 45 tests GREEN | PASS |

---

## Verdict

**APPROVED.** Clean, minimal fix. The interim classification heuristic is clearly marked for future improvement. No blocking issues.
