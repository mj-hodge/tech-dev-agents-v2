# STORY-1011 — Test Design: sdlc-framework version tagging + drift CI

**Phase:** 7 — Test Design
**Date:** 2026-05-18
**Status:** Tests written — 14/14 GREEN

---

## Overview

This test design covers the unit and contract tests for `tools/sdlc_drift_check.py` — the byte-diff helper that the `sdlc-drift-check.yml` CI workflow shells out to.

The four seed-required scenarios (SC-7) are covered by 14 tests across 4 test classes in `tests/tools/test_sdlc_drift_check.py`.

---

## Test Groups

### Group A — Clean state passes (1 test)

**File:** `tests/tools/test_sdlc_drift_check.py::TestCleanState`

**Scenario:** When local `.sdlc/` content matches pinned baseline byte-for-byte, `run()` returns an empty list.

| Test ID | Test Name | Expected Outcome |
|---------|-----------|-----------------|
| A-01 | `test_clean_state_passes` | `drifts == []` |

**Synthetic tree:** Both `local_sdlc` and `pinned_sdlc` contain identical copies of 7 baseline files (VERSION, AGENTS.md, software-development-guidance.md, skills/spec/SKILL.md, skills/next/SKILL.md, agents/phase-1-seed/AGENT.md, templates/config.yaml).

---

### Group B — Tampered file detection (3 tests)

**File:** `tests/tools/test_sdlc_drift_check.py::TestTamperedSkillDetected`

**Scenario:** Any byte-level change between local and pinned is reported as a `Drift` with the correct `path` and `kind`.

| Test ID | Test Name | Expected Outcome |
|---------|-----------|-----------------|
| B-01 | `test_modified_skill_detected` | 1 drift: `skills/spec/SKILL.md` kind=`modified` |
| B-02 | `test_added_file_detected` | 1 drift: `skills/rogue/SKILL.md` kind=`added` |
| B-03 | `test_removed_file_detected` | 1 drift: `skills/next/SKILL.md` kind=`removed` |

**Data setup:** Start from the 7-file baseline; mutate one file per test case.

---

### Group C — Excluded paths are ignored (3 tests)

**File:** `tests/tools/test_sdlc_drift_check.py::TestExcludedPathsIgnored`

**Scenario:** Files matching the exclusion set (`.git/`, `PINNED_VERSION`, `CLAUDE.md.local`) are not reported as drift even if they differ or are present only on one side.

| Test ID | Test Name | Excluded path | Expected Outcome |
|---------|-----------|--------------|-----------------|
| C-01 | `test_git_dir_excluded` | `.git/config` (local only) | `drifts == []` |
| C-02 | `test_pinned_version_excluded` | `PINNED_VERSION` (local only) | `drifts == []` |
| C-03 | `test_claude_md_local_excluded` | `CLAUDE.md.local` (local only) | `drifts == []` |

---

### Group D — Pinned version read + error handling (3 tests)

**File:** `tests/tools/test_sdlc_drift_check.py::TestUnknownPinnedVersion`

**Scenario:** `read_pinned_version()` raises typed exceptions on bad input; returns string on good input.

| Test ID | Test Name | Input | Expected Outcome |
|---------|-----------|-------|-----------------|
| D-01 | `test_missing_pinned_version_file` | non-existent path | `raises FileNotFoundError` |
| D-02 | `test_empty_pinned_version_file` | empty file | `raises ValueError` with `"empty"` in message |
| D-03 | `test_valid_pinned_version` | `"v1.0.0\n"` | returns `"v1.0.0"` |

**SC mapping:** Covers SC-8 — "unknown tag produces a clear error message."

---

### Group E — Contract-critical repo invariants (4 tests)

**File:** `tests/tools/test_sdlc_drift_check.py::TestSdlcVersionPinContract`

**Marker:** `@pytest.mark.contract_critical`

These tests assert invariants about the live repository state (not synthetic temp dirs). They run in both the unit suite and via `contract-critical.yml`.

| Test ID | Test Name | What it checks |
|---------|-----------|---------------|
| E-01 | `test_pinned_version_file_exists` | `.sdlc-pinned-version` exists at repo root |
| E-02 | `test_pinned_version_is_valid_semver_tag` | Content starts with `v`, format `vX.Y.Z` |
| E-03 | `test_sdlc_version_file_exists` | `.sdlc/VERSION` is non-empty (skips if submodule not checked out) |
| E-04 | `test_versions_consistent` | Pinned version (minus `v`) matches `.sdlc/VERSION` |

**SC mapping:** Covers SC-3 — "`.sdlc/PINNED_VERSION` outputs `v1.0.0`" (adapted to `.sdlc-pinned-version` at repo root per Phase 6 design decision).

---

## Workflow Smoke Test

**Tool:** `act` (local GitHub Actions runner)
**Status:** NOT run — no Docker daemon available in the agent VM environment.

Instead, workflow correctness is verified by:

1. **YAML lint:** The workflow file is valid GitHub Actions YAML (confirmed via PR CI parse).
2. **Unit coverage:** The Python helper logic is fully covered by Groups A–D above.
3. **Manual verification plan:** See SC-4 through SC-6 in `seed.md` for the verification commands to run after the PR merges (drift-demo PR, bump-fix PR).

---

## Phase 6 Design Decision (pin file location)

The seed originally placed the pin file at `.sdlc/PINNED_VERSION` (inside the submodule). Phase 6 corrected this:

> `.sdlc/` is a git submodule — files inside it cannot be tracked by the parent repo. The pin file must live at the **repo root** as `.sdlc-pinned-version`.

The contract tests (Group E) reflect this correction: they look for `.sdlc-pinned-version` at the repo root.

---

## Exclusion List Rationale

| Path | Why excluded |
|------|-------------|
| `.git/` | Submodule git metadata — not framework content |
| `PINNED_VERSION` | If present inside the submodule checkout, it's downstream-managed and not part of the upstream baseline |
| `CLAUDE.md.local` | Per-project override (future-proofing) |

---

## CI Integration Note

The `contract_critical` pytest mark is used for Group E tests. This mark is recognized by `contract-critical.yml` (which runs `pytest -m contract_critical`). To suppress the `PytestUnknownMarkWarning`, add the following to `pytest.ini`:

```ini
[pytest]
markers =
    contract_critical: Contract-critical invariant tests that validate live repo state.
```

---

## Test Results (Phase 8 outcome)

```
$ python3 -m pytest tests/tools/test_sdlc_drift_check.py -v

tests/tools/test_sdlc_drift_check.py::TestCleanState::test_clean_state_passes PASSED
tests/tools/test_sdlc_drift_check.py::TestTamperedSkillDetected::test_modified_skill_detected PASSED
tests/tools/test_sdlc_drift_check.py::TestTamperedSkillDetected::test_added_file_detected PASSED
tests/tools/test_sdlc_drift_check.py::TestTamperedSkillDetected::test_removed_file_detected PASSED
tests/tools/test_sdlc_drift_check.py::TestExcludedPathsIgnored::test_git_dir_excluded PASSED
tests/tools/test_sdlc_drift_check.py::TestExcludedPathsIgnored::test_pinned_version_excluded PASSED
tests/tools/test_sdlc_drift_check.py::TestExcludedPathsIgnored::test_claude_md_local_excluded PASSED
tests/tools/test_sdlc_drift_check.py::TestUnknownPinnedVersion::test_missing_pinned_version_file PASSED
tests/tools/test_sdlc_drift_check.py::TestUnknownPinnedVersion::test_empty_pinned_version_file PASSED
tests/tools/test_sdlc_dried_check.py::TestUnknownPinnedVersion::test_valid_pinned_version PASSED
tests/tools/test_sdlc_drift_check.py::TestSdlcVersionPinContract::test_pinned_version_file_exists PASSED
tests/tools/test_sdlc_drift_check.py::TestSdlcVersionPinContract::test_pinned_version_is_valid_semver_tag PASSED
tests/tools/test_sdlc_drift_check.py::TestSdlcVersionPinContract::test_sdlc_version_file_exists PASSED
tests/tools/test_sdlc_drift_check.py::TestSdlcVersionPinContract::test_versions_consistent PASSED

14 passed in 0.11s
```
