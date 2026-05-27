# STORY-769 — Dependency-Aware Dispatch: Test Design

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-769 |
| Scope | Small |
| Coverage Target | 50% (critical paths) |
| Test Files | 2 (parser + claim gate) |
| Total Tests | 23 |
| RED State | 23 FAIL, 0 PASS |

## Test Structure

```
tests/ops_console/
├── test_dispatch_dependency_parser.py     # Group A: 10 parser unit tests
└── test_dispatch_claim_dependency_gate.py # Groups B–F: 13 claim gate + queue + edge + integration tests
```

## Test Groups

### Group A — Parser: `_extract_dependencies(prompt)`

**Purpose:** Verify the regex parser correctly extracts STORY-IDs from `DO NOT START until STORY-N` markers.
**Level:** Unit (pure Python, no mocks)
**Location:** `tests/ops_console/test_dispatch_dependency_parser.py`

| Test | What It Verifies | SC/AC |
|------|------------------|-------|
| `test_no_marker_returns_empty_list` | Prompt without marker → `[]` | SC-1 |
| `test_single_marker_returns_one_story_id` | One marker → `['STORY-632']` | SC-1 |
| `test_multiple_markers_returns_deduped_list` | Two markers + duplicate → deduped list | AC-8 |
| `test_case_insensitive_do_not_start` | Lowercase 'do not start' recognized | AC-7 |
| `test_mixed_case_until` | Title case 'Do Not Start Until' recognized | AC-7 |
| `test_whitespace_variants` | Extra spaces between words tolerated | AC-7 |
| `test_marker_embedded_in_longer_prompt` | Marker buried in multi-line prompt found | AC-7 |
| `test_empty_prompt_returns_empty_list` | Empty string → `[]` | Boundary |
| `test_story_reference_without_marker_not_matched` | Bare STORY-500 mention not matched | SC-1 |
| `test_returns_list_type` | Always returns `list`, not set/tuple | SC-1 |

### Group B — Claim Gate: Blocked Candidates Skipped

**Purpose:** Verify `/dispatch/next` skips stories whose dependencies are unmet.
**Level:** Integration (mocked DB service + FastAPI TestClient)
**Location:** `tests/ops_console/test_dispatch_claim_dependency_gate.py`

| Test | What It Verifies | SC/AC |
|------|------------------|-------|
| `test_blocked_candidate_skipped` | Story with unmet dep → 204 | SC-2, SC-3 |
| `test_no_marker_story_claimed_normally` | No-dep story → 200 (unchanged behavior) | SC-8 |
| `test_satisfied_dependency_allows_claim` | Completed dep with PR → 200 | SC-2 |
| `test_skip_logged_with_story_and_dep_id` | Skip log includes story + dep IDs | SC-3, AC-6 |
| `test_blocked_story_remains_pending` | No state-changing methods called on block | SC-3 |

### Group C — Queue API: `dependencies_unmet` Field

**Purpose:** Verify `GET /api/dispatch/queue` returns `dependencies_unmet` on pending items.
**Level:** Integration (mocked DB service + FastAPI TestClient)

| Test | What It Verifies | SC/AC |
|------|------------------|-------|
| `test_dependencies_unmet_in_queue_listing` | Pending item with marker has `dependencies_unmet: ["STORY-899"]` | SC-6, AC-4 |
| `test_no_marker_has_empty_dependencies_unmet` | No-dep item has `dependencies_unmet: []` | SC-6 |

### Group D — Edge Cases

**Purpose:** Boundary conditions and fail-safe behavior.
**Level:** Integration

| Test | What It Verifies | SC/AC |
|------|------------------|-------|
| `test_unknown_dependency_blocks` | Dep not in dispatch_items → blocked | SC-5 |
| `test_default_fail_safe_on_db_error` | DB error on dep lookup → don't claim (fail-safe) | AC-11 |
| `test_completed_without_pr_number_still_blocks` | completed + pr_number=NULL → blocked | SC-2 |
| `test_multiple_deps_all_must_be_satisfied` | 2 deps, only 1 satisfied → blocked | SC-2 |

### Group E — Integration: Unblocking

**Purpose:** Story becomes claimable when dependency completes.
**Level:** Integration

| Test | What It Verifies | SC/AC |
|------|------------------|-------|
| `test_unblocks_on_dep_completion` | Dep incomplete → 204; dep complete → 200 | SC-7 |

### Group F — Output Variance (Stub Detection)

**Purpose:** Different inputs produce different outputs.
**Level:** Unit

| Test | What It Verifies | SC/AC |
|------|------------------|-------|
| `test_parser_output_varies_with_input` | Two different prompts → two different results | Gate |

## RED State Verification

```
$ python3 -m pytest tests/ops_console/test_dispatch_dependency_parser.py \
                     tests/ops_console/test_dispatch_claim_dependency_gate.py -v
23 failed in 3.97s
```

**Parser tests:** Fail with `NotImplementedError` — `_extract_dependencies()` is a stub.
**Route tests:** Fail with `401` (auth not wired for dep check) or `NotImplementedError`.
**No import errors.** All tests import cleanly.

## Zero Regressions

```
$ python3 -m pytest tests/ops_console/test_dispatch_service.py \
    tests/ops_console/test_dispatch_db_service.py \
    tests/ops_console/test_dispatch_release.py \
    tests/ops_console/test_dispatch_cancel.py \
    tests/ops_console/test_dispatch_enum_usage.py \
    tests/ops_console/test_dispatch_contract.py -q
61 passed, 26 skipped in 0.76s
```

## Stubs Added (Minimal, Phase 8 Replaces)

1. `_extract_dependencies()` in `dispatch_db_service.py` — raises `NotImplementedError`
2. `dependencies_unmet: list[str]` field on `DispatchItem` Pydantic model — defaults to `[]`

## SC-to-Test Mapping

| SC | Tests |
|----|-------|
| SC-1 | A1–A10 (parser) |
| SC-2 | B1, B3, D3, D4 (claim gate + satisfied definition) |
| SC-3 | B1, B4, B5 (no state change + logging) |
| SC-4 | Covered by design: 1 indexed query per dep via `db_svc.get()` |
| SC-5 | D1 (unknown dep blocks) |
| SC-6 | C1, C2 (queue listing field) |
| SC-7 | E1 (integration unblock) |
| SC-8 | B2 (no-marker story unchanged) + existing suite GREEN |

## Defensive Gate Checklist

- [x] Boundary condition tests: empty prompt, no marker, multiple markers (A3, A8)
- [x] Null/None: unknown dep returns None → blocked (D1)
- [x] DB error fail-safe: dep lookup exception → don't claim (D2, AC-11)
- [x] Output-variance: two different inputs → two different results (F1)
- [x] No import errors on test collection
- [x] Every user-facing endpoint involved has tests (/dispatch/next, /dispatch/queue)
