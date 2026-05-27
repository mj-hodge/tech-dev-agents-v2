# Test Design — STORY-542
# Framework-Level Playwright Enforcement for Frontend Stories

**Phase:** 7  
**Date:** 2026-04-23  
**Author:** Devon (Phase 7 Principal Developer)  
**Status:** RED state confirmed — 17 FAIL / 3 FAIL / 1 SKIP across 2 test files  

---

## Summary

This story adds three enforcement surfaces to the SDLC:

1. **Detection helpers** in `sdlc_phase_runner.py` — 5 new functions identify frontend stories and Playwright spec paths
2. **Phase 7 gate** — inline check in `run_sdlc_phases` blocks completion when frontend story has no `e2e/*.spec.ts`
3. **Acceptance Diff gate extension** — `_verify_acceptance_diff` returns `(False, [...])` when frontend story is missing a Playwright spec in the diff
4. **CI job** — `playwright-frontend-tests` job in `test.yml` gates merges on Playwright smoke tests

Tests are split across two files:
- `tests/deployment/test_phase_runner_frontend_enforcement.py` — 18 tests (Group A), all new
- `tests/test_sdlc_framework_compliance.py` — 4 tests (Group B) appended to existing file

---

## Test Count Summary

| Group | File | Tests | RED | SKIP | PASS |
|-------|------|-------|-----|------|------|
| A — Phase runner detection + gates | `tests/deployment/test_phase_runner_frontend_enforcement.py` | 18 | 17 | 0 | 1* |
| B — Framework compliance | `tests/test_sdlc_framework_compliance.py` | 4 | 3 | 1 | 0 |
| **Total** | | **22** | **20** | **1** | **1** |

*A-15 (`test_acceptance_diff_gate_passes_when_playwright_spec_present`) passes because the existing `_verify_acceptance_diff` already returns `(True, [])` when all claimed Acceptance Diff files are present in the git diff — this is correct regression-guard behavior. Phase 8 adds the frontend-specific gate that this test also validates.

---

## Group A: Phase Runner Enforcement Tests

**File:** `tests/deployment/test_phase_runner_frontend_enforcement.py` (NEW)

### TestPathIsFrontend

| Test ID | Test Name | Assertion | RED Reason |
|---------|-----------|-----------|------------|
| A-1 | `test_path_is_frontend_tsx_extension` | `_path_is_frontend("frontend/src/components/Foo.tsx") is True` | `_path_is_frontend` not yet in `sdlc_phase_runner.py` |
| A-2 | `test_path_is_frontend_css_extension` | `_path_is_frontend("frontend/src/styles/main.css") is True` | Same |
| A-3 | `test_path_is_frontend_e2e_prefix` | `_path_is_frontend("e2e/dashboard.spec.ts") is True` | Same |
| A-4 | `test_path_is_frontend_backend_only_returns_false` | `_path_is_frontend("deployment/hermes/sdlc_phase_runner.py") is False` | Same |

### TestIsPlaywrightSpecPath

| Test ID | Test Name | Assertion | RED Reason |
|---------|-----------|-----------|------------|
| A-5 | `test_is_playwright_spec_path_valid` | `.spec.ts`, `.spec.tsx`, `.spec.js` under `e2e/` → True | `_is_playwright_spec_path` not yet implemented |
| A-6 | `test_is_playwright_spec_path_invalid` | `tests/test_foo.py`, `frontend/src/foo.spec.ts` → False | Same |

### TestIsFrontendStoryDetection

| Test ID | Test Name | Assertion | RED Reason |
|---------|-----------|-----------|------------|
| A-7 | `test_is_frontend_story_detects_tsx_in_acceptance_diff` | tmpdir with seed listing `frontend/src/Foo.tsx` → `_is_frontend_story()` True | `_is_frontend_story` not yet implemented |
| A-8 | `test_is_frontend_story_detects_keywords` (×4 params) | Seeds with "dashboard", "UI", "ui", "frontend" → `_is_frontend_story_from_seed_text()` True | `_is_frontend_story_from_seed_text` not yet implemented |
| A-9 | `test_is_frontend_story_backend_seed_returns_false` | Real story-537 seed (backend-only) → False | Same |
| A-10 | `test_keyword_regex_whole_word_boundary_no_false_positive` | Seed with no whole-word frontend keywords → False | Same |

### TestHasPlaywrightSpec

| Test ID | Test Name | Assertion | RED Reason |
|---------|-----------|-----------|------------|
| A-11 | `test_has_playwright_spec_returns_true_when_spec_exists` | tmpdir with `e2e/dashboard.spec.ts` → `_has_playwright_spec()` True | `_has_playwright_spec` not yet implemented |
| A-12 | `test_has_playwright_spec_returns_false_when_no_e2e_dir` | tmpdir with no `e2e/` → False | Same |

### TestGateLogic

| Test ID | Test Name | Assertion | RED Reason |
|---------|-----------|-----------|------------|
| A-13 | `test_phase_7_blocks_completion_when_frontend_lacks_playwright` | `_is_frontend_story` True + `_has_playwright_spec` False → gate preconditions fire | Both functions not yet implemented |
| A-14 | `test_acceptance_diff_gate_blocks_frontend_story_missing_playwright_spec` | `_verify_acceptance_diff` returns `(False, ["frontend story missing Playwright spec..."])` when git diff has no `e2e/*.spec.ts` | Extension not yet added to `_verify_acceptance_diff` |
| A-15 | `test_acceptance_diff_gate_passes_when_playwright_spec_present` | `_verify_acceptance_diff` returns `(True, [])` when `e2e/foo.spec.ts` in both seed and diff | **PASSES** (regression guard — existing logic already passes when required files are present) |

---

## Group B: Framework Compliance Test Additions

**File:** `tests/test_sdlc_framework_compliance.py` (ADDITIONS to existing file)

| Test ID | Test Name | Assertion | RED Reason |
|---------|-----------|-----------|------------|
| B-1 | `test_every_frontend_story_has_playwright_spec` | Iterate `features/story-*/seed.md` mtime ≥ 2026-04-23; frontend stories must list `e2e/*.spec.*` in Acceptance Diff | **SKIP** — `_is_frontend_story_from_seed_text` not yet implemented; test gracefully skips until Phase 8 |
| B-2 | `test_phase_runner_detects_frontend_stories` | `_path_is_frontend("frontend/Foo.tsx") is True`, `_path_is_frontend("deployment/runner.py") is False` | **FAIL** — `_path_is_frontend` not in module yet |
| B-3 | `test_acceptance_diff_gate_requires_e2e_for_frontend_stories` | `_is_playwright_spec_path("e2e/foo.spec.ts") is True` | **FAIL** — `_is_playwright_spec_path` not in module yet |
| B-4 | `test_test_workflow_has_playwright_job_gating_frontend_changes` | YAML-parses `test.yml`; asserts `playwright-frontend-tests` job exists, no `continue-on-error`, detect step writes `is_frontend=`, subsequent steps gated on `steps.detect.outputs.is_frontend` | **FAIL** — job does not exist in `test.yml` yet |

---

## RED State Confirmation

Run on 2026-04-23:

```
tests/deployment/test_phase_runner_frontend_enforcement.py: 17 FAIL, 1 PASS
tests/test_sdlc_framework_compliance.py (new tests only): 3 FAIL, 1 SKIP
```

All failures are `AssertionError: <function_name> not found in sdlc_phase_runner.py` or
`AssertionError: playwright-frontend-tests job missing from test.yml`. There are no
`SyntaxError`, `ModuleNotFoundError`, or `ImportError` failures — the test files import cleanly.

The 18 previously-passing tests in `tests/test_sdlc_framework_compliance.py` still pass (zero regressions).

---

## Implementation Notes for Phase 8

### 1. Add module-scope constants to `sdlc_phase_runner.py`

Near the top of the file (after imports, before the first function), add:

```python
_FRONTEND_EXTENSIONS = (".tsx", ".jsx", ".css", ".scss", ".html")
_FRONTEND_PREFIXES = ("frontend/", "e2e/")
_FRONTEND_KEYWORDS = re.compile(
    r"\b(dashboard|ui|frontend|render|screen)\b",
    re.IGNORECASE,
)
```

### 2. Add detection helpers

```python
def _path_is_frontend(path: str) -> bool:
    path = path.strip()
    if any(path.startswith(p) for p in _FRONTEND_PREFIXES):
        return True
    return path.endswith(_FRONTEND_EXTENSIONS)


def _is_playwright_spec_path(path: str) -> bool:
    return path.startswith("e2e/") and path.endswith((".spec.ts", ".spec.tsx", ".spec.js"))


def _is_frontend_story_from_seed_text(seed_text: str) -> bool:
    paths = _parse_acceptance_diff(seed_text) or []
    if any(_path_is_frontend(p) for p in paths):
        return True
    return bool(_FRONTEND_KEYWORDS.search(seed_text))


def _is_frontend_story(workdir: str, story_folder: str) -> bool:
    # Read seed via folder-based lookup (prefer exact folder name match)
    seed_path = os.path.join(workdir, "features", story_folder, "seed.md")
    if not os.path.isfile(seed_path):
        return False
    try:
        with open(seed_path) as f:
            seed_text = f.read()
    except Exception:
        return False
    return _is_frontend_story_from_seed_text(seed_text)


def _has_playwright_spec(workdir: str, story_folder: str) -> bool:
    e2e_dir = os.path.join(workdir, "e2e")
    if not os.path.isdir(e2e_dir):
        return False
    for root, _dirs, files in os.walk(e2e_dir):
        for f in files:
            if f.endswith((".spec.ts", ".spec.tsx", ".spec.js")):
                return True
    return False
```

### 3. Phase 7 gate inline in `run_sdlc_phases`

After `_verify_deliverable` check for Phase 7, add:

```python
if phase_num == 7 and _is_frontend_story(workdir, story_folder):
    if not _has_playwright_spec(workdir, story_folder):
        print(
            "Phase 7 requires a Playwright spec under e2e/ for frontend stories",
            flush=True,
        )
        return False, None
```

### 4. Acceptance Diff gate extension in `_verify_acceptance_diff`

After the `token_misses` check (line ~1254), append the frontend-spec check:

```python
if _is_frontend_story_from_seed_text(seed_text):
    spec_in_diff = any(_is_playwright_spec_path(p) for p in changed)
    spec_in_seed = any(_is_playwright_spec_path(p) for p in (required or []))
    if not spec_in_diff or not spec_in_seed:
        return False, [
            "frontend story missing Playwright spec — add "
            "e2e/<feature>.spec.ts to Acceptance Diff."
        ]
```

### 5. CI job in `.github/workflows/test.yml`

Add a third job `playwright-frontend-tests` (see `feature-spec.md §5.2`). The job must:
- Have NO `continue-on-error: true`
- Contain a `detect` step that writes `is_frontend=true/false` to `$GITHUB_OUTPUT`
- Gate subsequent steps with `if: steps.detect.outputs.is_frontend == 'true'`
- Run `npx playwright install --with-deps chromium`
- Run `npx playwright test --grep @smoke`

### 6. Bootstrap files

Add `e2e/playwright.config.ts`, `e2e/smoke.spec.ts`, and update `package.json`
per `feature-spec.md §6`.

---

## Files Changed in This Phase (Phase 7 — Test Design Only)

| File | Change |
|------|--------|
| `tests/deployment/test_phase_runner_frontend_enforcement.py` | NEW — 18 tests (Group A) |
| `tests/test_sdlc_framework_compliance.py` | MODIFIED — 4 new tests appended (Group B) |
| `features/story-542-sdlc-playwright-enforcement/test-design.md` | NEW — this file |

No production code was modified in Phase 7.
