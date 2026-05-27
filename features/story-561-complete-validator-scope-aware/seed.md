# Seed: STORY-561 — `/api/dispatch/complete` Validator Ignores Story Scope

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_replace |
| Scope | small |
| Feature Name | Scope-aware deliverable validation in the `/complete` endpoint |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Status | Seed complete 2026-04-24 — NOT DISPATCHED (parked for later) |

---

## 1. Idea / Trigger

On 2026-04-24 03:09Z, Morris attempted to manually reconcile two stuck DB rows (STORY-505 and STORY-528, both fully done code-wise with PRs open) by calling `POST /api/dispatch/complete/{story_id}`. Both calls rejected with HTTP 422:

```
SDLC deliverables missing for STORY-505 (scope=large): feature-spec.md.
Required in features/story-505/ or features/story-505-*/:
  seed.md, analysis.md, feature-spec.md, test-design.md
```

The validator's required-list is the **Medium-scope deliverable set**. Per `CLAUDE.md` Phase Deliverables table, Large/New scope stories produce a *5-document set* at Phase 6 INSTEAD of `feature-spec.md`:

| Phase | Output File(s) | Scope |
|-------|---------------|-------|
| 6 | `feature-spec.md` (Medium) OR `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `implementation-plan.md` (Large/New) | Medium+ |

The `/complete` endpoint's validator doesn't read `scope` from the story row and always looks for `feature-spec.md`, so every Large-scope story that correctly follows CLAUDE.md's own policy will fail the `/complete` validator.

Workaround used tonight: added a 3-line `feature-spec.md` stub on each branch that points at the real 5-doc set, committed + pushed, retried `/complete`, got HTTP 200. This is the wrong fix — agents shouldn't need a bogus stub for validator appeasement. The policy says Large uses 5 docs; the validator should respect that.

## 2. Problem Statement

- **Validator hardcodes Medium deliverables:** For Large scope, should require `seed.md`, `analysis.md`, the 5-doc Phase-6 set (or `feature-spec.md` as fallback for Large-retros that used Medium deliverables), and `test-design.md`.
- **Tonight's consequence:** Two correctly-completed stories (STORY-505, 528) got stuck in the queue until a human manually POSTed stubs. With the phase runner missing-`/complete`-call bug (STORY-560) and this validator bug, Large stories can't auto-close even when everything else works.
- **Cross-cutting impact:** The phase runner's completion-reporting path (STORY-560) will hit this validator once it starts calling `/complete`. Fixing STORY-560 without fixing this validator just moves the failure from "silent no-op" to "explicit HTTP 422 / retry loop".

## 3. Scope Classification

**Small.** Single-file change in the ops-console backend (the dispatch `/complete` route + service helper). Focused unit tests. No schema changes, no dispatch-poller changes, no VM changes.

Phase path `1 → 7 → 8 → Done`. Skip 2-6 and 9-10.

## 4. Codebase Context

### Affected Files

- **`tech_dev_agents/ops_console/routes/dispatch.py`** — Find the `complete_story` route and its deliverable-validation step. Grep for the error text `SDLC deliverables missing for` — that's the site. Pull the deliverable list from a scope-aware constant (or helper function) that mirrors CLAUDE.md's table:

  ```python
  REQUIRED_DELIVERABLES = {
      "small":  ["seed.md", "test-design.md"],
      "medium": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
      "large":  ["seed.md", "analysis.md", "specification.md", "architecture.md",
                 "api-design.md", "database-schema.md", "implementation-plan.md",
                 "test-design.md"],
      "new":    (same as large)
  }
  ```

  For Large/New, accept `feature-spec.md` as a fallback too (some retrofitted stories have a consolidated feature-spec.md instead of the 5-doc set — don't break them).

- **`tech_dev_agents/ops_console/services/dispatch_db_service.py`** — The scope is stored on the row; pass it to the validator.

- **`tests/ops_console/test_complete_endpoint_scope_aware.py`** (new) — Cases:
  - Large story with 5-doc Phase-6 set (no feature-spec.md) → 200 (tonight's repro)
  - Large story with feature-spec.md only (no 5-doc set) → 200 (fallback)
  - Large story with neither → 422 listing the 5-doc set as missing
  - Medium story with feature-spec.md → 200 (regression)
  - Medium story without feature-spec.md → 422
  - Small story with seed.md + test-design.md → 200 (regression — small has fewer requirements)

### Not Touched

- `sdlc_phase_runner.py` — its per-phase skip checks have their own deliverable lists; separate concern.
- Phase runner's missing-`/complete`-call bug (STORY-560) — complementary, lands separately.

## 5. Success Criteria

- [ ] **SC-1:** `POST /api/dispatch/complete/{story_id}` on a Large-scope story with all 5 Phase-6 docs (and no `feature-spec.md`) returns HTTP 200.
- [ ] **SC-2:** Same endpoint on a Large-scope story with `feature-spec.md` (and no 5-doc set) also returns HTTP 200 — backward-compat for consolidated Large stories.
- [ ] **SC-3:** A Large-scope story with NEITHER a 5-doc set NOR a `feature-spec.md` returns HTTP 422 with the error listing *both* acceptable deliverable sets so the operator knows what's actually required.
- [ ] **SC-4:** Medium-scope stories continue to require `feature-spec.md` (regression guard).
- [ ] **SC-5:** Small-scope stories require only `seed.md` + `test-design.md` (regression guard).
- [ ] **SC-6:** Validator helper is extracted to a single scope→list map so future CLAUDE.md policy changes are a one-line edit.

## 6. Out of Scope

- Changing CLAUDE.md's deliverable policy itself. This story adapts the validator to the existing policy.
- Automating the stub-feature-spec.md workaround in the phase runner. Stubs are pollution; fixing the validator removes the need.
- Retroactively cleaning up the stubs added to STORY-505 and STORY-528 branches tonight. They can stay (they do no harm) or be removed later in a separate cleanup.

## 7. Related Stories

- **STORY-559 (parked):** Phase runner misclassifies SDK failures. Different file, different failure mode, same theme of "phase runner talks to ops-console and the contract is unclear."
- **STORY-560 (parked):** Phase runner doesn't call `/complete` after the terminal phase. **This story must land before or with STORY-560** — otherwise STORY-560's fix will hit the validator bug and silently retry.
- **STORY-507 (in progress):** Resume-aware phase runner.

## 8. Incident Log

### 2026-04-24 03:09Z — Manual /complete on STORY-505 + STORY-528 both rejected

- STORY-505 (scope=large): branch had `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `implementation-plan.md` (all Large-scope deliverables per CLAUDE.md). Validator required `feature-spec.md`. HTTP 422.
- STORY-528 (scope=large): identical pattern. HTTP 422.
- Morris committed a 3-line `feature-spec.md` stub on each branch, retried `/complete`, got HTTP 200.
- Workaround commit SHAs: STORY-505 `f4e8bfc`, STORY-528 `4f7cabc`.

## 9. Dispatch Notes (for future dispatcher)

- Target repo: `tech-dev-agents`.
- Branch convention: `story-561/story-561`.
- Target role: `developer`.
- Scope: `small`.
- Can dispatch independently. Ideal to batch with STORY-560 — both files changes are small and co-located in the ops-console dispatch module.
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20 min.

**Frontend:** false

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
