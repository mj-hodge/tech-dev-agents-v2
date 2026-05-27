# Seed: STORY-637 — Make Phase 8 branch-mismatch guard rework-aware

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | Phase 8 branch-mismatch guard honors `rework_of` instead of always deriving expected_branch from `story_id` |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-637/rework-aware-branch-mismatch-guard` |
| Status | Seed written 2026-04-25 ~20:45 UTC |
| Priority | 70 — blocking 5 active rework stories from completing |

---

## 1. Idea / Trigger

On 2026-04-25 19:50–20:35 UTC, five rework dispatches all failed with the same pattern:

| Story | rework_of | Resumed on (correctly) | Guard expected (incorrectly) |
|---|---|---|---|
| STORY-626 | STORY-621 | `story-621/story-621` | `story-626/story-626` |
| STORY-629 | STORY-589 | `story-589/...` | `story-629/...` |
| STORY-631 | STORY-302 | `story-302/...` | `story-631/...` |
| STORY-632 | STORY-551 | `story-551/...` | `story-632/...` |
| STORY-635 | STORY-565 | `story-565/...` | `story-635/...` |

Each story journal shows the same flow:
1. `branch_resume` event fires correctly to the rework target's branch (e.g., `story-621/story-621` for STORY-626) — `_ensure_branch` honored `rework_of`.
2. Phase 8 (Implementation) runs successfully (`rc=0`, real work happens).
3. At Phase 8 end, the branch-mismatch guard fires:
   ```
   {"event": "branch_mismatch", "story_id": "STORY-635", ..., 
    "expected_branch": "story-635/story-635", "current_branch": "story-565/story-565"}
   ```
4. The guard refuses to commit, treats it as cross-story contamination, marks the story FAILED, and dispatches a Teams alert.

The bug is that **`expected_branch` is derived from the dispatch row's `story_id`** instead of from `rework_of` when set. The original PR #116 plumbing made `_ensure_branch` rework-aware but missed this terminal guard.

## 2. Problem Statement

- **Real production blocker.** 5 reworks failed today exclusively to this bug. Each one was actual work the agent completed correctly but couldn't commit.
- **Asymmetric `rework_of` handling.** `_ensure_branch` honors `rework_of`. The Phase 8 guard doesn't. Either both should, or neither — and "both" is correct.
- **No regression test exists** for "rework_of-set Phase 8 should accept the rework target's branch as the expected branch." That's why the bug went unnoticed when PR #116 shipped.

## 3. Scope Classification

**Small.** Two files:
- `deployment/hermes/sdlc_phase_runner.py` — the guard that needs to be rework-aware
- `tests/deployment/test_phase_runner_branch_mismatch.py` (new file) — RED test then GREEN

No schema, no API, no cross-repo. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`deployment/hermes/sdlc_phase_runner.py`** — Around line 1849:
  ```python
  branch_name = f"story-{_story_num}/{story_id.lower()}"
  ```
  This is one location. There may also be a similar derivation in the Phase 8 end check (`branch_mismatch` event emitter). Read the file to find both occurrences and decide whether one shared helper is the right shape OR each call site updates independently.

  The fix: when `rework_of` is set on the run, derive `branch_name` (a.k.a. `expected_branch`) from `rework_of`, not from `story_id`. Same formula `story-<rework_num>/<rework_id.lower()>`, matching the formula in `_ensure_branch`.

- **`run_sdlc_phases` signature** already accepts `rework_of` (per PR #116). The guard code just needs to use it.

### Files NOT to touch

- `deployment/hermes/dispatch_poller.py` — irrelevant; just passes `rework_of` through
- `routes/dispatch.py`, `models/responses.py`, DB schema — `rework_of` plumbing already correct end-to-end

## 5. The Fix

### Change 1: derive expected_branch from rework_of when set

```python
# Inside the Phase 8 branch-mismatch check (find the existing derivation):
def _derive_expected_branch(story_id: str, rework_of: str | None) -> str:
    """Return the canonical branch for a story, accounting for reworks.
    
    For a rework dispatch (rework_of set), the agent works on the ORIGINAL
    story's branch (not a fresh story-N/N branch). The Phase 8 guard must
    match that, otherwise it cries 'mismatch' on perfectly correct work.
    """
    target_id = rework_of if rework_of else story_id
    num_match = re.match(r'STORY-(\d+)', target_id, re.IGNORECASE)
    if not num_match:
        # Fall back to the existing formula
        return f"story-{story_id.lower()}/{story_id.lower()}"
    return f"story-{num_match.group(1)}/{target_id.lower()}"
```

Use the helper at every existing `f"story-{_story_num}/{story_id.lower()}"` call site that's used for branch verification (NOT `_ensure_branch`'s create path — that already has its own rework-aware logic).

### Change 2: log the decision

When the guard fires `branch_mismatch`, include the `rework_of` value (or `null`) in the event payload. Future debugging gets the full picture:

```python
_emit_event(
    "branch_mismatch",
    story_id=story_id,
    expected_branch=expected,
    current_branch=current,
    rework_of=rework_of,  # NEW
    dirty_files=len(dirty),
)
```

## 6. Out of Scope

- Refactoring `_ensure_branch` itself — already correct
- Changing the rework dispatch contract (Morris-side prompt format, `rework_of` field semantics) — also correct
- Backfilling the 5 stories that already failed — the requeue-failed skill (or manual SQL) handles those after this lands

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/deployment/test_phase_runner_branch_mismatch.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| Rework-of set → expected_branch matches rework target | Mock `run_sdlc_phases` invoked with `story_id="STORY-626"`, `rework_of="STORY-621"`. Workdir on branch `story-621/story-621`. | Phase 8 end check passes; no `branch_mismatch` event emitted; commit/push proceed. |
| Rework-of unset → expected_branch matches story_id (regression) | Mock `run_sdlc_phases` with `story_id="STORY-700"`, `rework_of=None`. Workdir on branch `story-700/story-700`. | Existing behavior preserved; no mismatch. |
| Real cross-story contamination still detected | `story_id="STORY-700"`, `rework_of=None`, workdir on `story-621/story-621` (wrong). | `branch_mismatch` fires; commit refused. |
| Rework-of set, agent on wrong branch (not the rework target either) | `story_id="STORY-626"`, `rework_of="STORY-621"`, workdir on `story-999/...`. | `branch_mismatch` fires; commit refused. |
| Event payload includes rework_of | Trigger any mismatch | Event JSON has `rework_of` key (null when unset) |

All tests are unit-level with mocked subprocess calls. No live agent VM required.

## Validation

After Phase 8 lands + push-code.sh deploys to all 5 VMs:

1. Re-run the 5 failed reworks (STORY-626/629/631/632/635) — they should now complete Phase 8 and create PRs without `branch_mismatch` errors.
2. Dispatch a non-rework story (e.g., a fresh small task) and verify the existing branch-mismatch detection still works (would reject if the agent ended up on the wrong branch).
3. journalctl shows `branch_mismatch` events including the `rework_of` field.

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-637/rework-aware-branch-mismatch-guard`
- Scope: small
- Priority: 70 (5 stuck stories blocked)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20 min
- Implementing agent should: (a) read `_ensure_branch` to understand the existing rework-aware pattern, (b) find every place the current code derives an expected branch for verification (grep `story-` formula + `_story_num` references), (c) introduce a single helper or update the call sites individually — implementer's call, (d) cover all 5 test cases above before Phase 8.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
