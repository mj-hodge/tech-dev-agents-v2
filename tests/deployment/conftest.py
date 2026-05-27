"""
Branch-logic test fixtures for the deployment test suite.

WHY THIS FILE EXISTS
====================
When writing tests for code that touches branch resolution, rework_of handling,
or rework-target seed reading: USE THE ``rework_scenario`` FIXTURE.

It parameterizes your test over 4 cases that have bitten us in production:

1. non-rework (baseline)               — rework_of=None, formula branch
2. rework with canonical formula branch — rework_of=STORY-589, branch=story-589/story-589
3. rework with slugged branch           — rework_of=STORY-551, branch=story-551/remediate-pre-deploy-gate
4. rework with frontend=True target     — rework_of=STORY-525, target seed has Frontend: true

The fixture exists because we kept shipping rework-handling code that was only
tested against case 1 or 2, then production hit case 3 (slugged) and triggered
a follow-up story:

  STORY-637: added rework_of to _save_partial_work. Tested canonical. Missed slugged.
  STORY-642: added ls-remote lookup. Finally caught slugged branches.
  STORY-646: made the 4-case matrix a fixture so authors can't forget.

USAGE PATTERN
=============
Any test that exercises branch resolution, seed reads, or AccDiff classification
with a ``rework_of`` parameter MUST consume this fixture::

    def test_something_branch_related(rework_scenario, tmp_path):
        # Set up ls-remote mock based on scenario
        if rework_scenario.rework_of:
            ls_output = f"abc\trefs/heads/{rework_scenario.target_branch_on_remote}\n"
        else:
            ls_output = ""

        # Exercise the function under test
        result = _expected_branch_for_story(str(tmp_path), "STORY-999",
                                            rework_of=rework_scenario.rework_of)

        # Assert based on expected outcome
        if rework_scenario.rework_of:
            assert result == rework_scenario.target_branch_on_remote
        else:
            assert result == "story-999/story-999"

ADDING SCENARIOS
================
To cover a new production case, append to REWORK_SCENARIOS. All tests that
consume ``rework_scenario`` will automatically run with the new case. No
per-test changes needed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class ReworkScenario:
    """Represents one parameterized rework_of test case.

    Attributes:
        rework_of:              The rework_of value to pass to functions
                                (None for non-rework stories).
        target_branch_on_remote: The branch name that ``git ls-remote`` would
                                return for the rework target. Empty string when
                                rework_of is None (ls-remote not called).
        target_seed_frontend:   The Frontend flag value in the rework target's
                                seed.md. Determines whether AccDiff demands a
                                Playwright spec.
        description:            Human-readable label (used as pytest test ID).
    """

    rework_of: str | None
    target_branch_on_remote: str
    target_seed_frontend: bool
    description: str


REWORK_SCENARIOS: list[ReworkScenario] = [
    ReworkScenario(
        rework_of=None,
        target_branch_on_remote="",  # ls-remote not called for non-reworks
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
        description="rework with slugged branch (production bug case, STORY-637/642)",
    ),
    ReworkScenario(
        rework_of="STORY-525",
        target_branch_on_remote="story-525/story-525",
        target_seed_frontend=True,
        description="rework with frontend=True target seed",
    ),
]


@pytest.fixture(params=REWORK_SCENARIOS, ids=lambda s: s.description)
def rework_scenario(request: pytest.FixtureRequest) -> ReworkScenario:
    """Parameterize a test over all known rework_of production cases.

    Use this fixture in any test that exercises branch-resolution logic,
    rework_of threading, or AccDiff seed classification.  The test will
    automatically run once for each scenario in REWORK_SCENARIOS.
    """
    return request.param  # type: ignore[return-value]
