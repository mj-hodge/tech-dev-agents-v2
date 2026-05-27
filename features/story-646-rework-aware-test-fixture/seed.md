# Seed: STORY-646 — Rework-aware test fixture for branch-logic codepaths

## Overview

| Field | Value |
|-------|-------|
| Mode | new_feature |
| Scope | small |
| Frontend | false |
| Feature Name | Pytest fixture that parameterizes branch-logic tests over `rework_of=None` AND `rework_of=STORY-N (slugged)`, forcing every code path that touches branch logic to test BOTH cases |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-646/rework-aware-test-fixture` |
| Status | Seed written 2026-04-26 ~01:45 UTC |
| Priority | 90 — prevents the rework-bug class that has now bit us 3x |

---

## 1. Idea / Trigger

**The pattern:** every story we ship that touches branch logic in `sdlc_phase_runner.py` is incomplete because the test cases assume canonical formula branches (`story-N/story-N`). Real production rework targets often use slugged branches (`story-551/remediate-pre-deploy-gate`). This week:

- **STORY-637** (rework-aware Phase 8 guard): seed and tests assumed canonical formula. Shipped, ran in production, hit slugged branches, FAILED. Required STORY-642 follow-up to add ls-remote lookup.
- **STORY-642** (the follow-up): added ls-remote lookup. Tests parameterized this time, but only because we'd already seen the bug. Future stories touching branch logic will repeat the cycle without a structural test discipline.

We need a fixture that **forces** every branch-logic test to run with both:
1. `rework_of=None` (canonical formula path)
2. `rework_of=STORY-N` where the rework target uses a slugged branch (real-world path)

Without this fixture, an author can write 5 happy-path tests against `rework_of=None`, get them green, ship, and find out in production that slugged branches break. **The fixture removes the option to forget.**

## 2. Problem Statement

- **3 production incidents from the same blind spot** in 2 weeks. Cost: hours of operator time, retry-loop token waste, queue downtime.
- **Test-writing is the failure point**, not implementation. Implementers code to whatever tests they're given. Seeds and reviews have not caught this gap.
- **Pytest fixtures are the right shape** because they auto-apply across many tests without each test author having to remember the pattern.
- **The fixture must be discoverable** — implementers writing branch-logic tests must trip over it naturally (importing from `tests/conftest.py` or a clear `tests/branch_logic_fixtures.py`).

## 3. Scope Classification

**Small.** Two files:
- `tests/conftest.py` (or `tests/deployment/conftest.py`) — add the fixture
- `tests/deployment/test_phase_runner_branch_mismatch.py` — extend existing tests to use the fixture as a regression smoke

No production code changes. No schema. No deploy. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`tests/conftest.py`** (project root) or **`tests/deployment/conftest.py`** — pytest auto-discovers fixtures in conftest.py. The right placement is whichever is closest to the tests being parameterized. Recommendation: `tests/deployment/conftest.py` since branch-logic tests live there.

- **`tests/deployment/test_phase_runner_branch_mismatch.py`** (already exists, expanded by STORY-637 and STORY-642) — refactor existing tests to consume the new fixture so they re-run for both rework_of cases.

- **`tests/deployment/test_phase_runner_acceptance_diff.py`** (already exists, expanded by STORY-643) — same: tests that depend on the rework-target's seed should also use the fixture.

- **`tests/deployment/test_phase_runner_seed_path.py`** — STORY-640 fix may have similar oversight; audit and parameterize.

### Fixture API design

```python
# tests/deployment/conftest.py

import pytest
from dataclasses import dataclass

@dataclass
class ReworkScenario:
    """Represents one rework-of test case."""
    rework_of: str | None  # e.g., "STORY-551" or None
    target_branch_on_remote: str  # e.g., "story-551/remediate-pre-deploy-gate" or canonical
    target_seed_frontend: bool  # Frontend flag from the rework target's seed
    description: str

REWORK_SCENARIOS = [
    ReworkScenario(
        rework_of=None,
        target_branch_on_remote="",  # not applicable
        target_seed_frontend=False,
        description="non-rework (regression baseline)",
    ),
    ReworkScenario(
        rework_of="STORY-589",
        target_branch_on_remote="story-589/story-589",
        target_seed_frontend=False,
        description="rework with canonical formula branch",
    ),
    ReworkScenario(
        rework_of="STORY-551",
        target_branch_on_remote="story-551/remediate-pre-deploy-gate",
        target_seed_frontend=False,
        description="rework with slugged branch (production case)",
    ),
    ReworkScenario(
        rework_of="STORY-525",
        target_branch_on_remote="story-525/story-525",
        target_seed_frontend=True,
        description="rework with frontend=true target seed",
    ),
]

@pytest.fixture(params=REWORK_SCENARIOS, ids=lambda s: s.description)
def rework_scenario(request):
    """Parameterizes a test over multiple rework_of cases.
    
    Branch-logic tests MUST consume this fixture so they automatically run
    against canonical, slugged, and frontend-target rework cases.
    """
    return request.param
```

Each test that consumes this fixture runs 4 times (one per scenario), so a single test method covers all the cases that have bitten us in production.

### How tests consume the fixture

```python
def test_branch_mismatch_guard_for_rework(rework_scenario, mock_workdir, ...):
    expected = _derive_expected_branch(
        workdir=mock_workdir,
        story_id="STORY-PROBE",
        rework_of=rework_scenario.rework_of,
    )
    if rework_scenario.rework_of:
        assert expected == rework_scenario.target_branch_on_remote
    else:
        assert expected == "story-probe/story-probe"
```

The single test now covers all 4 scenarios. New scenarios can be added by appending to the list.

## 5. The Fix

### Change 1: define the fixture

Add `tests/deployment/conftest.py` with the `rework_scenario` fixture and `REWORK_SCENARIOS` list. Cover:
- non-rework baseline
- rework with canonical formula branch
- rework with slugged branch (the bug we keep hitting)
- rework with frontend=true target seed (for AccDiff classification stories)

### Change 2: refactor existing tests to use the fixture

In each of:
- `test_phase_runner_branch_mismatch.py`
- `test_phase_runner_acceptance_diff.py`
- `test_phase_runner_seed_path.py` (audit; refactor if applicable)

Find tests that touch branch logic or AccDiff classification with rework_of as a parameter. Refactor them to consume `rework_scenario` instead of hard-coding values. Verify the existing tests still pass for each scenario (RED → GREEN as a smoke).

### Change 3: documentation

Add a comment block at the top of `tests/deployment/conftest.py`:

```python
"""
Branch-logic test fixtures.

When writing tests for code that touches branch resolution, rework_of handling,
or rework-target seed reading: USE THE rework_scenario FIXTURE.

It parameterizes your test over the 4 cases that have bitten us in production:
1. non-rework (baseline)
2. rework with canonical formula branch
3. rework with slugged branch (e.g., STORY-637's blind spot)
4. rework with frontend=true target

The fixture exists because we keep shipping rework-handling code that's only
tested against case 1 or 2, then production hits case 3 (slugged) and we have
to ship a follow-up.
"""
```

### Change 4: optional — a CI gate

If feasible without too much complexity: a pre-commit or CI check that scans `tests/deployment/test_*` for tests that use `rework_of` parameter but DON'T consume the fixture. Flag those for review. Out-of-scope if it grows the story; nice-to-have.

## 6. Out of Scope

- Refactoring test files outside `tests/deployment/` — the fixture lives close to where it's used.
- Adding fixtures for unrelated patterns (cross-repo, multi-agent, etc.) — separate stories if needed.
- Making the fixture validate against real `git ls-remote` output — keep it stubbed; integration tests cover the real lookup.
- A CLI tool that auto-generates the fixture — overkill.
- Asana/Monday integration — N/A.

## Test Criteria

Phase 7 produces `test-design.md` and a refactored test file demonstrating the fixture pattern. Then in Phase 8 (`tests/deployment/test_*` extensions):

| Test | Setup | Expectation |
|---|---|---|
| Fixture yields 4 scenarios | use `pytest --collect-only` | 4 scenario IDs visible in test names |
| Each scenario produces correct expected_branch | `_derive_expected_branch` exercised across all 4 cases | each test variant passes |
| Slugged-branch scenario triggers ls-remote lookup | mock subprocess on the slugged scenario | `git ls-remote --heads origin "story-551/*"` is called; canonical scenario does NOT call ls-remote |
| Frontend=true scenario flags AccDiff playwright requirement | run AccDiff guard with that scenario | AccDiff requires Playwright spec; backend scenario doesn't |
| Adding a new scenario | append to `REWORK_SCENARIOS` | all consuming tests automatically run with new case (no per-test changes) |
| Existing tests keep passing | run full test suite | green |

## Validation

After Phase 8 lands:

1. Run `pytest tests/deployment/test_phase_runner_branch_mismatch.py -v` — output shows each test running 4x, one per scenario, all GREEN.
2. Add a deliberately broken test case (e.g., a new scenario where the slugged branch is expected but the helper returns canonical) — verify the slugged variant fails RED.
3. Confirm the conftest.py fixture is discoverable by pytest (no manual import needed in tests).

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-646/rework-aware-test-fixture`
- Scope: small
- Priority: 90 (3 production incidents averted)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~25 min
- Implementing agent should: (a) place fixture in `tests/deployment/conftest.py` (closest to consumers), (b) refactor 3 existing test files to use it as smoke proof, (c) include the docstring comment block so future test authors trip over the right pattern, (d) keep the scenario list small + meaningful (4 cases, not 20).
