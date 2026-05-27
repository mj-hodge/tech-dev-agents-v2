# Seed: STORY-565 — Mandate `frontend` classification on seeds + Playwright `@smoke` gate at completion

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_replace |
| Scope | small |
| Feature Name | Frontend classification field in Phase 1 seed + Phase 8 completion gate that rejects `/complete` for `frontend: true` stories lacking a `@smoke`-tagged Playwright spec |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Target Repo | tech-dev-agents |
| Target Role | developer |
| Target Branch | `story-565/story-565` |
| Status | Seed written 2026-04-24 — ready to dispatch |

---

## 1. Idea / Trigger

Mark on 2026-04-24:

> should there be any framework changes for tickets with UI components? it seems too common that we have Playwright misses

Pattern observed: UI stories ship through the SDLC without a matching Playwright `@smoke` spec. The existing guardrails close *part* of the loop but not all of it:

- `tests/deployment/test_phase_runner_frontend_enforcement.py` — asserts that IF a story has a `## Acceptance Diff` block mentioning a Playwright spec, the phase runner's gate logic refuses to mark Phase 7 complete without the spec on disk. Good, but only fires when the seed mentions Playwright in the first place.
- `.github/workflows/test.yml` — `playwright-frontend-tests` job only runs when `*.tsx/jsx/css/scss/html` files change or when paths match `frontend/` / `e2e/`. Good for the CI side, but agents pushing a UI diff plus a test-less `/complete` still get through.

Seeds don't declare "touches UI?" explicitly, and the `/complete` endpoint's deliverable validator doesn't look at Playwright at all. So a UI story can:

1. Ship a `.tsx` change in Phase 8
2. Not add any `e2e/**/*.spec.ts`
3. Pass the completion-gate validator (which only checks for `seed.md`, `test-design.md`, etc.)
4. Get its DB row moved to completed
5. Leave the Playwright smoke suite unchanged — no new coverage

This story closes that gap by making UI-ness a first-class seed field and turning it into an enforceable gate at `/complete`.

## 2. Problem Statement

**For agents dispatching UI stories, the current SDLC lets `/complete` succeed without ever adding a Playwright `@smoke` test**, so visual regressions and broken UI flows ship undetected.

Three concrete failure modes already observed this quarter:
- STORY-019, STORY-022, STORY-023 dashboard work — shipped with zero new `@smoke` specs. Subsequent regressions discovered manually.
- Frontend-adjacent refactors (CSS-only tweaks, component rename PRs) — the existing `playwright-frontend-tests` CI job tries to run but fails on the pre-existing `jest-matchers-object` dep conflict, so "red" becomes background noise and agents learn to ignore it.
- Seeds for mixed (backend + minor UI) stories don't declare UI scope, so Phase 7 test-design doesn't require a Playwright spec even when one would be appropriate.

## 3. Scope Classification

**Small.** Touches a handful of focused files, no schema changes, no new endpoints, single-repo change.

Phase path: `1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done`. Skip 2-6 and 9-10.

## 4. Codebase Context

### Affected Files (main repo: `tech-dev-agents`)

- **`tech_dev_agents/ops_console/services/dispatch_db_service.py`** — Look for the `complete_story` / `complete()` method. After the existing deliverable validator passes, add a frontend-gate check: if the story row's `frontend == True` (new column? see below — OR pulled from seed.md), assert that the PR's diff contains at least one `e2e/**/*.spec.ts` file tagged with `@smoke`. If not, reject with HTTP 422 + a message that points at this story for context.
- **`tech_dev_agents/ops_console/routes/dispatch.py`** — The route wrapper around the service method. Thread the `frontend` flag through if needed.
- **`tests/test_sdlc_framework_compliance.py`** — Add `test_every_seed_dispatched_after_20260425_declares_frontend_classification` (mirror of the existing `test_every_seed_written_after_20260422_has_required_sections` pattern). Require a `- **Frontend:** true|false` line in the Overview table OR a standalone `## Frontend Classification` section. Grandfather pre-cutoff seeds by git-first-commit timestamp the same way the existing test does.
- **`tests/ops_console/test_complete_endpoint_frontend_gate.py`** (new) — RED tests for the completion-gate behavior. Cases:
  - `frontend: true` + PR diff has `e2e/dashboard/@smoke` spec → 200
  - `frontend: true` + PR diff has no `e2e/**/*.spec.ts` → 422 with actionable message
  - `frontend: true` + PR diff has an `e2e/` file but no `@smoke` tag → 422
  - `frontend: false` + no e2e → 200 (regression guard)
  - `frontend: false # rationale` + no e2e → 200 (escape-hatch regression guard)
- **`.sdlc/skills/phase-1/SKILL.md`** (secondary, in submodule — can be a follow-on PR if cross-repo friction is too high) — Add a `Frontend (required)` bullet to the seed template so authors get prompted for it.

### Data location

Where does the `frontend` flag live? Two options:

1. **Parse from `seed.md` on the fly.** `/complete` already reads the seed for deliverable presence; add a regex pass for `^Frontend:\s*(true|false)` in the Overview table or a `## Frontend Classification` header. Zero schema change.
2. **Add a `frontend BOOLEAN` column to `dispatch_items`.** Cleaner but adds a migration.

**Recommend option 1** for this Small scope. Schema change can come later if we want to query/aggregate.

### Success criteria (expanded in Phase 6 for larger stories; this is Small so they live here)

- [ ] **SC-1:** Every new seed (committed after a declared cutoff, e.g. `2026-04-25T00:00Z`) carries a `- **Frontend:** true|false` line in the Overview table. Existing seeds grandfathered by git-first-commit timestamp (same pattern as `test_every_seed_written_after_20260422_has_required_sections`).
- [ ] **SC-2:** `POST /api/dispatch/complete/{story_id}` on a `frontend: true` story with NO `e2e/**/*.spec.ts` containing `@smoke` in the PR diff returns HTTP 422 with a message naming what's missing.
- [ ] **SC-3:** Same endpoint on a `frontend: true` story WITH a matching spec returns HTTP 200.
- [ ] **SC-4:** `frontend: false` stories (or `frontend: false # rationale`) keep their existing completion behavior — no new gate fires.
- [ ] **SC-5:** Framework-contract test asserts every post-cutoff seed declares the field. Missing field → test FAILS with a clear actionable error listing the offenders.
- [ ] **SC-6:** Regression: Small-scope backend stories that didn't previously need Playwright don't newly require it.
- [ ] **SC-7:** The message on a 422 from SC-2 includes the exact `e2e/**/*.spec.ts` glob + `@smoke` tag rule so an agent (or human) can self-correct without reading the code.

## 5. Out of Scope

- **Retroactively classifying existing seeds.** Pre-cutoff seeds are grandfathered; authors can backfill voluntarily.
- **Fixing the pre-existing Playwright CI dep conflict** (`jest-matchers-object` collision). That deserves its own story — and it's load-bearing on whether this gate *signals anything*, so note it as a blocker for the gate to feel "real" but not a blocker for merging this story.
- **Visual regression** (screenshot baselines). The gate only verifies that *a* `@smoke` test was added; it doesn't verify the test is meaningful.
- **`frontend BOOLEAN` column** on `dispatch_items`. Parse-from-seed is sufficient for now.
- **Extending the gate to Phase 7.** Phase 7 already has a partial gate via `test_phase_runner_frontend_enforcement`; wiring this same rule there is a small follow-on if useful, but the Phase 8 completion gate is the enforcement point that matters.

## 6. Related Stories

- **STORY-541 (Playwright agent row visual contract):** introduced the frontend detection in `.github/workflows/test.yml` that this story leans on. Complementary.
- **STORY-561 (scope-aware `/complete` validator):** the same `/complete` endpoint that already takes scope into account — this story adds one more check alongside those. Ideal to land in sequence or together.
- **STORY-544 (contract-test harness):** the framework-contract gate pattern this story mirrors. Reference commit for the grandfather-by-git-timestamp pattern.
- **PR #102 (Mark's auto-dispatch):** documents the SKILL Discipline rule the new `frontend` field supports.

## 7. Test Criteria

Phase 7 will produce `test-design.md` and RED test modules covering:

- `test_every_seed_dispatched_after_20260425_declares_frontend_classification` in `tests/test_sdlc_framework_compliance.py`:
  - Walks `features/story-*/seed.md`, filters by git-first-commit timestamp >= cutoff (reuses existing helper).
  - Regex for `- **Frontend:** true|false` in the Overview table OR a `## Frontend Classification` section.
  - Asserts missing-list is empty.
- `tests/ops_console/test_complete_endpoint_frontend_gate.py` (new):
  - Fixture: fake PR diff + fake seed.md written into a temp features/ dir.
  - Case A: `frontend: true` + diff contains `e2e/dashboard/login.smoke.spec.ts` with `@smoke` annotation → 200.
  - Case B: `frontend: true` + diff contains no `e2e/` files → 422, error naming `e2e/**/*.spec.ts` and `@smoke`.
  - Case C: `frontend: true` + diff contains `e2e/dashboard/login.spec.ts` without `@smoke` in the file → 422.
  - Case D: `frontend: false` + no e2e → 200 (regression guard).
  - Case E: `frontend: false # CSS-only refactor, covered by existing @smoke suite` + no e2e → 200 (escape-hatch).

All tests mock-only (no real Postgres, no real GitHub API) — must run inside the `python-tests` gating CI job that STORY-565's sibling stories already use.

## 8. Validation

After Phase 8 lands:

1. `python-tests` CI job is GREEN on the PR with the new module executed.
2. Dispatch a tiny Small `frontend: true` test story to a dev agent; agent fails `/complete` until it adds an `@smoke` spec. Verify the 422 message is actionable.
3. Dispatch a tiny Small `frontend: false` test story; agent completes cleanly (regression check).
4. `test_every_seed_dispatched_after_20260425_declares_frontend_classification` fails with a clear message when one hand-crafted post-cutoff seed is missing the field; passes when the field is added.
5. Manually rebase and re-run CI on one already-open UI PR (e.g. a dashboard-touching PR if one is open) — confirm the gate either fires appropriately or is correctly skipped per `frontend: false`.

## 9. Dispatch Notes (for queue)

- Target repo: `tech-dev-agents`
- Target branch: `story-565/story-565` (poller default)
- Target role: `developer`
- Scope: `small`
- Phase path: `1 → 7 → 8 → Done` (skip 2-6 and 9-10; skip 8b and 11 per automated-dispatch policy — Morris's PR review covers those)
- Expected runtime: Phase 7 ~15 min, Phase 8 ~25–30 min
- No cross-repo dependency: the `.sdlc` SKILL.md template update is a secondary nice-to-have that can land in a follow-on PR if the agent hits cross-repo friction
- Dispatcher should include PR #103's merge-SHA context so the agent sees the grandfather-by-git-timestamp pattern reused here

## 10. Acceptance Diff

Expected diff shape (approximate token list, not exact LOC):

- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — +20 to +40 lines: new `_verify_frontend_gate()` helper + call site after deliverable check.
- `tests/test_sdlc_framework_compliance.py` — +25 to +45 lines: new test function, may refactor the git-timestamp helper to be shared.
- `tests/ops_console/test_complete_endpoint_frontend_gate.py` — new file, ~120 lines (5 test cases + fixtures).
- `features/story-565-frontend-classification-gate/seed.md` — this file (already written).
- `features/story-565-frontend-classification-gate/test-design.md` — produced by Phase 7.

Must-contain tokens in the final diff:
- `def _verify_frontend_gate`
- `@smoke`
- `e2e/**/*.spec.ts`
- `test_every_seed_dispatched_after_20260425_declares_frontend_classification`
- `test_complete_endpoint_frontend_gate`

## 11. Frontend

**Frontend:** false — this story adds a backend validator + a contract test. No UI surface changes.

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
