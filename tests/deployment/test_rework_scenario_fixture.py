"""STORY-646: Rework-aware test fixture — meta-tests and usage demonstration.

This file does four things:

1. **Fixture meta-tests (Group A)** — verify that the rework_scenario fixture
   itself is correctly defined (scenario count, field types, required cases
   present).  These tests guard against accidental fixture regression.

2. **Behavioral tests via fixture (Groups B, C)** — demonstrate the fixture
   being used to parameterize real branch-logic and AccDiff tests.  These are
   the tests that ENFORCE coverage of all 4 rework scenarios for the functions
   that have bitten us in production.

3. **Output-variance tests (Group D)** — detect stub implementations that
   return the same hardcoded branch regardless of input.

4. **Consuming-file adoption tests (Group E)** [RED → GREEN in Phase 8] —
   assert that the three pre-existing test files that exercise branch logic
   have been refactored to IMPORT and USE the ``rework_scenario`` fixture.
   These tests are intentionally RED until Phase 8 completes the refactoring.

RED → GREEN notes
-----------------
Group A tests (A-01..A-05) pass as soon as conftest.py is created.

Group B tests (B-01..B-04) exercise ``_expected_branch_for_story`` across all
4 rework scenarios via the fixture.  They will FAIL if the implementation does
not handle slugged-branch ls-remote lookup or canonical-fallback correctly.

Group C tests (C-01..C-03) exercise ``_verify_acceptance_diff`` via the
fixture.  C-03 covers the rework-with-frontend=True scenario — a case that
has NO counterpart in the pre-STORY-646 test suite.

Group E tests (E-01..E-03) are the PRIMARY RED tests for Phase 7.  They fail
until Phase 8 refactors the three existing test files to use the fixture.

Group D tests (D-01..D-02) are output-variance tests that verify ``_expected_
branch_for_story`` returns DIFFERENT results for different rework scenarios —
detecting stub implementations that always return the same value.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .conftest import REWORK_SCENARIOS, ReworkScenario

# ---------------------------------------------------------------------------
# Module loader (same pattern as other deployment tests)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_SRC = _REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert _RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {_RUNNER_SRC}"

_module_cache: dict = {}


def _get_runner():
    if "mod" in _module_cache:
        return _module_cache["mod"]
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner_646", str(_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


def _ls_remote_mock(scenario: ReworkScenario):
    """Return a subprocess.run side_effect that mocks git ls-remote for a scenario."""

    def _side_effect(cmd, **kwargs):
        cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
        if "ls-remote" in cmd_list:
            if scenario.rework_of and scenario.target_branch_on_remote:
                stub = (
                    f"abc123\trefs/heads/{scenario.target_branch_on_remote}\n"
                )
                return _ok(stdout=stub)
            return _ok(stdout="")  # non-rework: ls-remote returns empty
        return _ok()

    return _side_effect


# ---------------------------------------------------------------------------
# Group A — Fixture mechanics meta-tests
# ---------------------------------------------------------------------------


class TestFixtureMechanics:
    """A-01..A-05: rework_scenario fixture is correctly defined."""

    def test_scenario_count_is_four(self):
        """A-01: REWORK_SCENARIOS contains exactly 4 entries.

        The 4 production-failure cases that STORY-646 is designed to cover.
        If this fails, a scenario was accidentally removed.
        """
        assert len(REWORK_SCENARIOS) == 4, (
            f"Expected exactly 4 rework scenarios, got {len(REWORK_SCENARIOS)}.\n"
            "REWORK_SCENARIOS must cover: non-rework, canonical, slugged, frontend=True."
        )

    def test_first_scenario_is_non_rework(self):
        """A-02: First scenario has rework_of=None (baseline regression guard)."""
        first = REWORK_SCENARIOS[0]
        assert first.rework_of is None, (
            f"First scenario must be non-rework (rework_of=None). "
            f"Got rework_of={first.rework_of!r}. "
            "Move the non-rework baseline to index 0."
        )

    def test_slugged_branch_scenario_exists(self):
        """A-03: At least one scenario has a non-canonical (slugged) branch.

        The slugged-branch scenario is the specific production failure from
        STORY-637. Without it, the fixture doesn't cover the most important case.
        """
        slugged = [
            s for s in REWORK_SCENARIOS
            if s.rework_of is not None
            and s.target_branch_on_remote != f"story-{s.rework_of.split('-')[-1]}/story-{s.rework_of.split('-')[-1]}"
        ]
        assert len(slugged) >= 1, (
            "REWORK_SCENARIOS must include at least one scenario where the "
            "target branch is NOT the canonical formula (e.g. story-551/remediate-pre-deploy-gate).\n"
            "This is the production-bug case from STORY-637."
        )

    def test_frontend_true_scenario_exists(self):
        """A-04: At least one scenario has target_seed_frontend=True.

        Needed so AccDiff tests are forced to cover the Playwright-required path
        for rework stories whose rework target is a frontend story.
        """
        frontend = [s for s in REWORK_SCENARIOS if s.target_seed_frontend is True]
        assert len(frontend) >= 1, (
            "REWORK_SCENARIOS must include at least one scenario where "
            "target_seed_frontend=True. Without it, AccDiff tests can never "
            "catch a regression in the 'rework-of-frontend-story' code path."
        )

    def test_all_fields_have_correct_types(self):
        """A-05: Every ReworkScenario instance has correct field types."""
        for i, s in enumerate(REWORK_SCENARIOS):
            assert isinstance(s, ReworkScenario), (
                f"REWORK_SCENARIOS[{i}] is {type(s).__name__}, not ReworkScenario."
            )
            assert s.rework_of is None or isinstance(s.rework_of, str), (
                f"REWORK_SCENARIOS[{i}].rework_of must be str | None, got {type(s.rework_of)}"
            )
            assert isinstance(s.target_branch_on_remote, str), (
                f"REWORK_SCENARIOS[{i}].target_branch_on_remote must be str."
            )
            assert isinstance(s.target_seed_frontend, bool), (
                f"REWORK_SCENARIOS[{i}].target_seed_frontend must be bool."
            )
            assert isinstance(s.description, str) and s.description, (
                f"REWORK_SCENARIOS[{i}].description must be a non-empty str."
            )


# ---------------------------------------------------------------------------
# Group B — _expected_branch_for_story across all rework scenarios
# ---------------------------------------------------------------------------


class TestExpectedBranchAllScenarios:
    """B-01..B-04: _expected_branch_for_story handles every rework scenario.

    Each test runs 4× (once per scenario) via rework_scenario fixture.
    """

    def test_branch_resolution_matches_scenario(self, rework_scenario, tmp_path):
        """B-01: _expected_branch_for_story returns the scenario's expected branch.

        For non-rework: returns formula branch (story-999/story-999).
        For rework: returns target_branch_on_remote (ls-remote result or formula).
        """
        mod = _get_runner()
        fn = getattr(mod, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found in sdlc_phase_runner.py"

        with patch.object(mod.subprocess, "run",
                          side_effect=_ls_remote_mock(rework_scenario)):
            result = fn(
                str(tmp_path),
                "STORY-999",
                rework_of=rework_scenario.rework_of,
            )

        if rework_scenario.rework_of is None:
            expected = "story-999/story-999"
        else:
            expected = rework_scenario.target_branch_on_remote

        assert result == expected, (
            f"Scenario '{rework_scenario.description}':\n"
            f"  rework_of={rework_scenario.rework_of!r}\n"
            f"  expected  branch={expected!r}\n"
            f"  actual    branch={result!r}\n"
        )

    def test_non_rework_does_not_call_ls_remote(self, rework_scenario, tmp_path):
        """B-02: For non-rework scenario, git ls-remote must NOT be called.

        Verifies no expensive network call is made for normal dispatches.
        Only asserted for the non-rework scenario; others may call ls-remote.
        """
        if rework_scenario.rework_of is not None:
            pytest.skip("ls-remote assertion only applies to non-rework scenario")

        mod = _get_runner()
        fn = getattr(mod, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        ls_remote_calls: list = []

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                ls_remote_calls.append(cmd_list)
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            fn(str(tmp_path), "STORY-999", rework_of=None)

        assert len(ls_remote_calls) == 0, (
            "Scenario 'non-rework (regression baseline)':\n"
            f"  git ls-remote called {len(ls_remote_calls)} time(s); expected 0.\n"
            "  Non-rework stories must NOT make ls-remote network calls."
        )

    def test_slugged_rework_calls_ls_remote(self, rework_scenario, tmp_path):
        """B-03: For rework scenarios with a rework_of set, ls-remote IS called.

        This is the core protection against formula-only branch derivation.
        Only asserted for scenarios where rework_of is set.
        """
        if rework_scenario.rework_of is None:
            pytest.skip("ls-remote call assertion only applies to rework scenarios")

        mod = _get_runner()
        fn = getattr(mod, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        ls_remote_calls: list = []

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                ls_remote_calls.append(cmd_list)
                return _ok(stdout=f"abc\trefs/heads/{rework_scenario.target_branch_on_remote}\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            fn(str(tmp_path), "STORY-999", rework_of=rework_scenario.rework_of)

        assert len(ls_remote_calls) >= 1, (
            f"Scenario '{rework_scenario.description}':\n"
            f"  rework_of={rework_scenario.rework_of!r}\n"
            "  Expected git ls-remote to be called for rework dispatch, but it wasn't.\n"
            "  The function must use ls-remote to resolve the actual branch, not just formula."
        )

    def test_branch_result_includes_rework_prefix(self, rework_scenario, tmp_path):
        """B-04: Branch result always starts with 'story-' prefix.

        Basic sanity: every resolved branch name (formula or ls-remote)
        should start with the story number prefix.
        """
        mod = _get_runner()
        fn = getattr(mod, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        with patch.object(mod.subprocess, "run",
                          side_effect=_ls_remote_mock(rework_scenario)):
            result = fn(
                str(tmp_path),
                "STORY-999",
                rework_of=rework_scenario.rework_of,
            )

        assert result.startswith("story-"), (
            f"Scenario '{rework_scenario.description}':\n"
            f"  Branch {result!r} does not start with 'story-'.\n"
            "  All branch names must follow the 'story-N/...' convention."
        )


# ---------------------------------------------------------------------------
# Group C — _verify_acceptance_diff with rework scenarios
# ---------------------------------------------------------------------------


def _make_seed_content(story_id: str, frontend: bool) -> str:
    """Build a minimal seed.md for a given frontend flag."""
    frontend_str = "true" if frontend else "false"
    return (
        f"# Seed: {story_id}\n\n"
        "## Overview\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"| Mode | new_feature |\n"
        f"| Frontend | {frontend_str} |\n"
        f"| Scope | small |\n\n"
        "## Acceptance Diff\n\n"
        "- `deployment/hermes/sdlc_phase_runner.py` — core implementation\n"
    )


class TestVerifyAcceptanceDiffAllScenarios:
    """C-01..C-03: _verify_acceptance_diff applied across all rework scenarios.

    C-01: backend scenarios (target_seed_frontend=False) → Playwright NOT required.
    C-02: non-rework scenario → reads current story's seed (regression).
    C-03: frontend=True target scenario → Playwright IS required. [KEY NEW TEST]
    """

    def _write_target_seed(self, tmp_path: Path, scenario: ReworkScenario) -> None:
        """Write the rework target's seed.md into tmp_path/features/."""
        if scenario.rework_of is None:
            # Non-rework: write the current story's seed
            story_dir = tmp_path / "features" / "story-999-probe"
            story_dir.mkdir(parents=True, exist_ok=True)
            (story_dir / "seed.md").write_text(
                _make_seed_content("STORY-999", frontend=False)
            )
        else:
            # Rework: write the TARGET story's seed
            num = scenario.rework_of.split("-")[-1]
            # Use a folder name that _extract_story_folder would discover
            story_dir = tmp_path / "features" / f"story-{num}-target-seed"
            story_dir.mkdir(parents=True, exist_ok=True)
            (story_dir / "seed.md").write_text(
                _make_seed_content(scenario.rework_of, frontend=scenario.target_seed_frontend)
            )

    def test_backend_target_no_playwright_required(self, rework_scenario, tmp_path):
        """C-01: Scenarios where target seed has Frontend:false → Playwright not required.

        Covers non-rework, canonical-rework, and slugged-rework cases.
        The rework-with-frontend=True scenario is skipped here (covered by C-03).
        """
        if rework_scenario.target_seed_frontend:
            pytest.skip("Frontend=True scenario covered by C-03")

        mod = _get_runner()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found in sdlc_phase_runner.py"

        self._write_target_seed(tmp_path, rework_scenario)

        def _mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(stdout="deployment/hermes/sdlc_phase_runner.py\n")
            if "diff" in cmd_str:
                return _ok(stdout="+++ b/deployment/hermes/sdlc_phase_runner.py\n+fix\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_run):
            ok, missing = fn(
                str(tmp_path),
                "STORY-999",
                rework_of=rework_scenario.rework_of,
            )

        assert ok is True, (
            f"Scenario '{rework_scenario.description}':\n"
            f"  rework_of={rework_scenario.rework_of!r}, target_frontend=False\n"
            f"  Expected (True, []) — no Playwright spec required for backend target.\n"
            f"  Got ok={ok}, missing={missing!r}"
        )
        assert missing == [], (
            f"Scenario '{rework_scenario.description}': Expected empty missing list. "
            f"Got: {missing!r}"
        )

    def test_frontend_true_rework_target_requires_playwright(self, rework_scenario, tmp_path):
        """C-03 [KEY TEST]: rework with target_seed_frontend=True → Playwright required.

        This is the only scenario that covers: rework_of is set AND the rework
        target seed declares Frontend: true.

        Before STORY-646, no test exercised this path. An implementation bug
        that ignores the rework target's Frontend flag for the Playwright check
        would go undetected. This test closes that gap.

        Setup:
          - Write features/story-525-*/seed.md with ``| Frontend | true |``
          - git diff returns backend files only (no e2e/*.spec.ts)
          - rework_of="STORY-525"
        Expected:
          - (False, [...playwright...]) — Playwright spec IS required because
            the rework target is a frontend story
        """
        if not rework_scenario.target_seed_frontend:
            pytest.skip("This assertion only applies to the frontend=True scenario")

        mod = _get_runner()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        self._write_target_seed(tmp_path, rework_scenario)

        def _mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                # Backend-only diff — no e2e spec — should trigger missing-playwright
                return _ok(stdout="deployment/hermes/sdlc_phase_runner.py\n")
            if "diff" in cmd_str:
                return _ok(stdout="+++ b/deployment/hermes/sdlc_phase_runner.py\n+fix\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_run):
            ok, missing = fn(
                str(tmp_path),
                "STORY-999",
                rework_of=rework_scenario.rework_of,
            )

        assert ok is False, (
            f"Scenario '{rework_scenario.description}':\n"
            f"  rework_of={rework_scenario.rework_of!r}, target_frontend=True\n"
            "  Expected (False, ...) — Playwright spec MUST be required when rework\n"
            "  target seed has Frontend: true and no e2e spec is in the diff.\n"
            f"  Got ok={ok}, missing={missing!r}\n"
            "  Fix: _verify_acceptance_diff must read the rework TARGET's frontend\n"
            "  flag and apply the Playwright requirement accordingly."
        )
        assert any(
            "playwright" in str(m).lower() or "e2e" in str(m).lower()
            for m in missing
        ), (
            f"Scenario '{rework_scenario.description}':\n"
            f"  Expected 'playwright' or 'e2e' in missing list. Got: {missing!r}"
        )


# ---------------------------------------------------------------------------
# Group D — Output-variance tests
# ---------------------------------------------------------------------------


class TestOutputVariance:
    """D-01..D-02: _expected_branch_for_story output varies with input.

    Detects stub implementations that return the same hardcoded value
    regardless of the rework_of argument.
    """

    def test_rework_vs_non_rework_outputs_differ(self, tmp_path):
        """D-01: Non-rework output must differ from rework output.

        Calls _expected_branch_for_story with rework_of=None and
        rework_of='STORY-551' (slugged scenario). Results must differ.
        """
        mod = _get_runner()
        fn = getattr(mod, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        slugged_branch = "story-551/remediate-pre-deploy-gate"

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return _ok(stdout=f"abc\trefs/heads/{slugged_branch}\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            result_non_rework = fn(str(tmp_path), "STORY-999", rework_of=None)
            result_rework = fn(str(tmp_path), "STORY-999", rework_of="STORY-551")

        assert result_non_rework != result_rework, (
            "_expected_branch_for_story returned the same branch for both rework_of=None "
            f"and rework_of='STORY-551'.\n"
            f"  non-rework: {result_non_rework!r}\n"
            f"  rework:     {result_rework!r}\n"
            "A stub that returns a hardcoded value would fail this test."
        )

    def test_canonical_vs_slugged_rework_outputs_differ(self, tmp_path):
        """D-02: Canonical-formula rework output must differ from slugged-branch rework.

        Calls _expected_branch_for_story twice:
          - rework_of='STORY-589' with ls-remote returning story-589/story-589
          - rework_of='STORY-551' with ls-remote returning story-551/remediate-pre-deploy-gate

        Results must differ (different branch names returned).
        """
        mod = _get_runner()
        fn = getattr(mod, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        def _mock_canonical(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return _ok(stdout="abc\trefs/heads/story-589/story-589\n")
            return _ok()

        def _mock_slugged(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return _ok(stdout="abc\trefs/heads/story-551/remediate-pre-deploy-gate\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_canonical):
            result_canonical = fn(str(tmp_path), "STORY-999", rework_of="STORY-589")

        with patch.object(mod.subprocess, "run", side_effect=_mock_slugged):
            result_slugged = fn(str(tmp_path), "STORY-999", rework_of="STORY-551")

        assert result_canonical != result_slugged, (
            f"Canonical rework branch {result_canonical!r} must differ from "
            f"slugged rework branch {result_slugged!r}.\n"
            "Both are different rework targets with different ls-remote responses; "
            "a correct implementation returns the respective ls-remote output for each."
        )


# ---------------------------------------------------------------------------
# Group E — Consuming-file adoption (RED until Phase 8 refactors the files)
# ---------------------------------------------------------------------------

_TEST_DIR = Path(__file__).parent

_CONSUMING_FILES = {
    "test_phase_runner_branch_mismatch.py": (
        "Group D (D-01..D-06) tests _expected_branch_for_story with rework_of — "
        "those tests must be refactored to accept rework_scenario and run 4x."
    ),
    "test_phase_runner_acceptance_diff.py": (
        "Group C (C-01..C-04) tests _verify_acceptance_diff with rework_of — "
        "those tests must be refactored to accept rework_scenario and run 4x."
    ),
    "test_phase_runner_seed_path.py": (
        "Group B (B-01..B-04) tests run_sdlc_phases — audit for rework_of usage "
        "and parameterize any test that passes rework_of as a kwarg."
    ),
}


class TestConsumingFileAdoption:
    """E-01..E-03: Existing test files must use the rework_scenario fixture.

    These tests are RED until Phase 8 refactors the three consuming test files.
    They serve as a gate: the refactoring is not done until all three pass.

    Each test reads the consuming file's source text and asserts that:
      1. ``rework_scenario`` appears as a function parameter (the fixture is consumed).
      2. The fixture is invoked via pytest parameterization (not just mentioned in a
         comment or import).

    PHASE 8 INSTRUCTION:
    For each file in _CONSUMING_FILES, find every test method that:
      - Accepts ``rework_of`` as a parameter, OR
      - Hard-codes a specific rework story ID (e.g. 'STORY-621', 'STORY-551')

    Refactor those tests to accept ``rework_scenario`` as a fixture parameter
    and derive rework_of from ``rework_scenario.rework_of``.  The mock for
    ls-remote (where used) should use ``rework_scenario.target_branch_on_remote``.
    """

    def _check_file_uses_fixture(self, filename: str) -> tuple[bool, str]:
        """Return (uses_fixture, diagnostic_message)."""
        path = _TEST_DIR / filename
        if not path.exists():
            return False, f"{filename} does not exist"
        content = path.read_text()
        # The fixture must appear as a function parameter (def test_...(rework_scenario, ...)
        # or def test_...(self, rework_scenario, ...))
        import re
        pattern = r"def\s+test_\w+\s*\(\s*(?:self\s*,\s*)?rework_scenario"
        if re.search(pattern, content):
            return True, "fixture used as parameter"
        # Also accept: fixture used anywhere in a test signature
        if "rework_scenario" in content and "def test_" in content:
            # Check it's actually a parameter, not just a comment or string
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("def test_") and "rework_scenario" in stripped:
                    return True, "fixture used as parameter"
        return False, (
            f"No test method in {filename} accepts 'rework_scenario' as a parameter.\n"
            "Phase 8 must refactor tests that hard-code rework_of values to use the fixture."
        )

    def test_branch_mismatch_file_uses_rework_scenario(self):
        """E-01: test_phase_runner_branch_mismatch.py must use rework_scenario.

        This is RED until Phase 8 refactors the D-series tests (D-01..D-06)
        to accept rework_scenario and run 4× over all production-failure cases.

        Why this matters: before STORY-646, D-01 tests only one rework case
        (STORY-551). After refactoring, it runs for ALL 4 scenarios automatically,
        including future scenarios added to REWORK_SCENARIOS.
        """
        ok, msg = self._check_file_uses_fixture("test_phase_runner_branch_mismatch.py")
        assert ok, (
            "test_phase_runner_branch_mismatch.py does not use the rework_scenario fixture.\n"
            f"Diagnostic: {msg}\n"
            f"Context: {_CONSUMING_FILES['test_phase_runner_branch_mismatch.py']}"
        )

    def test_acceptance_diff_file_uses_rework_scenario(self):
        """E-02: test_phase_runner_acceptance_diff.py must use rework_scenario.

        This is RED until Phase 8 refactors the C-series tests (C-01..C-04)
        to accept rework_scenario and run 4× over all production-failure cases.

        Why this matters: C-01 currently tests only one rework case
        (STORY-621, Frontend:false). After refactoring, AccDiff tests will
        automatically cover the frontend=True case and future scenarios.
        """
        ok, msg = self._check_file_uses_fixture("test_phase_runner_acceptance_diff.py")
        assert ok, (
            "test_phase_runner_acceptance_diff.py does not use the rework_scenario fixture.\n"
            f"Diagnostic: {msg}\n"
            f"Context: {_CONSUMING_FILES['test_phase_runner_acceptance_diff.py']}"
        )

    def test_seed_path_file_uses_rework_scenario_or_has_no_rework_tests(self):
        """E-03: test_phase_runner_seed_path.py: use fixture OR confirm no rework tests.

        This file may not have rework_of-parameterized tests. If it does, they
        must use the fixture. If it doesn't, the test passes (no refactoring needed).

        Phase 8 must audit this file:
          - If any test passes rework_of='STORY-N' → refactor to use rework_scenario.
          - If no test uses rework_of → leave as-is and mark this test as N/A.
        """
        path = _TEST_DIR / "test_phase_runner_seed_path.py"
        if not path.exists():
            pytest.skip("test_phase_runner_seed_path.py not found — skip audit")

        content = path.read_text()

        # Check if file has hardcoded rework_of usage
        import re
        hardcoded_rework = re.search(r"""rework_of\s*=\s*['"][A-Z]+-\d+['"]""", content)

        if hardcoded_rework is None:
            # No hardcoded rework_of — file doesn't need the fixture
            return  # Test passes

        # File has hardcoded rework_of — must use fixture
        ok, msg = self._check_file_uses_fixture("test_phase_runner_seed_path.py")
        assert ok, (
            "test_phase_runner_seed_path.py has hard-coded rework_of values "
            "but does NOT use the rework_scenario fixture.\n"
            f"Diagnostic: {msg}\n"
            "Phase 8 must refactor those tests to use rework_scenario."
        )
