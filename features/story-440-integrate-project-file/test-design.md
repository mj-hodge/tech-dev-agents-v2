# Test Design — STORY-440: Integrate project_file.py into Phase Runner

> **Phase:** 7 (Test Design)
> **Scope:** Small
> **Date:** 2026-04-19
> **Author:** Hermes (Agent)

---

## Overview

STORY-440 has two distinct test surfaces:

1. **`deployment/hermes/project_file.py` module** — bring over the 23 existing tests from story-438 (which exist on `origin/story-438/story-438` but not on this branch) plus add new bug-specific regression tests for AC-3 (exact-match) and AC-4 (TOCTOU).
2. **`deployment/hermes/sdlc_phase_runner.py` integration** — new tests verifying the phase runner actually calls `update_story_status()` after successful phases (AC-1, AC-2, AC-8) and handles errors gracefully.

All tests are **RED** until Phase 8 wires in `project_file.py` and patches the bugs.

---

## Test File Locations

| File | Purpose | Count |
|------|---------|-------|
| `tests/test_project_file.py` | Full suite: 23 existing tests (story-438 regression) + 3+ new bug-fix/integration tests | ≥26 |

> AC-8 measurement: `pytest tests/test_project_file.py` must report ≥26 collected tests.

---

## Setup / Teardown

### Fixtures (shared)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `tmp_path` | function | pytest built-in; each test gets a fresh temp dir for its `.project` file |
| `make_project_file(tmp_path, content)` | helper fn | writes a `.project` stub and returns its `pathlib.Path` |
| `MINIMAL_PROJECT` | module constant | minimal valid `.project` with empty Story Status and Phase History tables |
| `PROJECT_WITH_ACTIVE_STORY` | module constant | `.project` pre-populated with STORY-100 active and a Phase History row |

### Import Gate (RED mechanism)

The very first import statement makes all tests fail until Phase 8 delivers the module:

```python
from deployment.hermes.project_file import read_project, update_story_status
```

Phase-runner integration tests additionally import:

```python
from deployment.hermes.sdlc_phase_runner import run_sdlc_phases
```

and use `unittest.mock.patch` to isolate `_run_phase_sdk` and `update_story_status`.

---

## Test Cases Mapped to Acceptance Criteria

### AC-7 — All 23 existing tests pass (ported from story-438)

These tests were written for story-438 and confirm the `project_file.py` API itself. They are ported verbatim so they fail (RED) until project_file.py exists on the current branch.

| # | Test ID | Assertion |
|---|---------|-----------|
| 1 | `TestReadProject::test_read_project_returns_dict` | `read_project()` returns a mapping |
| 2 | `TestReadProject::test_read_project_returns_story_status_rows` | Story Status rows parsed correctly |
| 3 | `TestReadProject::test_read_project_returns_empty_story_status_when_table_empty` | Empty table → empty list |
| 4 | `TestReadProject::test_read_project_returns_phase_routing` | Phase Routing fields extracted |
| 5 | `TestReadProject::test_read_project_returns_phase_history_rows` | Phase History rows parsed |
| 6 | `TestReadProject::test_read_project_missing_file` | FileNotFoundError raised |
| 7 | `TestUpdateStoryStatus::test_update_adds_row_to_empty_table` | Inserts row into empty table |
| 8 | `TestUpdateStoryStatus::test_update_adds_row_fields` | All required columns present |
| 9 | `TestUpdateStoryStatus::test_update_is_idempotent` | Double call → single row |
| 10 | `TestUpdateStoryStatus::test_update_modifies_row_in_place` | Re-update changes phase/status in row |
| 11 | `TestUpdateStoryStatus::test_two_stories_do_not_clobber_each_other` | STORY-100 + STORY-200 both present |
| 12 | `TestUpdateStoryStatus::test_phase_routing_not_overwritten_for_other_story` | Different story → routing unchanged |
| 13 | `TestUpdateStoryStatus::test_phase_routing_updated_for_same_story` | Same story → routing updated |
| 14 | `TestUpdateStoryStatus::test_phase_routing_set_when_no_active_story` | Blank Active Story → gets set |
| 15 | `TestUpdateStoryStatus::test_other_sections_untouched` | Project Overview unchanged |
| 16 | `TestUpdateStoryStatus::test_output_is_valid_markdown_table` | Consistent pipe count per row |
| 17 | `TestUpdateStoryStatus::test_phase_history_appended_on_completion` | is_final=True adds history row |
| 18 | `TestUpdateStoryStatus::test_phase_history_existing_rows_preserved` | Pre-existing history survives |
| 19 | `TestUpdateStoryStatus::test_no_phase_history_when_not_final` | is_final=False → no history row |
| 20 | `TestUpdateStoryStatus::test_phase_history_not_duplicated_on_double_completion` | Double is_final → single entry |
| 21 | `TestUpdateStoryStatus::test_update_empty_table_header_only` | Header+separator only table → inserts |
| 22 | `TestUpdateStoryStatus::test_branch_with_slashes` | Branch `story-X/story-X-slug` stored correctly |
| 23 | `test_sequential_concurrent_writes` | Simulates 2-agent sequential write; both rows intact |

### AC-3 — Exact story ID match, not substring (new regression test)

| # | Test ID | Assertion |
|---|---------|-----------|
| 24 | `TestExactStoryIdMatch::test_story_id_does_not_match_prefix` | STORY-4380 active → STORY-438 update does NOT touch Phase Routing |
| 25 | `TestExactStoryIdMatch::test_story_id_does_not_match_suffix` | STORY-38 active → STORY-438 update does NOT touch Phase Routing |

**How it tests the bug:** The pre-bug code uses `story_id in active_story` (substring). With STORY-4380 as active, `"STORY-438" in "STORY-4380"` is `True` (wrong). The fixed code uses regex-based exact extraction. Test asserts Phase Routing field `Active Story` still says `STORY-4380` after an STORY-438 update.

### AC-4 — TOCTOU fix: file read exactly once (new regression test)

| # | Test ID | Assertion |
|---|---------|-----------|
| 26 | `TestTOCTOU::test_update_story_status_reads_file_once` | `pathlib.Path.read_text` called exactly once during `update_story_status()` |

**How it tests the bug:** Patches `pathlib.Path.read_text` as a spy via `unittest.mock`. Counts invocations. Pre-fix code calls it twice (once in `read_project()` then again to load raw lines). Post-fix code calls it once and derives both parsed structure and raw lines from that single read.

### AC-1 / AC-2 / AC-8 — Phase runner calls update_story_status (new integration tests)

| # | Test ID | Assertion |
|---|---------|-----------|
| 27 | `TestPhaseRunnerIntegration::test_runner_calls_update_after_successful_phase` | `update_story_status` is called after each phase that returns rc=0 |
| 28 | `TestPhaseRunnerIntegration::test_runner_passes_correct_parameters` | Call args include story_id, assignee, scope, current_phase, status, branch, is_final |
| 29 | `TestPhaseRunnerIntegration::test_runner_continues_on_project_file_error` | If `update_story_status` raises, phase runner does NOT abort; returns success |

**Setup:** All three tests mock `_run_phase_sdk` to return `(0, "ok")` and `_verify_deliverable` to return `True`, so the phase loop runs without real SDK calls. They also mock `_ensure_branch` and `subprocess.run` (for git commands).

---

## Edge Cases

| Edge Case | Test coverage |
|-----------|--------------|
| Story-ID that is a prefix of the active story (e.g. STORY-438 vs STORY-4380) | Test 24 |
| Story-ID that is a suffix of the active story (e.g. STORY-38 vs STORY-438) | Test 25 |
| update_story_status() raises an exception | Test 29 |
| is_final=True passed on last phase, False on intermediate phases | Test 28 |
| Double completion call (idempotent Phase History) | Test 20 |
| Branch name with slashes in table cell | Test 22 |
| Empty Story Status table (only header+separator) | Tests 7, 21 |
| Missing .project file | Test 6 |

---

## Test Dependencies

- `pytest` (already in project dev-dependencies)
- `unittest.mock` (stdlib)
- `textwrap` (stdlib)
- `pathlib` (stdlib)
- `deployment.hermes.project_file` — **does not exist on this branch** (RED)
- `deployment.hermes.sdlc_phase_runner` — exists but does not call `update_story_status` (RED for integration tests)

---

## RED State Verification

Run after Phase 7 commit to confirm all new tests fail for the right reasons:

```bash
pytest tests/test_project_file.py -v 2>&1 | head -60
# Expected: ImportError / ModuleNotFoundError for deployment.hermes.project_file
```

```bash
pytest tests/test_project_file.py --collect-only 2>&1 | grep "test session starts" -A5
# Expected: ≥29 tests collected (23 original + 6 new)
```
