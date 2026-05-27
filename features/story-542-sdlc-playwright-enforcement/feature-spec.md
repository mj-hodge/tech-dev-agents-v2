# Feature Spec — STORY-542
# Framework-Level Playwright Enforcement for Frontend Stories

**Scope:** Medium  
**Selected approach (Phase 4):** Approach B — Full Dual-Gate (Seed-Aligned)  
**Weighted score:** 4.10/5  
**Phase path:** 1 → 4 → 6 → 7 → 8

---

## 1. Problem Recap

The SDLC framework recommends Playwright for frontend stories but doesn't enforce it. STORY-513 shipped frontend changes with only vitest unit tests; the UI result didn't match Mark's mental model. STORY-541 had to add Playwright requirements to its own seed because no part of the framework forced it. Mark's directive: **"playwright is part of the core SDLC — spec whatever needs to happen so we're consistent in checking that for all front end tests, for all tickets, for all projects."**

---

## 2. Architecture Overview

This story modifies **three surfaces** that together produce structural enforcement:

| Surface | File(s) | Role |
|---------|---------|------|
| **Runtime phase runner** | `deployment/hermes/sdlc_phase_runner.py` | Detects frontend stories and blocks Phase 7 and Acceptance Diff completion when Playwright specs are absent |
| **CI workflow** | `.github/workflows/test.yml` | Runs Playwright smoke tests on frontend PRs as a hard merge gate |
| **Framework contract tests** | `tests/test_sdlc_framework_compliance.py`, `tests/deployment/test_phase_runner_frontend_enforcement.py` | Iterate `features/story-*/seed.md`, assert every frontend story lists a Playwright spec; assert workflow YAML contains the correct gate |
| **Skill patches** | `features/story-542-*/skills/{phase-1,phase-7,phase-8}/SKILL.md.patch` + `MANUAL-STEPS.md` | Delivered as unified diffs for Mark to apply to the `.sdlc` submodule (agent cannot push cross-org) |
| **Project bootstrap** | `e2e/playwright.config.ts`, `e2e/smoke.spec.ts`, `package.json` | Minimum viable Playwright installation so the hard CI gate does not immediately fail on empty `e2e/` |

### 2.1 Domain Boundaries

| Domain | Responsibility | Owns |
|--------|---------------|------|
| **Detection** | Identify frontend stories from seed.md signals | `_is_frontend_story(story_folder) -> bool` |
| **Phase 7 Gate** | Block phase advance when deliverable is incomplete | Extension of `_verify_deliverable` via `_verify_phase7_deliverable(story_folder)` wrapper |
| **Acceptance Diff Gate** | Block Complete when PR diff lacks required files | Extension of `_verify_acceptance_diff` — appends frontend-spec check |
| **CI Enforcement** | Block merges when Playwright smoke fails | `playwright-frontend-tests` job in `test.yml` |
| **Framework Compliance** | Assert cross-story contract at build time | Tests in `test_sdlc_framework_compliance.py` |
| **Skill Propagation** | Push detection contract to `.sdlc` submodule | `.patch` files + `MANUAL-STEPS.md` |

**No circular dependencies.** Detection is a pure function; Gates call Detection; Compliance tests call Detection (via module import) and parse the workflow YAML; CI runs Playwright directly.

---

## 3. Detection Function Design

### 3.1 Signature

```python
def _is_frontend_story(workdir: str, story_folder: str) -> bool:
    """Detect whether a story requires a Playwright spec.

    Returns True if EITHER signal fires:

      1. File-signal: seed.md's ## Acceptance Diff lists any path whose
         extension is one of (.tsx, .jsx, .css, .scss, .html) OR whose
         prefix is one of ('frontend/', 'e2e/').

      2. Keyword-signal: seed.md body text (case-insensitive) contains
         any of 'dashboard', 'ui', 'frontend', 'render', 'screen'.
         Matched as whole-word tokens to avoid false positives like
         'screening' or 'dashboard-agnostic-word'.

    Returns False when seed.md cannot be read (missing, unreadable) —
    fail-open for the detection itself; the Acceptance Diff gate will
    still fire for stories with no seed file.

    Pure function: no side effects. Reused by both phase-7 and
    acceptance-diff gates, and by the framework-compliance tests.
    """
```

### 3.2 Keyword Matching — Whole-Word Tokens

Use `\b` word boundaries in regex. Rationale:
- Naive substring matching produces false positives (`dashboard` matches `dashboard-agnostic`; `ui` matches `guide`, `build`, `quit`, `require`).
- Case-insensitive flag only (no stemming/lemmatization — no NLP dependency).

```python
_FRONTEND_KEYWORDS = re.compile(
    r"\b(dashboard|ui|frontend|render|screen)\b",
    re.IGNORECASE,
)
```

### 3.3 File-Extension Rule

```python
_FRONTEND_EXTENSIONS = (".tsx", ".jsx", ".css", ".scss", ".html")
_FRONTEND_PREFIXES = ("frontend/", "e2e/")

def _path_is_frontend(path: str) -> bool:
    path = path.strip()
    if any(path.startswith(p) for p in _FRONTEND_PREFIXES):
        return True
    return path.endswith(_FRONTEND_EXTENSIONS)
```

### 3.4 Implementation

```python
def _is_frontend_story(workdir: str, story_folder: str) -> bool:
    seed_text = _read_seed_for_story_by_folder(workdir, story_folder)
    if seed_text is None:
        return False
    # File-signal
    paths = _parse_acceptance_diff(seed_text) or []
    if any(_path_is_frontend(p) for p in paths):
        return True
    # Keyword-signal (scan whole seed body; keywords anywhere qualify)
    if _FRONTEND_KEYWORDS.search(seed_text):
        return True
    return False
```

Helper: `_read_seed_for_story_by_folder` follows the same broad-match pattern as the existing `_read_seed_for_story` but accepts a `story_folder` (already-slugged) as input — reuses the existing file-locator logic. If the `story_folder` shape already matches `story-NNN-*`, a direct read is attempted first. (Keep a single helper; do not duplicate locator logic.)

---

## 4. Gate Integration Points

### 4.1 Phase 7 Deliverable Gate

The existing phase-loop calls `_verify_deliverable(workdir, story_folder, deliverable)` at line 1657 (post-SDK). For Phase 7 of a frontend story, the deliverable `test-design.md` alone is insufficient — a Playwright spec must also exist.

**Design:** Add a wrapper that, after the base deliverable check for Phase 7 succeeds, performs a frontend-spec existence check. Do NOT modify `_verify_deliverable` itself (it's used by all phases); add the check inline in the phase loop.

```python
# In run_sdlc_phases phase loop, after the post-run _verify_deliverable check:
if phase_num == 7 and _is_frontend_story(workdir, story_folder):
    if not _has_playwright_spec(workdir, story_folder):
        print(
            f"[DISPATCH] Phase 7 ({phase_name}) for {story_id} — frontend story "
            f"detected but no e2e/*.spec.{{ts,tsx,js}} file exists. Phase 7 "
            f"requires a Playwright spec under e2e/ for frontend stories.",
            flush=True,
        )
        _notify_teams(
            f"Phase 7 FAILED for {story_id}: frontend story missing "
            f"Playwright spec — add e2e/<feature>.spec.ts and re-run."
        )
        return False, None
```

**Helper:**
```python
def _has_playwright_spec(workdir: str, story_folder: str) -> bool:
    """True if any e2e/*.spec.{ts,tsx,js} file exists on disk."""
    e2e_dir = os.path.join(workdir, "e2e")
    if not os.path.isdir(e2e_dir):
        return False
    for root, _dirs, files in os.walk(e2e_dir):
        for f in files:
            if f.endswith((".spec.ts", ".spec.tsx", ".spec.js")):
                return True
    return False
```

### 4.2 Acceptance Diff Gate

Extend `_verify_acceptance_diff` (at line 1148). After the existing file-presence and token-presence checks pass, add a final frontend-spec check that consults both the seed's Acceptance Diff (what was *claimed*) AND the actual git diff (what was *shipped*).

**Error message contract** (from seed.md §3):
> "frontend story missing Playwright spec — add e2e/<feature>.spec.ts to Acceptance Diff."

**Extension:**
```python
# At the tail of _verify_acceptance_diff, after token_misses check:
# STORY-542: frontend stories must include at least one e2e/*.spec.{ts,tsx,js}
# file in the PR diff (and the seed should claim it too; the file-presence
# check above catches the seed-claim case; this final check catches the
# "seed claimed, PR dropped" case and the "seed forgot to claim" case).
if _is_frontend_story_from_seed_text(seed_text):
    spec_in_diff = any(
        _is_playwright_spec_path(p) for p in changed
    )
    spec_in_seed = any(
        _is_playwright_spec_path(p) for p in (required or [])
    )
    if not spec_in_diff:
        return False, [
            "frontend story missing Playwright spec — add "
            "e2e/<feature>.spec.ts to Acceptance Diff."
        ]
    if not spec_in_seed:
        return False, [
            "frontend story missing Playwright spec — add "
            "e2e/<feature>.spec.ts to Acceptance Diff."
        ]
```

**Helpers:**
```python
def _is_playwright_spec_path(path: str) -> bool:
    return path.startswith("e2e/") and path.endswith((".spec.ts", ".spec.tsx", ".spec.js"))

def _is_frontend_story_from_seed_text(seed_text: str) -> bool:
    """Same logic as _is_frontend_story but operates on already-read seed text
    — avoids re-reading the seed inside _verify_acceptance_diff (which already
    has the text in scope).
    """
    paths = _parse_acceptance_diff(seed_text) or []
    if any(_path_is_frontend(p) for p in paths):
        return True
    return bool(_FRONTEND_KEYWORDS.search(seed_text))
```

### 4.3 Gate Ordering Decision

Both gates consult the same detection logic (extracted to `_is_frontend_story_from_seed_text` for in-function reuse; `_is_frontend_story` is the public entrypoint for compliance tests and the Phase 7 gate). **The detection logic lives in ONE place** — both gates call the same extraction/matching helpers. This satisfies the Analysis Phase risk mitigation (R-C-2): no off-by-one regex mismatch between gates.

---

## 5. CI Workflow Design

### 5.1 Current State

`.github/workflows/test.yml` has two jobs:
- `python-tests` — hard gate (no `continue-on-error`), runs curated contract-test set
- `python-tests-full` — soft gate (`continue-on-error: true`), runs everything else

### 5.2 New Job: `playwright-frontend-tests`

Added as a third job, **hard gate** (no `continue-on-error`). Gated by a detect-step output variable so the job only executes for PRs with frontend changes.

```yaml
  playwright-frontend-tests:
    name: Playwright smoke tests (frontend PRs)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    # NOTE: no continue-on-error — this job MUST gate merges for frontend PRs.
    # The compliance test test_test_workflow_has_playwright_job_gating_frontend_changes
    # asserts the absence of this key.
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: false
          fetch-depth: 0   # need origin/main for the diff-based detection

      - name: Detect frontend changes
        id: detect
        run: |
          # Identify frontend changes relative to origin/main. On push-to-main
          # events there is no diff to compute — we then default to "skip"
          # since the hard gate is only meaningful on PRs.
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            BASE="origin/${{ github.event.pull_request.base.ref }}"
          else
            echo "is_frontend=false" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          CHANGED=$(git diff --name-only "$BASE"...HEAD || true)
          if echo "$CHANGED" | grep -E '\.(tsx?|jsx?|css|scss|html)$|^frontend/|^e2e/' >/dev/null; then
            echo "is_frontend=true" >> "$GITHUB_OUTPUT"
          else
            echo "is_frontend=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Set up Node
        if: steps.detect.outputs.is_frontend == 'true'
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        if: steps.detect.outputs.is_frontend == 'true'
        run: npm ci

      - name: Install Playwright browsers
        if: steps.detect.outputs.is_frontend == 'true'
        run: npx playwright install --with-deps chromium

      - name: Run Playwright smoke tests
        if: steps.detect.outputs.is_frontend == 'true'
        run: npx playwright test --grep @smoke

      - name: Upload Playwright report
        if: steps.detect.outputs.is_frontend == 'true' && always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: |
            playwright-report/
            e2e/screenshots/
          if-no-files-found: ignore
          retention-days: 14
```

### 5.3 Why Step-Level `if:` (Not Job-Level)

Job-level `if:` on a hard-gated job with `needs:` chains propagates "skipped" as "success" — correct. But we want the workflow to show a single job entry that either runs tests or no-ops cleanly on backend PRs. Putting the detect step at the top and guarding every subsequent step with the output var achieves this without hiding the job from the required-checks list (important for branch protection: the check name remains stable).

### 5.4 CI Condition Contract Test

To mitigate R-B-2 (malformed output-variable silently evaluates false-negative), a compliance test parses the YAML and asserts:
- The detect step writes `is_frontend=` to `$GITHUB_OUTPUT`.
- At least one Playwright-relevant step's `if:` references `steps.detect.outputs.is_frontend == 'true'`.
- The job does NOT contain `continue-on-error: true`.

---

## 6. Playwright Project Bootstrap

### 6.1 `e2e/playwright.config.ts` (NEW)

```typescript
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  // Single browser target for the smoke gate. Adding Firefox/WebKit is a
  // follow-on decision; smoke must be fast.
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  use: {
    // Default base URL points at the local dev server. Individual specs
    // can override via page.goto(absoluteURL). CI jobs that need a
    // deployed URL can export PLAYWRIGHT_BASE_URL.
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  expect: {
    toHaveScreenshot: {
      // Tolerate minor antialiasing differences across OS/browser versions.
      maxDiffPixels: 100,
    },
  },
  reporter: [['html', { outputFolder: 'playwright-report', open: 'never' }]],
  // No baseline screenshots committed on Day 1 — toHaveScreenshot() is wired
  // but specs opt-in by calling it explicitly. First spec that calls it must
  // commit the generated e2e/screenshots/ baseline in the same PR.
});
```

### 6.2 `e2e/smoke.spec.ts` (NEW — Day 1 passing spec)

Required to mitigate R-B-1 (hard gate on empty `e2e/` would fail every PR forever). Minimal content:

```typescript
import { test, expect } from '@playwright/test';

// Tagged @smoke so the CI grep picks it up.
test('@smoke framework enforcement baseline — e2e directory exists and config loads', async () => {
  // This is a no-network test: proves Playwright can parse the config, load
  // a test, and report success. More substantive smoke tests (that launch
  // the ops console, sign in, load the dashboard) are added by the stories
  // that actually ship those frontends.
  expect(true).toBe(true);
});
```

### 6.3 `package.json` Additions

```json
{
  "devDependencies": {
    "@playwright/test": "^1.47.0"
  },
  "scripts": {
    "e2e": "playwright test",
    "e2e:smoke": "playwright test --grep @smoke"
  }
}
```

**Decision:** `package.json` at repo root (not `frontend/package.json`). Rationale:
- tech-dev-agents currently has no `frontend/` directory (ops-console frontend lives elsewhere).
- A root-level `package.json` with `e2e/` colocated is the simplest layout for a repo that is primarily Python but needs Node-based browser tests.
- Other projects (advertising-amazon) may already have `frontend/package.json`; their MANUAL-STEPS will handle the nesting.

---

## 7. Skill Patches (For Mark)

Delivered as unified diffs in `features/story-542-sdlc-playwright-enforcement/skills/{phase-1,phase-7,phase-8}/SKILL.md.patch`. Mark applies with `git apply` from inside the `.sdlc/` directory.

### 7.1 `phase-1/SKILL.md.patch` — Seed Contract

Adds a "## Frontend Story Detection" section mandating that any seed whose story involves frontend work MUST list at least one `e2e/*.spec.{ts,tsx,js}` file in its Acceptance Diff. Tightens "SHOULD" → "MUST" in existing phrasing.

### 7.2 `phase-7/SKILL.md.patch` — Test Design Contract

Adds "## Playwright Spec Required (Frontend Stories)" section specifying:
- Spec file lives at `e2e/<feature-area>.spec.ts`
- Tagged `@smoke`
- Uses `toHaveScreenshot()` with committed baselines (when UI is stable)
- Screenshots stored at `e2e/screenshots/`

### 7.3 `phase-8/SKILL.md.patch` — Implementation Gate

Adds: "Phase 8 MUST run `npx playwright test --grep @smoke` and confirm GREEN before reporting complete for any frontend story."

### 7.4 `MANUAL-STEPS.md`

Step-by-step Markdown handback to Mark (reference STORY-540's MANUAL-STEPS pattern):
1. `cd .sdlc && git apply features/story-542-sdlc-playwright-enforcement/skills/phase-1/SKILL.md.patch`
2. Repeat for phase-7, phase-8
3. `git -C .sdlc add -A && git -C .sdlc commit -m "..." && git -C .sdlc push`
4. Bump submodule pointer in tech-dev-agents: `git submodule update --remote .sdlc && git add .sdlc && git commit`
5. Replicate submodule bump to advertising-amazon and any other consuming repo
6. Verify each project's CI now runs the Playwright gate
7. Open a test frontend PR in each project to confirm the gate fires

---

## 8. Framework Compliance Tests (Phase 7 deliverables)

All tests live in `tests/test_sdlc_framework_compliance.py` and `tests/deployment/test_phase_runner_frontend_enforcement.py`.

### 8.1 `tests/test_sdlc_framework_compliance.py` — New Tests

| Test name | Assertion |
|-----------|-----------|
| `test_every_frontend_story_has_playwright_spec` | Iterate `features/story-*/seed.md`. For each whose mtime ≥ 2026-04-23 00:00Z AND is detected as frontend, assert at least one `e2e/*.spec.*` path appears in its Acceptance Diff. Pre-cutoff seeds grandfathered. |
| `test_phase_runner_detects_frontend_stories` | Import `_is_frontend_story` from `sdlc_phase_runner`. Feed it a temp-dir fixture set of seed files; assert detection results match expectations. |
| `test_acceptance_diff_gate_requires_e2e_for_frontend_stories` | Feed `_verify_acceptance_diff` a fixture seed + fake git diff; assert the gate returns `(False, ["frontend story missing Playwright spec..."])` when expected. |
| `test_test_workflow_has_playwright_job_gating_frontend_changes` | YAML-parse `.github/workflows/test.yml`. Assert: `playwright-frontend-tests` job exists; `continue-on-error` is NOT set (or is False); at least one step uses `actions/setup-node`; at least one step runs `npx playwright install`; detect step writes `is_frontend=` to `$GITHUB_OUTPUT`; subsequent step has `if: steps.detect.outputs.is_frontend == 'true'`. |

**Grandfather cutoff:** Same `2026-04-23 00:00Z` cutoff used by the existing `test_every_seed_written_after_20260422_has_required_sections`. This keeps a single, well-understood grandfathering rule across the compliance suite.

### 8.2 `tests/deployment/test_phase_runner_frontend_enforcement.py` — New File

| Test name | Assertion |
|-----------|-----------|
| `test_phase_7_blocks_completion_when_frontend_lacks_playwright` | Build a temp workdir with a seed listing `frontend/src/Foo.tsx` and a test-design.md but NO `e2e/*.spec.ts`. Run the relevant detection + gate logic. Assert gate returns a block with the expected error. |
| `test_acceptance_diff_must_contain_e2e_when_tsx_in_diff` | Mock `subprocess.run` for `git diff` to return a file list that includes `frontend/src/Foo.tsx` but no `e2e/*.spec.ts`. Assert `_verify_acceptance_diff` returns `(False, ["frontend story missing Playwright spec..."])`. |
| `test_keyword_detection_dashboard_ui_frontend` | Parameterize three fixture seeds (one containing each of "dashboard", "UI", "frontend" — case-insensitive). Assert `_is_frontend_story` returns True for each. |
| `test_backend_only_story_not_flagged_as_frontend` | Parameterize 5 fixture seeds drawn from existing `features/story-5*/seed.md` that contain no frontend files or keywords. Assert `_is_frontend_story` returns False. |
| `test_detection_regex_whole_word_boundary` | Fixture seed containing "screening" (no match) and "screen" (match). Assert the whole-word boundary actually works. Guards against false positives. |

### 8.3 Skill-Content Soft-Gate Test

Per seed.md §5.6, a soft-fail test asserts the `.sdlc/skills/{phase-1,phase-7,phase-8}/SKILL.md` files contain Playwright language once the submodule is populated. If the submodule isn't populated in CI, the test skips (existing `SKILLS_DIR.exists()` pattern). Test name: `test_sdlc_skills_mandate_playwright_for_frontend_stories`.

---

## 9. Error Handling Design

No HTTP surface here; errors are phase-runner log lines + Teams notifications + return-False from the phase loop.

| Error case | Detection | Reported as | Follow-up |
|------------|-----------|-------------|-----------|
| seed.md missing / unreadable | `_is_frontend_story` returns False | Detection logs nothing; gates don't fire | Story proceeds; existing seed-required gate catches it elsewhere |
| Phase 7 deliverable present, Playwright spec missing | `_has_playwright_spec` returns False | `[DISPATCH] Phase 7 ... frontend story detected but no e2e/*.spec.{ts,tsx,js}` | `_notify_teams` fires; `return False, None` aborts remaining phases |
| Acceptance Diff missing e2e spec | `_verify_acceptance_diff` returns `(False, [...])` | Missing list printed with the existing `[DISPATCH] {story_id} Acceptance Diff FAILED` log; `_emit_event("acceptance_diff_fail", ...)` | Teams notified with exact error string; story auto-retries per existing behavior |
| CI detect step finds no frontend changes | `is_frontend=false` output var | Subsequent Playwright steps skip; job reports success; merge unblocked | None |
| CI Playwright step fails | `npx playwright test` non-zero exit | Job fails; merge blocked by branch protection | Author reads Playwright report artifact; fixes spec; re-pushes |

**Fallback behavior:** Detection fails-open (missing seed → not frontend) so an agent with no seed doesn't get blocked on an unrelated requirement. The gates fail-closed (block Complete) when detection says frontend AND spec is missing — this is the intended enforcement surface.

**No secrets or stack traces are logged or exposed.** The error messages are all seed-relative paths or contract strings.

---

## 10. Shared Utilities

| Utility | File | Purpose | Reused by |
|---------|------|---------|-----------|
| `_is_frontend_story(workdir, story_folder)` | `deployment/hermes/sdlc_phase_runner.py` | Public detection entry point | Phase 7 gate, compliance tests |
| `_is_frontend_story_from_seed_text(seed_text)` | same | Private detection using already-read seed text | `_verify_acceptance_diff` (avoids re-read) |
| `_has_playwright_spec(workdir, story_folder)` | same | File-existence check for e2e spec | Phase 7 gate |
| `_is_playwright_spec_path(path)` | same | Path-shape check (e2e/*.spec.{ts,tsx,js}) | Acceptance Diff gate |
| `_path_is_frontend(path)` | same | Extension/prefix check | Both detection helpers |

**No duplication.** The regex and extension tuple live once at module scope. Both detection helpers reuse the same tuple and compiled regex. Both gates call the same detection helpers.

---

## 11. Failure Modes Table

| Dependency | Unavailable Behavior | Rationale |
|------------|---------------------|-----------|
| seed.md file | `_is_frontend_story` returns False (fail-open) | A missing seed is already caught by the existing `_read_seed_for_story` path; this check should not duplicate that surface |
| `git diff origin/main` | `_verify_acceptance_diff` returns `(True, [])` (fail-open, existing behavior) | Unchanged — STORY-528 precedent: git-misconfig should not block Complete |
| `.sdlc/skills/` submodule (in CI) | `test_sdlc_skills_mandate_playwright_for_frontend_stories` skips | Submodule is private cross-org; default GITHUB_TOKEN cannot clone. Existing `SKILLS_DIR.exists()` skip pattern |
| Playwright browser binaries | `npx playwright install` step fails CI | Hard gate — this is the intended behavior; binaries are always installed fresh in CI |
| Node/npm in CI | `actions/setup-node` step fails CI | Hard gate — unambiguous failure mode |

---

## 12. Implementation Plan

### Build order

1. **Detection + helpers** (`_path_is_frontend`, `_FRONTEND_KEYWORDS`, `_FRONTEND_EXTENSIONS`, `_is_frontend_story`, `_is_frontend_story_from_seed_text`, `_has_playwright_spec`, `_is_playwright_spec_path`, `_read_seed_for_story_by_folder`) in `sdlc_phase_runner.py`
2. **Phase 7 gate integration** — inline in `run_sdlc_phases` phase loop
3. **Acceptance Diff gate extension** — append to `_verify_acceptance_diff`
4. **`e2e/` bootstrap** — `playwright.config.ts` + `smoke.spec.ts` + `package.json` devDep
5. **CI workflow update** — add `playwright-frontend-tests` job
6. **Framework compliance tests** — add 4 new tests in `test_sdlc_framework_compliance.py` + 5 in `tests/deployment/test_phase_runner_frontend_enforcement.py`
7. **Skill patches + MANUAL-STEPS.md** — author the three `.patch` files and handback doc

### Order rationale

Steps 1–3 are the runtime enforcement path; step 4 is the prerequisite for the CI hard gate; step 5 wires CI; step 6 locks everything in as compliance contracts; step 7 propagates to the `.sdlc` submodule via Mark's manual step. Steps 6 and 7 must both land in the same PR — the test file references the expected workflow structure, so it must be the new structure, not the old.

### Acceptance Diff for Phase 8 (preview)

The Phase 8 agent's Acceptance Diff must include exactly the files listed in seed.md §2 (Code in this repo) + skill patches + playwright config + smoke spec + package.json. The existing must-contain token check will verify `_is_frontend_story`, `playwright`, `e2e/` appear in `sdlc_phase_runner.py`, and verify the three test function names appear in the compliance test file.

---

## 13. Decisions Locked (carried forward from Phase 4 + new)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D-1 | Gate placement | Both `_verify_deliverable` (Phase 7 inline) AND `_verify_acceptance_diff` | Dual-layer enforcement — Acceptance Diff is the harder backstop |
| D-2 | CI enforcement level | Hard gate (no `continue-on-error`) | Seed contract test requires absence of the key |
| D-3 | CI condition pattern | Detect-step → `$GITHUB_OUTPUT` → `if: steps.detect.outputs.is_frontend == 'true'` on subsequent steps | Stable job entry in required-checks list; contract test validates the pattern |
| D-4 | Playwright config location | `e2e/playwright.config.ts` collocated with specs | Seed spec and skill guidance both reference `e2e/` as spec home |
| D-5 | Smoke spec pre-commit | Required in same PR (`e2e/smoke.spec.ts`) | Prevents Day-1 CI breakage on empty `e2e/` |
| D-6 | Keyword matching | Whole-word regex with `\b` boundaries + case-insensitive | Avoids false positives like "screening" matching "screen" |
| D-7 | Detection fallback | Fail-open when seed unreadable | Missing-seed is an orthogonal failure caught elsewhere |
| D-8 | `package.json` location | Repo root (not `frontend/`) | tech-dev-agents has no `frontend/` dir today; simplest viable layout |
| D-9 | Detection reuse | Single module-scope regex + extension tuple used by both gates and compliance tests | Mitigates R-C-2 (divergent detection in two call sites) |
| D-10 | Grandfather cutoff | `2026-04-23 00:00Z` (matches existing rule) | Single well-understood cutoff avoids confusion |
| D-11 | Cross-project propagation | MANUAL-STEPS.md in feature folder + submodule pointer bump | Agent cannot push `.sdlc` cross-org; Mark applies manually |

---

## 14. Cross-Project Submodule Propagation Risk

Per Phase 4 Risk Register cross-cutting note: the `.sdlc` skill patches rely on Mark to (a) apply the patches, (b) push `.sdlc`, (c) bump the submodule pointer in every consuming repo. **No automated mitigation in this story's scope** — it is deliberately handed off via `MANUAL-STEPS.md`. Phase 9/10 (out of scope for Medium) would add a submodule-currency contract test to each consuming repo.

**What this story DOES provide** for propagation safety: the compliance test `test_sdlc_skills_mandate_playwright_for_frontend_stories` (soft-fails with skip when `.sdlc` is not populated in CI) converts to a hard-fail in any environment that DOES have `.sdlc` populated but lacks the Playwright language — catching the "Mark forgot to push `.sdlc`" case the next time `tech-dev-agents` CI runs locally with the submodule cloned.

---

## 15. Out of Scope / Follow-ups

- **Visual-regression baselines.** `toHaveScreenshot()` is wired in config but no baselines are committed. First frontend story to call it will own the baseline-commit lift.
- **Multi-browser targets** (Firefox, WebKit). Chromium-only for smoke.
- **Submodule-currency contract test** per consuming repo (mentioned above — not in this story's scope).
- **Deploy-target `baseURL`.** Config defaults to localhost. Stories that need to hit a deployed URL can export `PLAYWRIGHT_BASE_URL` at CI time.

---

## 16. Phase 6 Gate Checklist

- [x] Simplicity Test: a new developer reading this spec understands the change in <15 min (detection func + two gate hooks + one CI job + 9 tests)
- [x] No interfaces with single implementers
- [x] No abstractions created for single use cases
- [x] Detection logic lives in ONE place (mitigates R-C-2)
- [x] Error Handling Design section present (§9)
- [x] Failure Modes Table present (§11)
- [x] Shared Utilities enumerated (§10)
- [x] CI condition contract test specified (§5.4, §8.1)
- [x] Grandfather cutoff aligned with existing rule (§8.1)
- [x] No new HTTP endpoints → HTTP semantics table and RFC 7807 not applicable
- [x] No new observability surface → Prometheus/metrics checklist not applicable (framework-internal logging via existing `[DISPATCH]` pattern)
