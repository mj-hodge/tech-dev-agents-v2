# Test Design — STORY-772: Phase Router Must Respect Seed's Declared Phase Path

## Summary

| Field | Value |
|-------|-------|
| Phase | 7 — Test Design |
| Story | STORY-772 |
| Scope | Small |
| Coverage Target | 50% (small scope critical paths) |
| Test Files | `tests/deployment/test_phase_path_parser.py`, `tests/deployment/test_phase_router_seed_respect.py` |
| Total Tests | 20 |
| RED State | All 20 FAIL |
| Run Time | < 2 seconds (all deterministic, no I/O except tmp_path) |

---

## Problem Context

STORY-640 added `parse_seed_phase_path(seed_text: str) -> set[int]` to `sdlc_phase_runner.py`
and wired it into `run_sdlc_phases` to filter which phases run. This fixed integer-only paths.

**Gap — string-suffix phases lost:** The regex `re.findall(r'(\d+)', ...)` extracts only digit
sequences. Phase identifiers like `6b`, `6c`, `8b` become just `6`, `6`, `8` — losing the suffix.
A seed with `Phase Path: 1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done` is parsed as
`{1, 4, 6, 7, 8, 11}` — Phase `6b` (Security Review) and `8b` (Code Review) silently vanish.

**Gap — unordered set:** `set[int]` loses declaration order. The router needs an ordered list
to determine "which phase comes next after the completed set" and to enforce prerequisite ordering
(e.g., `6b` must run before `7`).

**Gap — log format:** AC-8 requires `[DISPATCH] STORY-N: seed path=[...], completed=[...], next=X`.
Current format: `declared=[...] effective=[...]` — missing `next=` and `seed path=` keys.

**Incident (2026-04-30):** 11 stories simultaneously in `needs_info`, all asking:
- "Phase 2 dispatched but my seed path skips Phase 2" (wrong scope-default used)
- "Phase 7 dispatched but Phase 6b not done" (6b lost in parsing)

---

## Implementation Target

STORY-772 Phase 8 must add to `sdlc_phase_runner.py`:

```python
def _extract_phase_path(seed_text: str) -> list[str | int] | None:
    """Parse Phase Path declaration from seed text.

    Returns an ordered list[str | int]:
      - Integer for numeric-only phases: 1, 4, 7 → 1, 4, 7
      - String for string-suffix phases: '6b', '6c', '8b'
      - 'Done' terminator is stripped
    Returns None when:
      - No Phase Path line found (caller uses scope default)
      - Phase Path line has no parseable tokens (malformed)
    Handles: | Phase Path | N → Done | AND **Phase Path:** N → Done AND -> arrows.
    """
```

---

## Test Structure

```
tests/deployment/
├── test_phase_path_parser.py       # 9 parser unit tests (Group A)
└── test_phase_router_seed_respect.py  # 11 router integration tests (Groups B, C, D)
```

---

## Group A — `_extract_phase_path` Parser Unit Tests

**File:** `tests/deployment/test_phase_path_parser.py`
**RED reason:** `_extract_phase_path` does not exist on `sdlc_phase_runner` → `AttributeError`

| Test | Verifies | Expected Result |
|------|----------|-----------------|
| `test_parser_standard_overview_table_format` | Table format `\| Phase Path \| 1 → 7 → 8 → Done \|` | `[1, 7, 8]` |
| `test_parser_missing_field_returns_none` | No Phase Path line in seed | `None` |
| `test_parser_handles_done_terminator_stripped` | 'Done' not in result | `[1, 7, 8]` (no 'Done') |
| `test_parser_string_suffix_phases_6b_6c_8b` | `6b`, `6c`, `8b` preserved as strings | `[1, 4, 6, '6b', '6c', 7, 8, '8b', 11]` |
| `test_parser_ascii_arrow_format_supported` | `->` works same as `→` | `[1, 7, 8]` |
| `test_parser_bold_prose_form` | `**Phase Path:** 1 → 7 → 8 → Done` | `[1, 7, 8]` |
| `test_parser_malformed_no_numbers_returns_none` | "garbage text with no numbers" | `None` |
| `test_parser_preserves_ordered_list` | Returns `list`, not `set`; order preserved | `isinstance(result, list)` and index(4) < index(7) |
| `test_parser_full_medium_large_path` | STORY-008 path with all suffix phases | `[1, 4, 5, 6, '6b', 7, 8, '8b', 11]` |

### Test: `test_parser_string_suffix_phases_6b_6c_8b` (Critical)

**Verifies:** The integer-only regex gap is fixed.

**Arrange:**
```python
seed_text = "| Phase Path | 1 → 4 → 6 → 6b → 6c → 7 → 8 → 8b → 11 → Done |\n"
```

**Act:**
```python
result = fn(seed_text)  # _extract_phase_path
```

**Assert:**
```python
assert '6b' in result  # string, not int 6
assert '6c' in result  # string, not int 6 (duplicate would merge in set)
assert '8b' in result  # string, not int 8
assert result == [1, 4, 6, '6b', '6c', 7, 8, '8b', 11]
```

**Implementation note:** Use `re.findall(r'(\d+[a-z]*)', ...)` to capture digit+optional-suffix tokens,
then cast to `int` if no suffix, `str` otherwise. Strip `'Done'` from the result.

---

## Group B — Router Integration Tests

**File:** `tests/deployment/test_phase_router_seed_respect.py`

### B-01 through B-05: `_extract_phase_path` exists and gives correct results

All call `mod._extract_phase_path` → `AttributeError` → RED

| Test | Verifies |
|------|----------|
| `test_uses_seed_path_when_present` | Function exists, returns `[1, 7, 8]` for simple path |
| `test_skips_non_seed_phase` | Phase 2 absent from `1→4→6→7→8` path |
| `test_no_seed_path_fallback_to_scope_default` | Returns None when no Phase Path line |
| `test_seed_path_includes_6b_for_medium_story` | `'6b'` in result, before `7` in ordering |
| `test_seed_path_excludes_phase_2_for_medium_story` | `2 not in result` for `1→4→6→7→8` |

### B-06: Log format matches AC-8

**RED reason:** Current log: `declared=[...] effective=[...]`. Expected: `seed path=[...] ... next=X`.

**Arrange:**
```python
# Run run_sdlc_phases with medium seed containing Phase Path
_run_phases_with_mocks(tmp_path, "| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |\n")
```

**Act:**
```python
captured = capsys.readouterr()
stdout = captured.out
```

**Assert:**
```python
assert "seed path=" in stdout   # AC-8 key name
assert "next=" in stdout         # AC-8 — must log which phase comes next
```

---

## Group C — Incident Scenario Fixtures

**File:** `tests/deployment/test_phase_router_seed_respect.py`
**RED reason:** All call `mod._extract_phase_path` → `AttributeError` → RED

### C-01: STORY-008 Pattern — Phase 2 not in seed path

| Field | Value |
|-------|-------|
| Incident | Phase 2 dispatched despite Phase Path `1→4→5→6→6b→7→8→8b→11→Done` |
| Seed Path | `| Phase Path | 1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done |` |
| Assert | `2 not in int_phases`, `'6b' in result`, `'8b' in result` |
| Expected | `[1, 4, 5, 6, '6b', 7, 8, '8b', 11]` |

### C-02: STORY-011 Pattern — Phase 6b must precede Phase 7

| Field | Value |
|-------|-------|
| Incident | Phase 7 dispatched before Phase 6b completed |
| Seed Path | `| Phase Path | 1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done |` |
| Assert | `'6b' in result`, `result.index('6b') < result.index(7)` |

### C-03: STORY-015 Pattern — Phase 6 before Phase 7

| Field | Value |
|-------|-------|
| Incident | Phase 7 dispatched without Phase 6 deliverable |
| Seed Path | `| Phase Path | 1 → 4 → 5 → 6 → 6b → 6c → 7 → 8 → 8b → 11 → Done |` |
| Assert | `result.index(6) < result.index(7)`, `'6b' in result`, `'6c' in result` |

---

## Group D — Error Handling

**File:** `tests/deployment/test_phase_router_seed_respect.py`
**RED reason:** All call `mod._extract_phase_path` → `AttributeError` → RED

| Test | Input | Expected | Rationale |
|------|-------|----------|-----------|
| `test_missing_seed_file_returns_none` | Empty string (`""`) | `None` | No seed = fallback to scope default (AC-11) |
| `test_malformed_seed_arrows_only_returns_none` | `\| Phase Path \| → → → Done \|` | `None` | No tokens = malformed (AC-11) |

---

## Defensive Gates Applied

| Gate | Applied? | Notes |
|------|----------|-------|
| Gate 1: Null/None boundaries | ✓ | None returns for missing/malformed seed |
| Gate 2a: External API isolation | N/A | No external APIs in this story |
| Gate 4: Input validation | ✓ | Malformed seed input tested |
| Gate 10: Error observability | Partial | Phase 8 must add WARN log for malformed seeds |
| Gate 11: Fixture compilation | ✓ | All fixtures use actual string/list types, no dataclasses |

---

## Output-Variance Tests

The `_extract_phase_path` function transforms input text into a list. The following tests cover
two meaningfully different inputs producing two different outputs:

- `test_parser_standard_overview_table_format` → `[1, 7, 8]`
- `test_parser_full_medium_large_path` → `[1, 4, 5, 6, '6b', 7, 8, '8b', 11]`

These two inputs produce different outputs in length, content, and types — satisfying the
Output-Variance Gate requirement.

---

## RED State Summary

```
tests/deployment/test_phase_path_parser.py::TestExtractPhasePath (9 tests)
  ALL FAIL: AttributeError: module 'sdlc_phase_runner' has no attribute '_extract_phase_path'

tests/deployment/test_phase_router_seed_respect.py::TestRouterUsesSeedPath (6 tests)
  B-01..B-05: AttributeError: module has no attribute '_extract_phase_path'
  B-06: AssertionError: Log must contain 'seed path=' (current: 'declared=')

tests/deployment/test_phase_router_seed_respect.py::TestIncidentScenarios (3 tests)
  ALL FAIL: AttributeError: module has no attribute '_extract_phase_path'

tests/deployment/test_phase_router_seed_respect.py::TestErrorHandling (2 tests)
  ALL FAIL: AttributeError: module has no attribute '_extract_phase_path'

Total: 20 FAIL (0 PASS, 0 ERROR, 0 SKIP)
```

---

## Phase 8 Implementation Checklist

The Phase 8 implementer must make all 20 tests GREEN by:

- [ ] Add `_extract_phase_path(seed_text: str) -> list[str | int] | None` to `sdlc_phase_runner.py`
  - Token regex: `re.findall(r'(\d+[a-z]*)', text)` — captures `6b`, `8b` correctly
  - Cast to `int` if all digits, else keep as `str`
  - Filter out `'Done'` from result
  - Return `None` if no tokens found
  - Handle table format `| Phase Path | ... |` and prose format `**Phase Path:** ...`
  - Handle `→` (Unicode) and `->` (ASCII)

- [ ] Update `run_sdlc_phases` routing to use `_extract_phase_path` instead of `parse_seed_phase_path`
  - Replace `set[int]` with `list[str | int]` for declared path
  - Maintain order for prerequisite enforcement

- [ ] Add Phase 6b, 6c, 8b entries to PHASES_MEDIUM and PHASES_LARGE (AC-6)
  - These are currently absent, causing B-04/C-02 to fail after `_extract_phase_path` is added

- [ ] Update routing log format to match AC-8:
  - `[DISPATCH] STORY-N: seed path=[...], completed=[...], next=X`

- [ ] Keep `parse_seed_phase_path` as backward-compat alias or update callers

- [ ] Verify `pytest tests/deployment/test_dispatch_poller_725_seed_path.py` also goes GREEN
  (STORY-725 tests expect `dispatch_poller.parse_seed_phase_path` — Phase 8 should add it there too)

---

## Scope Notes

- **No DB changes:** No migration needed — this is pure routing logic.
- **No frontend:** `seed.md` says `Frontend: false`.
- **No external API calls:** Parser is pure stdlib regex.
- **Backward compat:** Seeds without Phase Path must still work (B-03 tests this).
- **STORY-640 preserved:** `parse_seed_phase_path` (STORY-640) may be kept as a backward-compat
  alias or updated in place. Either approach is acceptable.
