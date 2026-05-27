# Feature Spec: STORY-311 — SDLC Remediation

**Phase:** 6 — Design
**Story:** STORY-311
**Date:** 2026-04-16
**Scope:** Medium

---

## Overview

Write all missing SDLC deliverable markdown files for PR #35 (STORY-305 bundle) and PR #36 (STORY-304). No application code changes. All files follow the established templates in `.sdlc/templates/`.

---

## Deliverable Manifest

### Group A — story-304/retry branch

Target directory: `features/story-304-event-driven-presence/`

| File | Phase | Template | Notes |
|------|-------|----------|-------|
| `predeploy-gate.md` | 11 | predeploy-gate | Verify readiness for prod deploy of presence endpoint + push client |

### Group B — story-305/fix-cost-display branch

#### New folder: `features/story-305-fix-cost-display/`

| File | Phase | Template | Notes |
|------|-------|----------|-------|
| `seed.md` | 1 | seed | Describes the `foundry_cost_usd` one-line fix |
| `analysis.md` | 4 | analysis | Root cause of zero-display bug |
| `feature-spec.md` | 6 | feature-spec | Spec for cost parser fix |
| `security-review.md` | 6b | security-review | No new attack surface; cost data read-only |
| `ux-review.md` | 6c | ux-review | Agent card cost display correctness |
| `ops-review.md` | 6d | ops-review | No new infra; cache TTL considerations |
| `test-design.md` | 7 | test-design | Existing cost tests cover the fix |
| `code-review.md` | 8b | code-review | Review of the one-line parser fix |
| `predeploy-gate.md` | 11 | predeploy-gate | Pre-deploy checklist |

#### Additions to existing folder: `features/story-229-bot-teams-messaging/`

| File | Phase | Template | Notes |
|------|-------|----------|-------|
| `security-review.md` | 6b | security-review | MSAL client-credentials, secret storage |
| `ux-review.md` | 6c | ux-review | No UI changes; ops ergonomics of credential setup |
| `ops-review.md` | 6d | ops-review | Env var requirements, secret rotation |

### Group C — story-311/sdlc-remediation branch (this branch)

| File | Phase | Notes |
|------|-------|-------|
| `seed.md` | 1 | Done |
| `analysis.md` | 4 | Done |
| `feature-spec.md` | 6 | This file |
| `security-review.md` | 6b | Documentation only — no security surface |
| `ux-review.md` | 6c | N/A for documentation story; minimal file required |
| `ops-review.md` | 6d | N/A for documentation story; minimal file required |
| `test-design.md` | 7 | File-existence verification script |
| `code-review.md` | 8b | Review of all new deliverable files |
| `predeploy-gate.md` | 11 | Gate for this story itself |
| `bundling-rationale.md` | reference | Explains PR #35 4-story bundle |

---

## Content Guidelines

Each deliverable must:

1. **Accurately reflect the actual implementation** — cross-reference with PR diffs, not hypothetical designs
2. **Use present tense** for implemented state, past tense for historical context
3. **Not contain secrets** — no tokens, passwords, or internal hostnames
4. **Be concise** — these are retroactive write-ups; a thorough paragraph is better than a padded page

---

## File Routing

```
story-311/sdlc-remediation   → features/story-311-sdlc-remediation/   (all Group C files)
story-304/retry              → features/story-304-event-driven-presence/predeploy-gate.md
story-305/fix-cost-display   → features/story-305-fix-cost-display/    (all Group B new-folder files)
                             → features/story-229-bot-teams-messaging/ (Group B addition files)
```

---

## Definition of Done

- All files in the manifest exist on the correct branch
- Each file is non-empty and contains all required top-level sections
- `.project` on story-304/retry updated to Phase 11 Complete
- `bundling-rationale.md` references all four stories with commit evidence
