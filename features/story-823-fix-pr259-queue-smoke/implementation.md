# STORY-823: Phase 8 Implementation Report

## Summary

Fix story for PR #259 (STORY-096 Queue Smoke Tests). All three Morris review blockers
resolved: conflict markers removed from `.project`, global `sys.path` hack removed from
`tests/conftest.py`, and `work_queue` import localised to `tests/test_queue_smoke.py`.

## Changes

### F01 — `.project` Conflict Markers Removed

**Problem:** `story-096/blocked-question` had `<<<<<<< HEAD` committed into `.project`
(artifact of an earlier bad rebase), plus 20+ orphaned multi-worker rows that should not
have been on this branch.

**Fix:** Reset `.project` to `origin/main` via `git checkout main -- .project`, then
appended only the two rows that belong to this branch:

```
| STORY-096 | derrick | small | 8 | phase 8 complete — 5 smoke tests GREEN, PR #259 | story-096/blocked-question |
| STORY-823 | hermes  | small | 8 | phase 8 complete — 9/9 tests GREEN ...         | story-096/blocked-question |
```

`git diff origin/main -- .project` now shows exactly two `+|` lines in the multi-worker
table and zero changes to top-level Phase Routing fields.

### F02 — Global `sys.path` Removed from `tests/conftest.py`

**Problem:** `tests/conftest.py` had a module-level `sys.path.insert(0, scripts/)` block
that polluted the import namespace for every test in the suite.

**Fix:** Removed `import sys`, `from pathlib import Path`, `_SCRIPTS_DIR`, and
`sys.path.insert` block from `tests/conftest.py`. The `pytest_configure` function (smoke
marker registration) is preserved.

Result: `tests/conftest.py` is now identical to `origin/main` — the diff is empty, which
is the correct outcome (the change in PR #259 is fully reverted for this file).

### F03 — `work_queue` Import Localised to `tests/test_queue_smoke.py`

**Problem:** After removing the global sys.path, `import work_queue` in
`tests/test_queue_smoke.py` would fail because `scripts/` would no longer be on the path.

**Fix:** Added a localised path block immediately before `import work_queue`:

```python
import sys
from pathlib import Path

# scripts/ is not a package — add it to sys.path so work_queue can be imported.
# This is localised here rather than in tests/conftest.py to avoid polluting the
# global import namespace for all tests in the suite.
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
```

### F04 — `test-design.md` / `implementation.md` Consistency

**Problem:** `test-design.md` Phase Gate Note used "RED → GREEN cycle does not apply"
language while `implementation.md` reported GREEN — superficially contradictory.

**Fix:** Updated the Phase Gate Note to explicitly state that GREEN is the expected
end-state for a test-only story, matching the `implementation.md` language.

## Test Results

| Suite | Command | Result |
|-------|---------|--------|
| STORY-823 verification | `pytest tests/test_story823_pr259_fixes.py -v` | 6/6 PASSED |
| Queue smoke tests | `pytest tests/test_queue_smoke.py -v` | 5/5 PASSED |
| Combined | `pytest tests/test_story823_pr259_fixes.py tests/test_queue_smoke.py -v` | 11/11 PASSED |
| work_queue regression | `pytest tests/test_work_queue.py tests/deployment/test_stale_work_queue.py -q` | 31/31 PASSED |

## Acceptance Criteria Verification

| SC | Check | Result |
|----|-------|--------|
| SC-2 | `git diff main -- .project` shows only `+\|` rows | ✓ Two rows appended, no Phase Routing changes |
| SC-3 | `grep -c sys.path tests/conftest.py` returns 0 | ✓ File matches main (no sys.path) |
| SC-3 | `work_queue` importable via local mechanism | ✓ Local sys.path.insert in test_queue_smoke.py |
| SC-4 | test-design.md / implementation.md consistent | ✓ Phase Gate Note aligned |
| SC-5 | 5 smoke tests GREEN | ✓ 5/5 PASSED |
| SC-6 | Zero new regressions | ✓ 31/31 work_queue tests + 11/11 target tests GREEN |

## Acceptance Diff

Files changed vs `origin/main`:

| File | Change |
|------|--------|
| `tests/test_queue_smoke.py` | Localised sys.path.insert added before import work_queue |
| `tests/conftest.py` | Restored to main (no diff — sys.path removal is a net revert) |
| `features/story-096-queue-smoke/test-design.md` | Phase Gate Note consistency fix |
| `.project` | Append-only: two multi-worker rows added |
| `features/story-823-fix-pr259-queue-smoke/seed.md` | Phase 1 artifact |
| `features/story-823-fix-pr259-queue-smoke/test-design.md` | Phase 7 artifact |
| `tests/test_story823_pr259_fixes.py` | Phase 7 verification tests |
