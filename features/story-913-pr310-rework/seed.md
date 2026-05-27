# STORY-913 — Rework PR #310 (STORY-873: stop duplicate failed events)

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Frontend | false |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Date | 2026-05-06 |
| Parent Story | STORY-873 |
| Related PR | #310 |

---

## Problem Statement

PR #310 (STORY-873) received CHANGES_REQUESTED from Morris review and has two CI failures. The previous rebase dispatch (STORY-901) failed pre-claim. The PR needs rework to address:

1. **CI failures** — `test_sdlc_framework_compliance.py` fails because `seed.md` is missing `## Test Criteria`, `## Validation` sections, and `Frontend:` field declaration (required for seeds committed after 2026-04-22/2026-04-26)
2. **Missing SDLC deliverable** — `test-design.md` not present in `features/story-873-*/` (required by Phase 7 per seed's declared path)
3. **Merge conflict** — branch is behind origin/main and needs rebase

---

## Solution

1. Update `features/story-873-stop-duplicate-failed-event-emission/seed.md` to add required sections: Overview table with `Frontend: false`, `## Test Criteria`, `## Validation`, and rename sections to match SDLC conventions
2. Create `features/story-873-stop-duplicate-failed-event-emission/test-design.md` documenting the 16 tests in `test_dispatch_failure_policy_873.py` and 2 updated assertions in `test_dispatch_failure_classifier.py`
3. Rebase `story-873/stop-duplicate-failed-event-emission-clean` onto `origin/main`
4. Push to existing branch (no new PR)

---

## Acceptance Diff

| File | Change |
|------|--------|
| `features/story-873-stop-duplicate-failed-event-emission/seed.md` | Add Overview table with Frontend field, ## Test Criteria, ## Validation sections |
| `features/story-873-stop-duplicate-failed-event-emission/test-design.md` | New file: Phase 7 test design document |

## Test Criteria

1. `test_every_seed_written_after_20260422_has_required_sections` passes — seed.md contains `## Test Criteria` and `## Validation`
2. `test_every_seed_dispatched_after_20260426_declares_frontend_classification` passes — seed.md declares `Frontend: false`
3. All 16 tests in `test_dispatch_failure_policy_873.py` still pass (no regressions from seed/doc changes)
4. Branch rebases cleanly onto origin/main with no merge conflicts

## Validation

1. CI pipeline passes all checks after push (specifically `Python contract + unit tests` job)
2. PR #310 shows no merge conflicts
3. Morris re-review finds `test-design.md` present
