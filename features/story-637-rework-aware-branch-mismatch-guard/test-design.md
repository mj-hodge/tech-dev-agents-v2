# Test Design: STORY-637 — Rework-Aware Branch Mismatch Guard

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 50% of affected functions |
| Test file | `tests/deployment/test_phase_runner_branch_mismatch.py` |
| Tests | 12 total (11 RED, 1 PASS) |
| Level | Unit (mocked subprocess, no live git) |

## RED State Confirmation

```
11 failed, 1 passed in 0.18s
```

All 11 failures are `TypeError: got an unexpected keyword argument 'rework_of'` — the functions don't accept the parameter yet. This is the correct RED reason: the fix hasn't been applied.

The 1 PASS (`test_rework_of_not_passed_returns_story_id_branch`) confirms current non-rework behavior works. It will stay green through Phase 8 (regression guard).

## Test Groups

### Group A: `_expected_story_branch` honours `rework_of` (3 tests)

| Test | What It Verifies |
|------|------------------|
| `test_rework_of_set_returns_rework_target_branch` | `rework_of="STORY-621"` → `story-621/story-621` (not `story-626/...`) |
| `test_rework_of_none_returns_story_id_branch` | `rework_of=None` → falls back to `story_id` (regression) |
| `test_rework_of_not_passed_returns_story_id_branch` | Omitting `rework_of` kwarg → same as before (backward compat) |

### Group B: `_expected_branch_for_story` honours `rework_of` (2 tests)

| Test | What It Verifies |
|------|------------------|
| `test_rework_of_set_uses_rework_target_branch` | Higher-level resolver with seed lookup also honours `rework_of` |
| `test_rework_of_none_uses_story_id_branch` | `rework_of=None` → uses `story_id` (regression) |

### Group C: `_save_partial_work` threads `rework_of` (4 tests)

| Test | What It Verifies |
|------|------------------|
| `test_rework_dispatch_on_correct_branch_no_mismatch` | Rework on rework-target branch → no `branch_mismatch` event |
| `test_rework_dispatch_on_wrong_branch_triggers_mismatch` | Rework on unrelated branch → `branch_mismatch` fires |
| `test_non_rework_on_correct_branch_no_mismatch` | Non-rework on story branch → no mismatch (regression) |
| `test_non_rework_on_wrong_branch_triggers_mismatch` | Non-rework on wrong branch → mismatch fires (regression) |

### Group D: Event payload includes `rework_of` (2 tests)

| Test | What It Verifies |
|------|------------------|
| `test_mismatch_event_includes_rework_of_when_set` | `branch_mismatch` event has `rework_of="STORY-621"` |
| `test_mismatch_event_includes_rework_of_null_when_unset` | `branch_mismatch` event has `rework_of=None` for consistent schema |

### Group E: Output variance — stub detection (1 test)

| Test | What It Verifies |
|------|------------------|
| `test_different_rework_targets_yield_different_branches` | Two different `rework_of` values → two different expected branches |

## Implementation Notes for Phase 8

The fix requires three changes, all in `deployment/hermes/sdlc_phase_runner.py`:

1. **`_expected_story_branch(story_id, rework_of=None)`** — add `rework_of` param; when set, derive branch from `rework_of` instead of `story_id`.
2. **`_expected_branch_for_story(workdir, story_id, rework_of=None)`** — thread `rework_of` through to `_expected_story_branch`.
3. **`_save_partial_work(workdir, story_id, phase_num, phase_name, rework_of=None)`** — accept `rework_of`, pass it to `_expected_branch_for_story`. Also add `rework_of=rework_of` to the `_emit_event("branch_mismatch", ...)` call.
4. **All call sites of `_save_partial_work`** — thread `rework_of` where available (in `run_sdlc_phases` loop and SIGTERM handler).
5. **Line ~1868** — the `branch_name` derivation in `run_sdlc_phases` should also use `rework_of`.

## Checklist

- [x] Every test name clearly states what it verifies
- [x] Arrange/Act/Assert structure in every test
- [x] Output-variance test included (Group E)
- [x] Regression tests for non-rework path (Groups A–C)
- [x] Event payload schema test (Group D)
- [x] All 12 tests collected clean (`--collect-only`)
- [x] 11 RED (TypeError — correct reason), 1 PASS (regression guard)
- [x] No import errors, no syntax errors
- [x] Tests mock only external deps (subprocess, event emitter) — not the functions under test
