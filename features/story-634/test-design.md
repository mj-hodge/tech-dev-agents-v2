# Test Design: STORY-634

| Field | Value |
|-------|-------|
| Story | STORY-634 |
| Phase | 7 — Test Design |
| Scope | Small |
| Coverage target | Post-rebase code integrity verification |

## 1. Test Strategy Overview

This story is a **rebase** of STORY-560 PR #112 onto current main. The tests verify that after conflict resolution, both main-side changes (PRs #114, #116) and branch-side changes (STORY-560, STORY-565) coexist in the rebased source.

| Level | Tool | Count |
|-------|------|-------|
| Source integrity (backend) | pytest | 8 tests |

### Approach

Tests load `sdlc_phase_runner.py` and `dispatch_poller.py` via `importlib` (same pattern as STORY-560 tests) and assert the presence of critical symbols, signatures, and source tokens from **both sides** of the merge.

No functional/behavioral tests are added — those already exist in:
- `tests/deployment/test_phase_runner_complete_with_pr_and_sha.py` (12 tests, STORY-560)
- `tests/deployment/test_phase_runner_frontend_smoke_gate.py` (STORY-565)

This file adds **conflict-resolution correctness** tests only.

## 2. Test Cases

### File: `tests/deployment/test_rebase_634_integrity.py`

#### Group A — Main-side PR #114 preserved (resumed_question_path)

| ID | Test | Asserts |
|----|------|---------|
| A-01 | `test_consume_resumed_question_exists` | `_consume_resumed_question` function exists in phase runner |
| A-02 | `test_run_sdlc_phases_accepts_resumed_question_path_kwarg` | `run_sdlc_phases` signature has `resumed_question_path` parameter |

#### Group B — Main-side PR #116 preserved (rate-limit pause)

| ID | Test | Asserts |
|----|------|---------|
| B-01 | `test_pause_flag_path_defined` | `PAUSE_FLAG_PATH` module-level constant exists in dispatch poller |
| B-02 | `test_is_paused_function_exists` | `_is_paused()` function exists in dispatch poller |

#### Group C — Branch-side STORY-560 preserved (pr_number + retry + sidecar)

| ID | Test | Asserts |
|----|------|---------|
| C-01 | `test_run_sdlc_phases_source_has_pr_number` | `run_sdlc_phases` source contains `pr_number` |
| C-02 | `test_report_complete_with_retry_exists` | `_report_complete_with_retry` function exists in poller |

#### Group D — Branch-side STORY-565 preserved (_verify_frontend_gate)

| ID | Test | Asserts |
|----|------|---------|
| D-01 | `test_verify_frontend_gate_exists` | `_verify_frontend_gate` function exists in phase runner |
| D-02 | `test_verify_frontend_gate_source_has_playwright` | `_verify_frontend_gate` source references Playwright or `@smoke` |

## 3. RED State

All tests reference symbols and code that exist on the **story-560/story-560 branch** but NOT on the current **story-634/story-634 branch** (which has unmodified main copies of the two files). Tests will fail until Phase 8 performs the actual rebase and conflict resolution.
