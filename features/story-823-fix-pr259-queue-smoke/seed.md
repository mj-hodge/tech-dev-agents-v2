# STORY-823: Fix PR #259 (STORY-096 Queue Smoke Tests)

## Classification

| Field | Value |
|-------|-------|
| Story ID | STORY-823 |
| Scope | Small |
| Phase Path | 1 → 7 → 8 → Done |
| Advance Category | auto |
| Category | Fix (PR rework) |
| Frontend | false |

## Problem Statement

PR #259 (STORY-096: queue smoke tests for pre-deploy gate) on branch `story-096/blocked-question` has review findings from Morris that block merge. The PR is in CONFLICTING mergeable state due to `.project` divergence from main (20+ merges behind). Three issues must be resolved: (1) the `.project` diff must only append a row to the multi-worker Story Status table — it must NOT overwrite top-level Phase Routing metadata (Active Story, Current Phase, etc.) which would clobber the currently active story; (2) the `tests/conftest.py` change adds a `sys.path.insert(0, scripts/)` hack at module level that pollutes the import namespace for all tests — this should be replaced with a localized conftest.py in the test file or a proper package structure; (3) the `test-design.md` Phase Gate Note says "RED → GREEN cycle does not apply" for this test-only story yet `implementation.md` reports GREEN status, creating a documentation inconsistency that confuses phase tracking.

## Target Branch

story-096/blocked-question

## Desired Outcome

PR #259 passes Morris review and CI, is mergeable, and follows the same `.project` append-only pattern used by sibling PRs #258 and #260.

## Success Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| SC-1 | Branch rebased on current main with no merge conflicts | `gh pr view 259 --json mergeable` shows `MERGEABLE` |
| SC-2 | `.project` diff only adds a row to the Story Status (Multi-Worker) table — top-level Phase Routing fields unchanged from main | `git diff main -- .project` shows only a `+\|` line in the multi-worker table |
| SC-3 | `sys.path.insert` hack removed from `tests/conftest.py` — `scripts/` import handled locally in `tests/test_queue_smoke.py` or via a `scripts/conftest.py` | `grep -c sys.path tests/conftest.py` returns 0 |
| SC-4 | `test-design.md` and `implementation.md` are consistent on phase status | No contradictory RED/GREEN language between the two files |
| SC-5 | All 5 smoke tests still pass after fixes | `pytest tests/test_queue_smoke.py -v` — 5/5 GREEN |
| SC-6 | Zero regressions in existing test suite | `pytest tests/ -x --timeout=60` exits 0 |

## Scope Boundaries

### In Scope
- Rebase `story-096/blocked-question` onto current `main`
- Fix `.project` to append-only pattern (match PRs #258, #260)
- Remove `sys.path.insert` from `tests/conftest.py`, use local import mechanism
- Align `test-design.md` / `implementation.md` consistency
- Address Morris review MEDIUM findings (T03 PID comment, docstring accuracy)

### Out of Scope
- Adding new smoke tests beyond the existing 5
- Changing production queue code
- Addressing BLOCKER finding (DispatchFallbackService.claim `repo` kwarg) — already fixed in the PR diff

## Test Criteria

1. `pytest tests/test_queue_smoke.py -v` — 5/5 tests pass
2. `pytest -m smoke --collect-only` — 6+ smoke tests collected
3. `git diff main -- .project` — only multi-worker table row addition, no Phase Routing changes
4. `grep -c 'sys.path' tests/conftest.py` — returns 0
5. `gh pr view 259 --json mergeable` — MERGEABLE

## Validation

After pushing fixes to `story-096/blocked-question`: verify PR #259 shows MERGEABLE status on GitHub, CI passes, and Morris re-review does not raise new blockers.

## Acceptance Diff

The PR for this story MUST include changes to these files. Phase 8 will
fail if any are missing from `git diff origin/main --name-only`:

- `tests/test_queue_smoke.py` — smoke tests (already in PR, must survive rebase)
- `tests/conftest.py` must-contain `smoke` — smoke marker registration preserved, sys.path hack removed
- `features/story-096-queue-smoke/test-design.md` — consistency fix

## Codebase Context

| Component | File |
|-----------|------|
| PR branch | `story-096/blocked-question` |
| Sibling PRs (pattern reference) | PR #258 (`story-095/queue-smoke`), PR #260 (`story-100/story-100`) |
| Test file | `tests/test_queue_smoke.py` |
| Global conftest | `tests/conftest.py` |
| SDLC artifacts | `features/story-096-queue-smoke/` |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Rebase introduces new conflicts from recent main merges | Medium | Resolve incrementally; `.project` is the only known conflict point |
| Removing sys.path.insert breaks other tests importing from scripts/ | Low | Check grep for `import work_queue` across test files; localize fix |

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Import mechanism for scripts/ | Local `sys.path.insert` in test_queue_smoke.py or pytest conftest in scripts/ | Avoids polluting global test namespace; matches existing test patterns |
| Push to same branch | `story-096/blocked-question` | Dispatch instruction: fixes land on the existing PR, not a new one |
