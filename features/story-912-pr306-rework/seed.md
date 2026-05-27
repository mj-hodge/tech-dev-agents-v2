# STORY-912: Rework PR #306 — Failure Classifier Hardening
**Frontend:** false

## Seed

| Field | Value |
|-------|-------|
| Story | STORY-912 |
| Date | 2026-05-06 |
| Scope | small |
| Type | rework / operational |
| Parent PR | #306 (STORY-872: harden failure classifier) |
| Branch | `story-872/failure-classifier-hardening` |

## Problem Statement

PR #306 (STORY-872) had two blockers preventing merge:

1. **CONFLICTING** — the branch had diverged from `origin/main`. Previous rebase dispatch (STORY-900) failed pre-claim.
2. **CHANGES_REQUESTED** — Morris's initial review flagged cross-story contamination (STORY-871 and STORY-861 files bundled into a STORY-872 PR). This was subsequently resolved in a follow-up push, confirmed by Morris's re-review (`COMMENTED`, 2026-05-06T09:24:28Z): *"All prior feedback addressed ✅ … Once rebased and CI green → ready for approval and merge."*

## Acceptance Criteria

- AC-1: Branch `story-872/failure-classifier-hardening` rebased onto `origin/main` with no conflicts
- AC-2: `git diff origin/main --name-only` shows only STORY-872 files (9 files: `.project`, `backlog.md`, `development-tasks.md`, 4 feature deliverables, `dispatch_failure_policy.py`, test file)
- AC-3: Force-push to origin succeeds with `--force-with-lease`
- AC-4: PR #306 transitions from CONFLICTING → UNKNOWN → MERGEABLE
- AC-5: No new PR created — existing PR #306 updated in-place

## Solution

Rebase `story-872/failure-classifier-hardening` onto `origin/main`. Two conflict files:
- `.project` — parallel multi-worker story table; resolved by merging STORY-872 row + HEAD's STORY-440 status
- `development-tasks.md` — current sprint table; resolved by keeping all rows from both sides

## Phase Path

`operational` — no SDLC phases required. Rework is a rebase operation, not a code change.

## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.

## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

