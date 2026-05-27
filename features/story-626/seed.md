# Seed: STORY-626 — Remove Playwright artifacts from STORY-621 PR

## Overview

| Field | Value |
|-------|-------|
| Mode | rework |
| Scope | small |
| Frontend | false |
| Feature Name | Clean Playwright test-results/ artifacts from STORY-621 PR 122 |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | story-621/story-621 |
| Status | Seed written 2026-04-25 |
| Priority | 80 — PR 122 is blocked from merge; retry 3/3 |

---

## Problem Statement

PR #122 (STORY-621: fix stale-QUESTION.md needs_info loop) includes 17 Playwright test artifacts committed under `test-results/` — 8 `trace.zip` binaries and 9 `error-context.md` files from dashboard agent-row Chromium test runs. These files are build artifacts that should never be checked in. The PR cannot be merged in this state because (a) binary blobs inflate the repo, (b) the artifacts are environment-specific noise, and (c) reviewers flagged them as a blocker. The core STORY-621 implementation (`_clear_stale_questions`, freshness check logic, content-hash guard) is correct and must not be changed.

## Scope

**Small.** Three mechanical operations on the existing STORY-621 branch: `git rm` the artifacts, add `test-results/` to `.gitignore`, rebase on main. No logic changes. Phase path: 1 → 7 → 8 → Done.

## Target Branch

story-621/story-621

## The Fix

1. **Remove committed artifacts:** `git rm -r test-results/` to delete the entire `test-results/` directory from the index and working tree. Commit with a clear message.
2. **Prevent recurrence:** Add `test-results/` to the repo-root `.gitignore` so future Playwright runs never get staged.
3. **Rebase on main:** Ensure the branch is up to date with `origin/main` so the PR has no merge conflicts.
4. **Do NOT change implementation code.** The `_clear_stale_questions`, freshness check, content-hash, and version-marker logic in `deployment/hermes/sdlc_phase_runner.py` must remain untouched. The `tests/deployment/test_phase_runner_question_md.py` test file must remain untouched.

## Out of Scope

- Any changes to `_clear_stale_questions` or the freshness check logic in `sdlc_phase_runner.py`.
- Any changes to `tests/deployment/test_phase_runner_question_md.py`.
- Squashing or rewriting STORY-621's implementation commits.
- Adding a CI check for accidental binary commits (future story).

## Test Criteria

1. `test-results/` directory does not exist in the branch after the fix commit.
2. `git diff origin/main --name-only` does NOT list any `test-results/*` paths.
3. `.gitignore` contains `test-results/` entry.
4. All existing tests (`tests/deployment/test_phase_runner_question_md.py`) still pass — no regressions from the cleanup.
5. The branch rebases cleanly on `origin/main` with no merge conflicts.

## Validation

1. `gh pr view 122 --json files` no longer lists any `test-results/*` files.
2. `git log --oneline origin/main..HEAD` shows the cleanup commit(s) on top of the original STORY-621 work.
3. CI passes on the updated PR.

## Acceptance Diff

The PR for this story MUST include changes to these files. Phase 8 will
fail if any are missing from `git diff origin/main --name-only`:

- `.gitignore` must-contain `test-results/` — prevent future Playwright artifact commits

_Note: `test-results/` removals will appear as deletions in the diff but are not listed here as "must-contain" targets since they are being removed, not added._

## Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-621/story-621` (rework — targeting existing PR #122)
- Scope: small
- Priority: 80 (PR merge blocker, retry 3/3)
- Expected runtime: Phase 7 ~5 min, Phase 8 ~10 min
- Implementing agent should: (a) check out `story-621/story-621`, (b) `git rm -r test-results/`, (c) add `test-results/` to `.gitignore`, (d) commit, (e) rebase on main, (f) force-push to update PR #122. Do NOT modify any STORY-621 implementation files.
