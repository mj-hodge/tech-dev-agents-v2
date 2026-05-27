# STORY-529: Rebase PR #77 (STORY-495 CI/CD pipeline) onto main

## Problem Statement

PR #77 (`story-495/story-495`) introducing the ops-console CI/CD pipeline has
merge conflicts with `origin/main`. The PR is in CONFLICTING state and cannot
be merged until the branch is rebased and conflicts resolved.

## Scope

Small — operational rebase task. No new features; preserve all existing CI/CD
pipeline code from STORY-495 while incorporating upstream changes from main.

## Key Deliverables

1. Rebase `story-495/story-495` onto current `origin/main`.
2. Resolve all merge conflicts, preserving both sides where possible.
3. Ensure these files remain intact post-rebase:
   - `.github/workflows/deploy-ops-console.yml` (CI/CD workflow)
   - `deployment/ops-console/deploy.sh` (deploy script)
   - `deployment/ops-console/rollback.sh` (rollback script)
4. All existing pytest tests continue to pass.
5. Force-push the rebased branch so PR #77 becomes MERGEABLE.

## Constraints

- Do NOT create a new PR — PR #77 already exists.
- The deploy workflow must retain its `on: push: branches: [main]` trigger.
- No CI/CD pipeline code may be dropped during conflict resolution.

## Test Criteria

1. `gh pr view 77 --json mergeable` returns `MERGEABLE`.
2. `.github/workflows/deploy-ops-console.yml` exists and contains the push-to-main trigger.
3. `deployment/ops-console/deploy.sh` and `rollback.sh` exist on the branch.
4. All pytest tests pass (no regressions introduced by the rebase).

## Validation

After push, confirm via `gh pr view 77` that `mergeable=MERGEABLE` and the
commit count is 4 (one rebased commit per original commit, no duplicates).
