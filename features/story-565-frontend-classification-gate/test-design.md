# Test Design: STORY-565 — Frontend Classification + Playwright @smoke Gate

**Phase:** 7 (Test Design)
**Scope:** Medium
**Date:** 2026-04-24

---

## 1. Test Modules

### 1.1 `tests/deployment/test_phase_runner_frontend_smoke_gate.py` (new)

Unit tests for `_verify_frontend_gate` — the phase-runner gate that blocks
completion for `Frontend: true` stories missing a `@smoke`-tagged Playwright
spec. All tests are mock-only: no real git, no real `/complete` call.

**Import strategy:** Same as `test_phase_runner_frontend_enforcement.py` —
load `sdlc_phase_runner.py` via `importlib.util.spec_from_file_location`.

**Mock strategy:** `unittest.mock.patch("subprocess.run")` intercepts all
git subprocess calls. Each test provides a side_effect function that returns
canned responses for `git diff origin/main --name-only` and
`git show HEAD:<path>`.

### 1.2 `tests/test_sdlc_framework_compliance.py` (update)

One new test function:
`test_every_seed_dispatched_after_20260426_declares_frontend_classification`

Reuses existing `_git_first_commit_timestamp()` helper for grandfathering.

---

## 2. Test Cases (A–F)

| ID | Name | Seed Field | Diff State | @smoke? | Expected | Validates |
|----|------|-----------|------------|---------|----------|-----------|
| A | `test_frontend_true_with_smoke_spec_passes` | `frontend: true` | `e2e/dashboard/login.smoke.spec.ts` | Yes | PASS (True, []) | SC-3: diff with matching spec → complete proceeds |
| B | `test_frontend_true_no_spec_fails` | `frontend: true` | No `e2e/**/*.spec.ts` | N/A | FAIL (False, [...]) | SC-2: no spec → blocked |
| C | `test_frontend_true_spec_without_smoke_tag_fails` | `frontend: true` | `e2e/dashboard/login.spec.ts` | No | FAIL (False, [...]) | SC-2: spec without @smoke → blocked |
| D | `test_frontend_false_passes` | `frontend: false` | None | N/A | PASS (True, []) | SC-4, SC-6: false → short-circuit, no diff scan |
| E | `test_frontend_false_with_rationale_passes` | `frontend: false # CSS-only refactor` | None | N/A | PASS (True, []) | SC-4: escape hatch |
| F | `test_missing_frontend_field_passes_with_warning` | *(missing)* | Any | N/A | PASS (True, []) + warning | SC-5: grandfathered seed fail-open |

---

## 3. Compliance Test

| ID | Name | What it checks |
|----|------|---------------|
| G | `test_every_seed_dispatched_after_20260426_declares_frontend_classification` | Every `features/story-*/seed.md` committed after `2026-04-26T00:00Z` contains a `Frontend:` field. Uses `_git_first_commit_timestamp` for grandfathering. |

---

## 4. Error Message Assertions

- Case B error must contain `e2e/**/*.spec.ts` (actionable glob).
- Case C error must contain `@smoke` (actionable tag name).
- Case B and C errors must contain the story ID.

---

## 5. Sidecar Assertion

- Cases B and C: after gate failure, verify that
  `_write_frontend_gate_sidecar` would produce the correct path:
  `/home/hermes/state/{agent}/phase-runner-gate-failed/{story_id}.json`

Note: the sidecar write is tested at the integration level within
`_verify_frontend_gate`'s caller. The unit tests focus on the gate return
values. A separate test verifies the sidecar write function directly.

---

## 6. Fixtures

All fixtures use `tmp_path` for isolated worktree simulation. Each creates:
- `{tmp_path}/features/story-999-test/seed.md` with the appropriate `Frontend:` field
- Mock `subprocess.run` side_effect returning canned `git diff` and `git show` output

No real git operations, no network calls, no DB access.
