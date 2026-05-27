# Seed: STORY-311 — SDLC Remediation for tech-dev-agents

**Story:** STORY-311
**Date:** 2026-04-16
**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done

---

## Problem Statement

PRs #35 (STORY-305) and #36 (STORY-304) shipped implementation code but are missing required SDLC deliverables. The SDLC compliance guard will reject these PRs without:

- **PR #36 (STORY-304):** `predeploy-gate.md` missing from `features/story-304-event-driven-presence/` (security-review.md and code-review.md already present on branch but not yet on main)
- **PR #35 (STORY-305):** The `features/story-305-fix-cost-display/` folder does not exist; bundled stories 227, 229, 253 have partial or missing Medium-scope deliverables
- **Stale `.project`** on the story-304/retry branch shows wrong phase/status

## Goals

1. Write all missing Phase 6b/8b/11 deliverables for STORY-304 (PR #36)
2. Create `features/story-305-fix-cost-display/` with full Medium deliverables
3. Fill gaps in `features/story-229-bot-teams-messaging/` (security-review.md, ux-review.md, ops-review.md missing)
4. Document the 4-story bundling rationale for PR #35 (stories 227, 229, 253, 305)
5. Fix stale `.project` metadata on relevant branches

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | `features/story-304-event-driven-presence/predeploy-gate.md` exists on story-304/retry |
| AC-2 | `features/story-305-fix-cost-display/` folder exists with seed.md, analysis.md, feature-spec.md, security-review.md, ux-review.md, ops-review.md, test-design.md, code-review.md, predeploy-gate.md |
| AC-3 | `features/story-229-bot-teams-messaging/security-review.md`, `ux-review.md`, `ops-review.md` exist on story-305/fix-cost-display |
| AC-4 | `features/story-311-sdlc-remediation/bundling-rationale.md` documents the 4-story PR #35 bundle |
| AC-5 | `.project` updated on story-304/retry to show correct phase/status |

## Technical Plan

Pure documentation task. All work is writing SDLC deliverable markdown files to the correct `features/` subdirectories on the correct branches. No application code changes.

## Scope Expansion (PR #38 Review Feedback)

Morris's review of PR #38 identified three blocking issues:

1. **SECURITY:** Path traversal in `terminal_guard.py` — `cat /home/hermes/state/../dev/...` bypasses the safe-read carve-out `startswith` check. Fix: `os.path.normpath(rest)` before comparison.
2. **SDLC:** backlog.md and development-tasks.md missing STORY-311 registration.
3. **SCOPE:** PR description doesn't document operational code changes (dispatch retry, fleet-check.sh, SKILL.md, guard).

## Constraints

- Deliverables for STORY-304 must be committed to `story-304/retry` branch
- Deliverables for STORY-305/229 must be committed to `story-305/fix-cost-display` branch
- STORY-311 own deliverables live on `story-311/sdlc-remediation` (this branch)
- Use Sonnet model (Phase 8 default for documentation)
