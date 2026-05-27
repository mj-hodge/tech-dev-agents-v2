# STORY-542 — Framework-Level Playwright Enforcement for Frontend Stories

**Scope:** medium
**Repo:** tech-dev-agents (this story) + `.sdlc` submodule (skill changes Mark applies manually)
**Target Branch:** main

## Context

Mark's SDLC already mentions Playwright in `.sdlc/skills/phase-7/SKILL.md:58` ("frontend test code lives in `e2e/` as Playwright specs") and `.sdlc/agents/phase-10-operations.md:420` ("Playwright smoke tests post-deploy"). But it's documentation, not enforcement. STORY-513 shipped a frontend change with only vitest unit tests, the tests passed, and the user-visible result didn't match Mark's mental model. STORY-541 had to add Playwright requirements to *its own seed* because no part of the framework forced it.

Mark's instruction (2026-04-22): "playwright is part of the core SDLC — spec whatever needs to happen so we're consistent in checking that for all front end tests, for all tickets, for all projects."

This story makes Playwright enforcement structural so:
- Every future frontend seed.md *automatically* requires a Playwright spec and screenshots
- The Acceptance Diff gate refuses to mark a frontend story complete without the e2e/ deliverable
- CI runs Playwright smoke tests against frontend changes and gates merges
- The framework-compliance test suite asserts the rule across every story
- The same enforcement applies to *every project* using this `.sdlc` submodule (advertising-amazon, future projects), not just tech-dev-agents

A "frontend story" is defined as: any story whose Acceptance Diff lists a file matching `frontend/**`, `e2e/**`, or `**/*.tsx`, `**/*.jsx`, `**/*.css`, `**/*.scss`, `**/*.html`, OR any story whose seed.md text mentions "dashboard", "UI", "frontend", "render", or "screen". Both the file-level and the keyword-level detection trigger the requirement; either alone catches different cases (config-only seeds vs. seeds that haven't picked file paths yet).

## Acceptance Diff

The implementation MUST add or modify these files with the must-contain tokens below.

### Code in this repo (tech-dev-agents — agent can commit directly)

- `deployment/hermes/sdlc_phase_runner.py` — must-contain `_is_frontend_story`, must-contain `playwright`, must-contain `e2e/` in the Phase 7 verification path. The phase runner detects frontend stories by scanning the seed.md and Acceptance Diff file list, and the Phase 7 completion check refuses to advance unless an `e2e/*.spec.ts` (or `.spec.tsx`/`.spec.js`) file is part of the deliverable.
- `tests/test_sdlc_framework_compliance.py` — must-contain `def test_every_frontend_story_has_playwright_spec`, must-contain `def test_phase_runner_detects_frontend_stories`, must-contain `def test_acceptance_diff_gate_requires_e2e_for_frontend_stories`. These are framework contract tests — they iterate over `features/story-*/seed.md`, identify frontend stories, and assert each has the corresponding Playwright deliverable.
- `tests/deployment/test_phase_runner_frontend_enforcement.py` — NEW. must-contain `def test_phase_7_blocks_completion_when_frontend_lacks_playwright`, must-contain `def test_acceptance_diff_must_contain_e2e_when_tsx_in_diff`, must-contain `def test_keyword_detection_dashboard_ui_frontend`.
- `.github/workflows/test.yml` — must-contain `playwright`, must-contain `npx playwright install`, must-contain a job step that runs `npx playwright test --grep @smoke` when the frontend has changed. The job MUST gate merges (no `continue-on-error: true`).
- `.github/workflows/test.yml` — also must-contain a step that detects "is this a frontend PR" using `git diff --name-only origin/main...HEAD | grep -E '\.(tsx?|jsx?|css|scss|html)$|^frontend/|^e2e/'` and skips the Playwright job otherwise (don't run a 5-minute browser job on backend-only PRs).
- `tests/test_sdlc_framework_compliance.py` (additional) — must-contain `def test_test_workflow_has_playwright_job_gating_frontend_changes`. CI workflow itself is a contract.

### Skill changes (`.sdlc` submodule — written into features/ folder for Mark to apply)

- `features/story-542-sdlc-playwright-enforcement/skills/phase-1/SKILL.md.patch` — adds a "## Frontend Story Detection" section that mandates the Acceptance Diff list a Playwright spec when frontend files are listed; tightens the existing language so it's a MUST not a SHOULD.
- `features/story-542-sdlc-playwright-enforcement/skills/phase-7/SKILL.md.patch` — adds a "## Playwright Spec Required" section explicitly for frontend stories, with the contract: spec lives in `e2e/<feature-area>.spec.ts`, tagged `@smoke`, takes screenshots committed to `e2e/screenshots/`, uses `toHaveScreenshot()` baseline.
- `features/story-542-sdlc-playwright-enforcement/skills/phase-8/SKILL.md.patch` — adds language: "Phase 8 MUST run `npx playwright test --grep @smoke` and confirm GREEN before reporting complete for any frontend story."
- `features/story-542-sdlc-playwright-enforcement/MANUAL-STEPS.md` — instructions for Mark to apply the three patches in `.sdlc/skills/`, commit, push the submodule, then bump the submodule pointer in tech-dev-agents and any other project repo that uses `.sdlc`.

### Per-project bootstrap (tech-dev-agents only — others follow same pattern)

- `package.json` (or `frontend/package.json`) — must-contain `"@playwright/test"` in devDependencies. If already present, leave alone.
- `e2e/playwright.config.ts` — NEW if missing. must-contain `screenshot:`, must-contain `toHaveScreenshot` (so the baseline-screenshot mechanism is wired).
- `e2e/smoke.spec.ts` — NEW. Baseline Playwright spec tagged @smoke that passes on Day 1.

## Test Criteria

Every assertion below must have a pytest-level test (or vitest for the workflow detection) that fails RED before implementation and passes GREEN after.

1. **Frontend-story detection**
   - `_is_frontend_story(story_folder)` returns True when seed.md lists any `*.tsx` file in its Acceptance Diff. Test with a fixture seed listing `frontend/src/components/Foo.tsx`.
   - Returns True when seed.md text contains keywords "dashboard", "UI", "frontend", "render", "screen" (case-insensitive). Test with three fixture seeds, one per keyword.
   - Returns False for backend-only seeds (no .tsx/.css/.html in diff, no UI keywords). Test with 5 fixture seeds drawn from existing `features/story-5*/seed.md` to avoid mocks.

2. **Phase 7 completion gate**
   - When `_is_frontend_story` is True and no `e2e/*.spec.{ts,tsx,js}` file exists in the deliverable diff → Phase 7 raises a specific error: `"Phase 7 requires a Playwright spec under e2e/ for frontend stories"`.
   - When the spec exists → Phase 7 completes normally.
   - The gate runs in `_verify_deliverable` or wherever Phase 7's existing deliverable check lives — extend it, don't fork.

3. **Acceptance Diff gate**
   - The existing Acceptance Diff parser (in `sdlc_phase_runner.py`) MUST refuse to mark a frontend story complete unless the diff includes at least one `e2e/*.spec.{ts,tsx,js}` file. The error message must say "frontend story missing Playwright spec — add e2e/<feature>.spec.ts to Acceptance Diff."
   - Test: a fixture seed listing `frontend/src/Foo.tsx` but no `e2e/` file → gate fails with that exact error message.
   - Test: same seed + `e2e/foo.spec.ts` listed → gate passes.

4. **CI workflow runs Playwright on frontend PRs**
   - The Playwright job in `.github/workflows/test.yml` runs when the PR diff contains any frontend file. Test with a YAML-parsing assertion in `tests/test_sdlc_framework_compliance.py`: load the workflow, find the Playwright step, verify its `if:` condition matches frontend file globs.
   - The job is NOT marked `continue-on-error` — it gates merges. Test asserts the absence of that key.
   - The job runs `npx playwright install --with-deps chromium` (or equivalent) in a setup step, so browser binaries are present.

5. **Framework-compliance contract test**
   - `test_every_frontend_story_has_playwright_spec` iterates all `features/story-*/seed.md` files, identifies frontend stories via the same detection as the runtime gate, asserts each lists at least one `e2e/*.spec.*` file in its Acceptance Diff. Pre-2026-04-23 stories (mtime grandfathered) are skipped — the same grandfather pattern `test_every_seed_written_after_20260422_has_required_sections` already uses.

6. **Skill content verification (no submodule changes possible — verify in feature folder)**
   - `tests/test_sdlc_framework_compliance.py` MUST contain a test that verifies the *intended* skill changes are at least drafted in `features/story-542-*/skills/`. This guards against Mark forgetting to apply the manual step — the contract test fails until the corresponding `.sdlc` submodule pointer is bumped *and* the actual skill files contain the Playwright language. (This is a soft-fail handback; the test reads `.sdlc/skills/phase-7/SKILL.md` and grep-asserts the Playwright section is present. If the submodule isn't populated in CI, the test skips per the existing skip pattern.)

## Validation

After implementation lands and is deployed, and after Mark applies the manual `.sdlc` skill patches:

1. Open a PR that touches `frontend/src/components/AgentCard.tsx` but NOT `e2e/`. Expectations:
   - The Acceptance Diff gate (in the dispatched agent's Phase 8) refuses to mark complete with error `"frontend story missing Playwright spec"`.
   - CI on the PR runs the Playwright job (no e2e to run, so it should fail or be marked NEEDS attention).
   - The framework-compliance test in CI fails: `test_every_frontend_story_has_playwright_spec` flags this PR's seed.

2. Open a PR that touches `frontend/src/components/AgentCard.tsx` AND `e2e/dashboard.spec.ts`. Expectations:
   - All gates pass.
   - CI runs the Playwright job, executes the spec, screenshots are produced, baselines verified.
   - Merge succeeds.

3. Open a PR that touches only backend Python code. Expectations:
   - The Playwright job is SKIPPED (the workflow's `if:` condition correctly identifies a non-frontend PR).
   - Merge succeeds without browser tests.

4. Apply the same `.sdlc` submodule pointer bump to `advertising-amazon` repo. Open a frontend PR there. Expectations: same enforcement applies. (This is the cross-project goal: one source of truth for SDLC rules.)

5. Run `pytest tests/test_sdlc_framework_compliance.py -v` locally. Every test passes against the current state of `features/story-*/seed.md`.

## Implementation Notes for the SDK Agent

- The phase-runner detection function: keep it simple. Read seed.md, extract the Acceptance Diff file list (the parser already exists for must-contain tokens — reuse `_parse_acceptance_diff`), check file extensions and prefixes. For keyword detection, lowercase the seed text and scan for the 5 keywords. Return True if EITHER signal fires.
- The Acceptance Diff gate's frontend-spec check: append to the existing `_verify_acceptance_diff` flow. Don't fork the function.
- The CI workflow: the existing `python-tests` job stays as-is. ADD a new `playwright-frontend-tests` job that:
  - Has an `if:` condition: `${{ contains(github.event.pull_request.changed_files, '.tsx') || ... }}` — actual syntax may need a separate step that sets an output var.
  - Uses `actions/setup-node@v4` to install Node, then `npm ci`, then `npx playwright install --with-deps chromium`, then runs the smoke specs.
  - Uploads screenshots as artifacts so failures show what the browser saw.
- The `e2e/playwright.config.ts` file: keep it minimal. Just `testDir`, `use.baseURL` (point to the deployed URL or local dev server), `expect.toHaveScreenshot.maxDiffPixels` set to a reasonable threshold (~100), and `screenshot: 'only-on-failure'` for the run-time setting.
- For the skill patches: write them as proper unified diffs (`diff -u` format). Mark applies them with `git apply` from the `.sdlc/` directory. Reference STORY-540's MANUAL-STEPS pattern for the handback structure.
- Do NOT try to push the submodule directly — the agent doesn't have access. The Acceptance Diff gate for THIS story should account for the cross-org-submodule-not-pushable pattern: the gate accepts the work if the patch files are present in `features/story-542-*/skills/`, even though the actual `.sdlc/skills/*.md` files won't change in this PR.

Phase 7 writes the tests (RED). Phase 8 implements (GREEN). Phase 8 also updates `MARK-TODO.md` with the manual-step handback.

## Manual Steps (Mark)

Placeholder — the dispatched agent MUST write the actual `MANUAL-STEPS.md` covering:
1. Apply the three .patch files in `.sdlc/skills/{phase-1,phase-7,phase-8}/SKILL.md`
2. Commit + push the `.sdlc` submodule
3. Bump the submodule pointer in tech-dev-agents (and replicate to advertising-amazon and any other project repo)
4. Verify each project's CI workflow now has the Playwright gate
5. Open a test frontend PR in each project to confirm the gate fires
