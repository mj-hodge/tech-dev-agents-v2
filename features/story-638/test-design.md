# Test Design: STORY-638 — Rework-Aware Branch-Mismatch Guard

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-638 (seed filed as STORY-637 — same work) |
| Scope | small |
| Phase | 7 — Test Design |
| Status | Complete — 10/10 tests GREEN (Phase 8 done) |
| Test File | `tests/deployment/test_phase_runner_branch_mismatch.py` |
| Tests | 10 total (10 PASS) |
| Run command | `python3 -m pytest tests/deployment/test_phase_runner_branch_mismatch.py -v` |

---

## Problem Summary

`_save_partial_work` derived `expected_branch` from `story_id` only. For rework
dispatches, the agent works on the BASE story's branch (e.g. `story-621/story-621`)
but the guard expected the rework story's own branch (`story-626/story-626`),
causing a false `branch_mismatch` event and refusing to commit correct work.

Five reworks failed this way on 2026-04-25. Each completed Phase 8 (rc=0) but
got its commit refused at the guard.

---

## Acceptance Criteria → Test Matrix

| # | Acceptance Criterion | Test | Group | Final State |
|---|---------------------|------|-------|-------------|
| AC1 | `_save_partial_work` accepts `rework_of` kwarg | `test_signature_accepts_rework_of` | B | GREEN |
| AC2 | rework_of set + on rework-target branch → no mismatch | `test_rework_of_set_correct_rework_branch_no_mismatch` | B | GREEN |
| AC3 | rework_of=None + on own branch → no mismatch (regression) | `test_rework_of_none_correct_branch_no_mismatch` | B | GREEN |
| AC4 | rework_of=None + on wrong branch → mismatch fires (regression) | `test_real_cross_story_contamination_still_detected` | B | GREEN |
| AC5 | rework_of set + on wrong branch → mismatch fires | `test_rework_of_set_wrong_branch_mismatch_fires` | B | GREEN |
| AC6 | branch_mismatch event includes rework_of=None | `test_event_includes_rework_of_null_for_normal_dispatch` | C | GREEN |
| AC7 | branch_mismatch event includes rework_of='STORY-621' | `test_event_includes_rework_of_value_for_rework_dispatch` | C | GREEN |
| AC8 | `_derive_expected_branch` exists, rework_of set → rework branch | `test_rework_of_set_uses_rework_number` | A | GREEN |
| AC9 | `_derive_expected_branch` exists, rework_of=None → story branch | `test_rework_of_none_uses_story_id` | A | GREEN |
| AC10 | `_derive_expected_branch` rework_of wins over story_id | `test_rework_of_wins_over_story_id` | A | GREEN |

---

## Test Groups

### Group A — `_derive_expected_branch` Pure Helper

Tests A-01/A-02/A-03: The helper `_derive_expected_branch(story_id, rework_of)` is
exposed on the module and computes the canonical branch correctly for both
rework and normal dispatches.

### Group B — `_save_partial_work` Signature + Rework-Of Behavior

Tests B-01 through B-05: `_save_partial_work` accepts `rework_of` and uses it
when computing the expected branch for the mismatch guard. Regression guards
verify existing behavior is preserved.

### Group C — Event Payload

Tests C-01/C-02: The `branch_mismatch` event includes `rework_of` (None or
the rework base story id) so operators can distinguish contamination from
guard misconfiguration in journalctl/Loki.

---

## Implementation Notes (Phase 8 complete)

### Changes made to `deployment/hermes/sdlc_phase_runner.py`

1. Added `_derive_expected_branch(story_id, rework_of)` helper — computes
   canonical branch from `rework_of` when set, else from `story_id`.
2. Added `rework_of: str | None = None` to `_save_partial_work` signature.
3. Guard uses `_derive_expected_branch` when `rework_of` is set.
4. `branch_mismatch` event includes `rework_of=rework_of`.
5. Added `_current_rework_of` global; SIGTERM handler threads it to
   `_save_partial_work`.
6. All `_save_partial_work` call sites in `_run_phase_sdk` pass `rework_of=rework_of`.

---

## Mocking Strategy

All 10 tests are pure unit tests. No live git repo needed.

| Patched symbol | What it provides |
|---------------|-----------------|
| `sdlc_phase_runner._emit_event` | Captures event names + kwargs for assertion |
| `sdlc_phase_runner._current_branch` | Returns controlled branch string |
| `sdlc_phase_runner.subprocess.run` | Returns dirty files for `--porcelain`; success for add/commit/push |

---

## Final State

```
10 passed in 0.08s
```

All 10 tests GREEN. 0 regressions in related phase-runner tests (29/29 pass
across `test_phase_runner_branch_mismatch.py` + `test_phase_runner_git_fail_fast.py`).
