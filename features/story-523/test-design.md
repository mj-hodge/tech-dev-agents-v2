# STORY-523: Dispatch Enqueue Cross-Story Validator — Test Design

**Phase 7 | Date: 2026-04-22 | Author: Devon**
**Story:** Dispatch enqueue: reject prompts whose STORY-N disagrees with `story_id`
**Scope:** Small | **Phase Path:** 1 → 7 → 8+PR → Done

---

## Overview

`POST /api/dispatch` currently accepts payloads where the free-text `prompt` references
a different `STORY-N` than the structured `story_id` field. On 2026-04-22, STORY-518 and
STORY-520 each burned a full phase-1 SDK session because of this: the agent received a
prompt mentioning the wrong story, couldn't proceed, and had to write a QUESTION.md.

Phase 8 will add:
1. `_prompt_references_other_story(prompt, story_id) -> list[str]` — extracts all
   `STORY-\d+` tokens (case-insensitive) from `prompt`, returns those differing from
   `story_id`.
2. In `enqueue_story`: if mismatches found AND `cross_story_reference=False` → HTTP 422.
3. If mismatches found AND `cross_story_reference=True` → accept (201) + log WARNING.
4. `cross_story_reference: bool = False` field on `DispatchRequest`.

---

## Test Category Table

| # | AC | Test Name | Category | Expected Result | RED Rationale |
|---|----|-----------|----------|----------------|---------------|
| 1 | AC-1 | `test_ac1_mismatch_returns_422` | Route integration | 422 | Validator not implemented; currently returns 201 |
| 2 | AC-2 | `test_ac2_matching_prompt_returns_201` | Route regression | 201 | Happy path works today; PASS (regression guard) |
| 3 | AC-3 | `test_ac3_cross_story_reference_flag_logs_warning` | Route + logging | 201 + WARNING log | No WARNING logged yet (no validator); log assert FAILS |
| 4 | AC-4a | `test_ac4a_regression_story_518` | Regression fixture | 422 | Validator not implemented; currently returns 201 |
| 5 | AC-4b | `test_ac4b_regression_story_520` | Regression fixture | 422 | Validator not implemented; currently returns 201 |
| 6 | AC-5 | `test_ac5_output_variance_different_mismatches` | Output variance | 422 × 2, distinct bodies | Both return 201; 422 assert FAILS |
| 7 | AC-6 | `test_ac6_no_story_token_returns_201` | Route regression | 201 | No STORY-N token; no validator needed; PASS (regression guard) |
| 8 | AC-7 | `test_ac7_self_reference_in_prompt_returns_201` | Route regression | 201 | Self-reference is not a mismatch; PASS (regression guard) |
| 9 | AC-8 | `test_ac8_multiple_mismatches_returns_422_with_all_ids` | Route integration | 422, all IDs in body | Validator not implemented; currently returns 201 |

**Total: 9 tests | 6 RED (FAIL) | 3 GREEN (PASS — regression guards)**

---

## Per-Test Specification

### Test 1: `test_ac1_mismatch_returns_422` (AC-1)

**Purpose:** Core guard — prompt mentioning a different story than `story_id` is rejected.

**Arrange:**
```python
story_id = "STORY-500"
prompt   = "Please do STORY-267 work as part of this dispatch."
# cross_story_reference omitted (defaults to False)
```

**Act:** `POST /api/dispatch` with above payload.

**Assert:**
- `resp.status_code == 422`
- `"STORY-267"` in `resp.text` (error names the mismatching story)
- `"STORY-500"` in `resp.text` (error names the requested story_id)

**RED reason:** Validator not yet implemented; endpoint returns 201.

---

### Test 2: `test_ac2_matching_prompt_returns_201` (AC-2)

**Purpose:** Regression guard — prompt that only mentions its own story_id must not trigger 422.

**Arrange:**
```python
story_id = "STORY-500"
prompt   = "STORY-500: implement the feature spec. Start Phase 7."
```

**Act:** `POST /api/dispatch`.

**Assert:** `resp.status_code == 201`

**RED reason:** N/A — this test is intentionally GREEN (passes before and after Phase 8).

---

### Test 3: `test_ac3_cross_story_reference_flag_logs_warning` (AC-3)

**Purpose:** `cross_story_reference=True` overrides the 422 gate; a WARNING log must be emitted.

**Arrange:**
```python
story_id              = "STORY-500"
prompt                = "Handle STORY-267 work here."
cross_story_reference = True
```

**Act:** `POST /api/dispatch` under `caplog.at_level(WARNING, logger=dispatch_logger)`.

**Assert:**
- `resp.status_code == 201` (opt-in bypass accepted)
- At least one captured WARNING record whose `.message` contains `"STORY-267"`

**RED reason:** No validator exists; no WARNING is logged. Log assertion fails.

---

### Test 4: `test_ac4a_regression_story_518` (AC-4a)

**Purpose:** Exact regression fixture from the 2026-04-22 STORY-518 incident.

**Arrange:**
```python
story_id = "STORY-518"
prompt   = "You are working on STORY-267. Start Phase 1 and produce seed.md ..."
```

**Act:** `POST /api/dispatch`.

**Assert:** `resp.status_code == 422`

**RED reason:** Validator not yet implemented; returns 201.

---

### Test 5: `test_ac4b_regression_story_520` (AC-4b)

**Purpose:** Exact regression fixture from the 2026-04-22 STORY-520 incident.

**Arrange:**
```python
story_id = "STORY-520"
prompt   = "Continue work on STORY-517. The agent should pick up where Phase 6 left off."
```

**Act:** `POST /api/dispatch`.

**Assert:** `resp.status_code == 422`

**RED reason:** Validator not yet implemented; returns 201.

---

### Test 6: `test_ac5_output_variance_different_mismatches` (AC-5)

**Purpose:** Guards against a stub that returns the same error body regardless of input.

**Arrange:**
```python
# Payload A
story_id = "STORY-500"
prompt   = "Please implement STORY-101 features here."

# Payload B
story_id = "STORY-500"
prompt   = "Please implement STORY-999 features here."
```

**Act:** POST both payloads.

**Assert:**
- `resp_a.status_code == 422`
- `resp_b.status_code == 422`
- `resp_a.text != resp_b.text` (bodies differ)
- `"STORY-101"` in `resp_a.text`
- `"STORY-999"` in `resp_b.text`

**RED reason:** Both return 201 today; the first 422 assertion fails.

---

### Test 7: `test_ac6_no_story_token_returns_201` (AC-6)

**Purpose:** Prompt with no `STORY-N` token at all must not trigger a false positive.

**Arrange:**
```python
story_id = "STORY-500"
prompt   = "Implement the enqueue validator feature. Start Phase 7."
```

**Act:** `POST /api/dispatch`.

**Assert:** `resp.status_code == 201`

**RED reason:** N/A — this test is intentionally GREEN (regex returns empty → no guard fires).

---

### Test 8: `test_ac7_self_reference_in_prompt_returns_201` (AC-7)

**Purpose:** A prompt that explicitly mentions its own `story_id` is valid (self-reference allowed).

**Arrange:**
```python
story_id = "STORY-500"
prompt   = "STORY-500: implement STORY-500 validator. See STORY-500 spec."
```

**Act:** `POST /api/dispatch`.

**Assert:** `resp.status_code == 201`

**RED reason:** N/A — this test is intentionally GREEN (only self-references, no mismatch).

---

### Test 9: `test_ac8_multiple_mismatches_returns_422_with_all_ids` (AC-8)

**Purpose:** Prompt referencing two wrong stories → 422 with both IDs listed in error.

**Arrange:**
```python
story_id = "STORY-500"
prompt   = "This work covers STORY-111 and STORY-222. Implement both."
```

**Act:** `POST /api/dispatch`.

**Assert:**
- `resp.status_code == 422`
- `"STORY-111"` in `resp.text`
- `"STORY-222"` in `resp.text`

**RED reason:** Validator not yet implemented; returns 201.

---

## Test File Location

```
tests/ops_console/test_dispatch_enqueue_validator.py
```

### Infrastructure

- Uses `client` + `app` fixtures from `tests/ops_console/conftest.py`
- Injects `DispatchFallbackService` (JSON-backed, no PostgreSQL required) via `inject_mock_services`
- Uses `caplog` (pytest built-in) for log capture in AC-3
- Each test creates its own `tmp_path`-scoped dispatch service (no shared state)

---

## Coverage Summary

| Concern | Tests |
|---------|-------|
| Cross-story mismatch rejected (422) | AC-1, AC-4a, AC-4b, AC-8 |
| Happy path not broken (201) | AC-2, AC-6, AC-7 |
| Opt-in bypass + audit log | AC-3 |
| Error body specificity (anti-stub) | AC-5 |
| Regression: STORY-518 exact fixture | AC-4a |
| Regression: STORY-520 exact fixture | AC-4b |
| Multiple mismatches all listed | AC-8 |
| Case-insensitive regex (STORY vs story) | Covered by AC-4a prompt casing |

---

## RED-State Checklist

- [x] All 9 tests importable (no `ModuleNotFoundError`)
- [x] `pytest --collect-only` shows 9 tests collected
- [x] 6 tests FAIL with `AssertionError` (not `Exception`/`Error`)
- [x] 3 tests PASS (AC-2, AC-6, AC-7 — intentional regression guards)
- [x] No `pytest.mark.xfail` used
- [x] No implementation code in test file
- [x] Tests use `DispatchFallbackService` (JSON) — no PostgreSQL dependency

### Verified RED-state run output

```
FAILED test_ac1_mismatch_returns_422          ← returns 201, expects 422
FAILED test_ac3_cross_story_reference_flag_logs_warning  ← no WARNING logged
FAILED test_ac4a_regression_story_518         ← returns 201, expects 422
FAILED test_ac4b_regression_story_520         ← returns 201, expects 422
FAILED test_ac5_output_variance_different_mismatches  ← both return 201
FAILED test_ac8_multiple_mismatches_returns_422_with_all_ids  ← returns 201

6 failed, 3 passed in 0.85s
```

---

## Phase 8 Implementation Notes

Phase 8 must make all 9 tests GREEN by:

1. Adding `cross_story_reference: bool = False` to `DispatchRequest`
   in `tech_dev_agents/ops_console/models/responses.py`

2. Adding helper in `tech_dev_agents/ops_console/routes/dispatch.py`:
   ```python
   def _prompt_references_other_story(prompt: str, story_id: str) -> list[str]:
       tokens = re.findall(r"STORY-\d+", prompt, re.IGNORECASE)
       return [t.upper() for t in tokens if t.upper() != story_id.upper()]
   ```

3. In `enqueue_story`, before enqueue call:
   ```python
   mismatches = _prompt_references_other_story(body.prompt, body.story_id)
   if mismatches and not body.cross_story_reference:
       raise HTTPException(
           422,
           f"Prompt references {mismatches} but story_id is {body.story_id}. "
           "Set cross_story_reference=true to override."
       )
   if mismatches and body.cross_story_reference:
       logger.warning(
           "cross_story_reference override: story_id=%s but prompt references %s",
           body.story_id, mismatches
       )
   ```

4. AC-3 caplog asserts on `r.message` — ensure the logger call uses `%`-style format
   args so the formatted string appears in `record.getMessage()`.
