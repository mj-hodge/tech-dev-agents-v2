# Test Design: STORY-646 — Rework-aware Test Fixture

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-646 |
| Scope | Small |
| Phase | 7 (Test Design) |
| Coverage target | 50% (smoke — critical paths for test infrastructure) |
| Frontend | false |
| Test runner | pytest |
| RED state confirmed | 2 FAIL, 24 PASS, 8 SKIP |

---

## What This Story Tests

STORY-646 creates test infrastructure, not production code. The Phase 7 deliverable has two layers:

1. **The fixture itself** (`tests/deployment/conftest.py`) — the `rework_scenario` fixture and `REWORK_SCENARIOS` list.
2. **Tests about the fixture** (`tests/deployment/test_rework_scenario_fixture.py`) — verify the fixture is correct and enforce that consuming test files adopt it.

The RED → GREEN progression is:
- **RED now (Phase 7):** `E-01` and `E-02` fail because the two existing test files (`test_phase_runner_branch_mismatch.py`, `test_phase_runner_acceptance_diff.py`) do not yet use the `rework_scenario` fixture.
- **GREEN after Phase 8:** Those two files are refactored to accept `rework_scenario` as a parameter; `E-01` and `E-02` pass.

---

## Files Produced

| File | Role |
|------|------|
| `tests/deployment/conftest.py` | pytest fixture — `ReworkScenario` dataclass, `REWORK_SCENARIOS` list (4 entries), `rework_scenario` parameterized fixture |
| `tests/deployment/test_rework_scenario_fixture.py` | Meta-tests + behavioral tests (this phase's RED deliverable) |

---

## Test Groups

### Group A — Fixture mechanics (5 tests, all PASS)

Verifies `REWORK_SCENARIOS` and the `rework_scenario` fixture are correctly defined.

| ID | Test | Verifies | State |
|----|------|----------|-------|
| A-01 | `test_scenario_count_is_four` | Exactly 4 scenarios in `REWORK_SCENARIOS` | PASS |
| A-02 | `test_first_scenario_is_non_rework` | First scenario has `rework_of=None` (baseline) | PASS |
| A-03 | `test_slugged_branch_scenario_exists` | At least one scenario has non-canonical (slugged) branch | PASS |
| A-04 | `test_frontend_true_scenario_exists` | At least one scenario has `target_seed_frontend=True` | PASS |
| A-05 | `test_all_fields_have_correct_types` | All `ReworkScenario` instances have correct field types | PASS |

**Purpose:** Guard against accidental fixture regression. If a scenario is removed or has wrong types, these tests fail immediately.

### Group B — `_expected_branch_for_story` across all scenarios (12 tests parameterized 4×, 4 skip expected)

Exercises `_expected_branch_for_story` with every rework scenario via the fixture.

| ID | Test | Verifies | State |
|----|------|----------|-------|
| B-01 | `test_branch_resolution_matches_scenario[*]` | Each scenario returns `target_branch_on_remote` | 4× PASS |
| B-02 | `test_non_rework_does_not_call_ls_remote[non-rework]` | `rework_of=None` → no ls-remote call | 1 PASS, 3 SKIP |
| B-03 | `test_slugged_rework_calls_ls_remote[rework *]` | `rework_of` set → ls-remote IS called | 3 PASS, 1 SKIP |
| B-04 | `test_branch_result_includes_rework_prefix[*]` | All branch names start with `story-` | 4× PASS |

**Mock pattern:** `_ls_remote_mock(scenario)` builds a `subprocess.run` side effect that returns `refs/heads/{target_branch_on_remote}` when `ls-remote` is in the command.

**Why needed:** Before STORY-646, `_expected_branch_for_story` was tested for specific hardcoded scenarios (D-01..D-06 in `test_phase_runner_branch_mismatch.py`). This group runs the same checks via the fixture so they automatically cover new scenarios.

### Group C — `_verify_acceptance_diff` across all scenarios (8 tests parameterized 4×, 4 skip expected)

Tests AccDiff classification with every rework scenario.

| ID | Test | Verifies | State |
|----|------|----------|-------|
| C-01 | `test_backend_target_no_playwright_required[*]` | Scenarios with `target_seed_frontend=False` → no Playwright required | 3 PASS, 1 SKIP |
| C-03 | `test_frontend_true_rework_target_requires_playwright[frontend=True]` | rework with frontend=True target → Playwright IS required | 1 PASS, 3 SKIP |

**Seed setup:** `_write_target_seed(tmp_path, scenario)` creates `features/story-N-*/seed.md` with the correct `| Frontend | {true|false} |` table row.

**Key coverage:** C-03 is the only test in the entire suite that covers `rework_of` + `target_seed_frontend=True`. Without STORY-646, a regression in this path would go undetected.

### Group D — Output-variance tests (2 tests, all PASS)

Detects stub implementations that return the same hardcoded branch for all inputs.

| ID | Test | Verifies | State |
|----|------|----------|-------|
| D-01 | `test_rework_vs_non_rework_outputs_differ` | `rework_of=None` and `rework_of='STORY-551'` return different branches | PASS |
| D-02 | `test_canonical_vs_slugged_rework_outputs_differ` | Canonical and slugged rework branches differ | PASS |

### Group E — Consuming-file adoption (3 tests, 2 RED, 1 PASS)

**Primary RED tests for Phase 7.** Verify that the three existing test files that exercise branch logic have been refactored to use `rework_scenario`.

| ID | Test | Verifies | State → Phase 8 |
|----|------|----------|-----------------|
| E-01 | `test_branch_mismatch_file_uses_rework_scenario` | `test_phase_runner_branch_mismatch.py` has test methods accepting `rework_scenario` | **RED** → PASS after refactor |
| E-02 | `test_acceptance_diff_file_uses_rework_scenario` | `test_phase_runner_acceptance_diff.py` has test methods accepting `rework_scenario` | **RED** → PASS after refactor |
| E-03 | `test_seed_path_file_uses_rework_scenario_or_has_no_rework_tests` | `test_phase_runner_seed_path.py` either uses fixture OR has no hardcoded `rework_of` | **PASS** (no refactor needed — file has no hardcoded `rework_of=STORY-N`) |

---

## RED State Summary

```
2 FAIL   ← E-01, E-02 (consuming-file adoption — Phase 8 target)
24 PASS  ← Groups A, B, C, D (fixture mechanics + behavioral coverage)
8 SKIP   ← Scenario-specific guards (e.g. ls-remote assertion for non-rework only)
```

---

## Phase 8 Implementation Instructions

### Step 1: Refactor `test_phase_runner_branch_mismatch.py`

Identify the D-series tests (D-01..D-06 in `TestExpectedBranchForStoryLsRemote`). Each calls `_expected_branch_for_story` with a hardcoded `rework_of` value. Refactor to:

```python
class TestExpectedBranchForStoryLsRemote:

    def test_branch_resolved_for_all_rework_scenarios(self, rework_scenario, tmp_path):
        """D-series parameterized: _expected_branch_for_story works for all scenarios."""
        if rework_scenario.rework_of is None:
            pytest.skip("non-rework handled by separate regression test")

        fn = sdlc_phase_runner._expected_branch_for_story

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return MagicMock(
                    returncode=0,
                    stdout=f"abc\trefs/heads/{rework_scenario.target_branch_on_remote}\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-999", rework_of=rework_scenario.rework_of)

        assert result == rework_scenario.target_branch_on_remote
```

Keep existing specific tests (D-03 multiple branches, D-04 empty fallback, D-05 exception fallback, D-06 non-rework) as they test specific behaviors not covered by parameterization.

### Step 2: Refactor `test_phase_runner_acceptance_diff.py`

Identify `TestVerifyAcceptanceDiffReworkOf` (C-01..C-04). Add a parameterized variant that covers all rework scenarios:

```python
def test_verify_acceptance_diff_all_rework_scenarios(self, rework_scenario, tmp_path):
    """C-series parameterized: _verify_acceptance_diff for all rework cases."""
    # Write target seed based on rework_scenario.target_seed_frontend
    # Call _verify_acceptance_diff with rework_scenario.rework_of
    # Assert based on rework_scenario.target_seed_frontend
```

### Step 3: Verify E-03 audit — `test_phase_runner_seed_path.py`

Confirm no test in this file uses `rework_of='STORY-N'` as a hardcoded value. E-03 currently passes because this is the case. After Phase 8, re-run to confirm it still passes.

---

## Fixture API Reference

```python
# tests/deployment/conftest.py

@dataclass(frozen=True)
class ReworkScenario:
    rework_of: str | None          # None for non-rework; 'STORY-N' for rework
    target_branch_on_remote: str   # branch git ls-remote would return; "" for non-rework
    target_seed_frontend: bool     # Frontend flag in rework target's seed
    description: str               # pytest test ID label

REWORK_SCENARIOS = [
    ReworkScenario(None, "", False, "non-rework (regression baseline)"),
    ReworkScenario("STORY-589", "story-589/story-589", False, "rework with canonical formula branch"),
    ReworkScenario("STORY-551", "story-551/remediate-pre-deploy-gate", False, "rework with slugged branch ..."),
    ReworkScenario("STORY-525", "story-525/story-525", True, "rework with frontend=True target seed"),
]

@pytest.fixture(params=REWORK_SCENARIOS, ids=lambda s: s.description)
def rework_scenario(request) -> ReworkScenario:
    return request.param
```

---

## Coverage Check

| Requirement from seed.md | Covered? | Where |
|--------------------------|----------|-------|
| Fixture yields 4 scenarios | ✅ A-01, `--collect-only` shows 4x | Group A |
| Each scenario produces correct expected_branch | ✅ B-01 (4×) | Group B |
| Slugged-branch scenario triggers ls-remote | ✅ B-03 | Group B |
| Non-rework does NOT call ls-remote | ✅ B-02 | Group B |
| Frontend=true scenario flags AccDiff Playwright requirement | ✅ C-03 | Group C |
| Output varies with input | ✅ D-01, D-02 | Group D |
| Existing tests use the fixture (gate) | 🔴 E-01, E-02 (RED until Phase 8) | Group E |
| Existing tests keep passing (regression) | ✅ no regressions in suite | — |

---

## Route Mock Verification (N/A)

No Playwright tests in this story. No `page.route()` patterns used.

---

## API Mock Verification

All `subprocess.run` mocks are verified against actual command patterns in `sdlc_phase_runner.py`:
- `"ls-remote" in cmd_list` matches `subprocess.run(["git", "ls-remote", "--heads", "origin", f"story-{n}/*"])`
- `"diff" in cmd_str` matches `subprocess.run(["git", "diff", "--name-only", ...])`
- Mock patterns verified against lines 752–767 (`_expected_branch_for_story`) and 1465–1620 (`_verify_acceptance_diff`) in `sdlc_phase_runner.py`.
