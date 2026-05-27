# Seed — Story 344: Rebase PR #44

## Context

PR #44 (branch `story-336/fix-dispatch-poller-bugs`) fixes 4 critical dispatch poller bugs that caused 41 story failures. The branch had diverged from `main` due to concurrent merges, making it unmergeable.

## Objective

Rebase the PR branch onto `origin/main` to resolve merge conflicts, preserving all bugfix commits, and force-push so the PR is cleanly mergeable.

## Scope

**Small** — operational git rebase, no code changes.

## Acceptance Criteria

1. Branch `story-336/fix-dispatch-poller-bugs` is rebased onto latest `main`
2. All 4 bugfix commits are preserved
3. PR #44 is mergeable with no conflicts
4. Force-push succeeds to remote
