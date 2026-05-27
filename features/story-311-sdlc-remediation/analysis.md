# Analysis: STORY-311 — SDLC Remediation Gap Analysis

**Phase:** 4 — Analysis
**Story:** STORY-311
**Date:** 2026-04-16
**Scope:** Medium

---

## 1. Executive Summary

Two open PRs shipped implementation code without completing the SDLC deliverable trail. This analysis documents every missing artifact, the root cause, and the remediation scope.

---

## 2. PR #36 — STORY-304 (story-304/retry branch)

### What shipped

STORY-304 implemented event-driven Teams presence: a new `POST /internal/presence` endpoint on each agent gateway (`presence_endpoint.py`), a push client in the ops-console (`presence_push.py`), retry logic, and cleanup of stale `morris_presence.py` heartbeat calls. 35 tests pass GREEN.

### Deliverable audit

| File | Status |
|------|--------|
| `seed.md` | Present on main (from earlier commit) |
| `analysis.md` | Not required (Small → Medium upgrade, analysis skipped) |
| `feature-spec.md` | Not present — story was originally Small, upgraded in flight |
| `security-review.md` | Present on branch, missing on main |
| `test-design.md` | Present on branch |
| `code-review.md` | Present on branch, missing on main |
| `predeploy-gate.md` | **MISSING** |

### Root cause

STORY-304 started as a Small story (seed only + implement). Mid-flight it grew to Medium complexity (multiple services, retry logic, internal HTTP auth). The phase path was not formally upgraded; phases 6, 6b, 8b, and 11 were partially executed without writing all outputs.

### Risk

Without `predeploy-gate.md`, the SDLC compliance gate will block the PR merge. The security-review and code-review are on the branch but not yet visible to reviewers on main.

---

## 3. PR #35 — STORY-305 (story-305/fix-cost-display branch)

### What shipped

PR #35 bundles four stories:
- **STORY-227:** Managed identity Azure auth migration (full Medium deliverables present)
- **STORY-229:** Bot-to-bot Teams messaging via app token (partial — missing security-review.md, ux-review.md, ops-review.md)
- **STORY-253:** Commit-gated dispatch completion (Small — seed.md + test-design.md present, correct for Small)
- **STORY-305:** Fix `foundry_cost_usd` population in cost parser (the named story — **no folder exists at all**)

### Deliverable audit — STORY-305 folder

| File | Status |
|------|--------|
| `features/story-305-fix-cost-display/` | **FOLDER DOES NOT EXIST** |

### Deliverable audit — STORY-229

| File | Status |
|------|--------|
| `seed.md` | Present |
| `analysis.md` | Present |
| `feature-spec.md` | Present |
| `test-design.md` | Present |
| `code-review.md` | Present |
| `predeploy-gate.md` | Present |
| `security-review.md` | **MISSING** |
| `ux-review.md` | **MISSING** |
| `ops-review.md` | **MISSING** |

### Deliverable audit — STORY-227

| File | Status |
|------|--------|
| `seed.md`, `analysis.md`, `feature-spec.md`, `test-design.md`, `code-review.md`, `predeploy-gate.md`, `security-review.md`, `ux-review.md`, `ops-review.md` | All present |

### Deliverable audit — STORY-253

| File | Status |
|------|--------|
| `seed.md` | Present |
| `test-design.md` | Present |
| (Small scope — no further deliverables required) | Compliant |

### Root cause

STORY-305 itself is a tiny fix (one-line `foundry_cost_usd` population) that was committed directly onto the story-305 branch without creating its own `features/` folder. The branch name and PR title reference STORY-305, but the SDLC folder was never created. For STORY-229, the three review phases (6b, 6c, 6d) were skipped during rapid implementation.

### Bundling rationale gap

PR #35 contains four stories but no document explains why they were bundled or how the stories relate. This is required for audit traceability.

---

## 4. .project Staleness

The `.project` file on `story-304/retry` still shows Phase 7 / RED state (last updated after test-design commit). It needs to reflect Phase 8 complete / all tests GREEN.

---

## 5. Remediation Plan

| Action | Branch | Files |
|--------|--------|-------|
| Write predeploy-gate.md for STORY-304 | story-304/retry | `features/story-304-event-driven-presence/predeploy-gate.md` |
| Create story-305 folder with all Medium deliverables | story-305/fix-cost-display | 9 files in `features/story-305-fix-cost-display/` |
| Write missing review deliverables for STORY-229 | story-305/fix-cost-display | `security-review.md`, `ux-review.md`, `ops-review.md` |
| Write bundling rationale | story-311/sdlc-remediation | `features/story-311-sdlc-remediation/bundling-rationale.md` |
| Update .project | story-304/retry | `.project` |

Total new files: 14

---

## 6. Out of Scope

- No application code changes
- No changes to STORY-227 or STORY-253 (both compliant for their scope)
- No changes to test suites
