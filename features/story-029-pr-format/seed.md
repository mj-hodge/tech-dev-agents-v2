# STORY-029: Standardize Agent PR Format

## Problem

Agent-created PRs have inconsistent quality. PR #20 (Entra SSO fix) is the gold standard with structured summary, detailed test plan, and handback items. PRs #12 and #13 are one-liner bodies with no structure — Mark cannot evaluate work, handback items get lost, and deferred work is invisible.

## Solution

1. Create `.github/PULL_REQUEST_TEMPLATE.md` with mandatory sections:
   - **Summary** — what changed and why (table for multi-item changes)
   - **Decisions Made** — architectural choices and rationale
   - **Test Results** — counts with checkboxes
   - **Mark TODO** — handback items (picked up by `/whats-next`)
   - **Deferred Items** — out-of-scope work discovered
   - **Seed Link** — traceability to the original spec

2. Update `deployment/vm/SOUL.md` to reference the template and set the quality bar (PR #20 = good, #12/#13 = bad).

## Scope

Small — two files changed, no code logic.

## Acceptance Criteria

- [ ] `.github/PULL_REQUEST_TEMPLATE.md` exists with all six sections
- [ ] `SOUL.md` PR creation section references the template
- [ ] SOUL.md explicitly names PR #20 as gold standard and #12/#13 as bad examples
