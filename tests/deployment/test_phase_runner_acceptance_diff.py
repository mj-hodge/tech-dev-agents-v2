"""STORY-642 (BUG 2): AccDiff frontend classification reads rework target seed.

The production failure (2026-04-26 00:52 UTC, STORY-626):
    STORY-626 reworks STORY-621 (a backend bug fix; seed has ``Frontend: false``).
    The agent did real backend work, but the AccDiff guard demanded a Playwright spec.

Root cause: ``_verify_acceptance_diff`` read the current story's seed for frontend
detection. For reworks, the relevant seed is the rework TARGET's seed. If the rework
target has ``| Frontend | false |``, no Playwright spec should be required.

This test file also covers the explicit ``| Frontend |`` flag in seed tables
overriding the keyword/path heuristics in ``_is_frontend_story_from_seed_text``.

Test groups:

    Group A — _parse_frontend_flag_from_seed helper
        A-01  table row ``| Frontend | false |`` → returns False
        A-02  table row ``| Frontend | true |`` → returns True
        A-03  table row absent → returns None (caller falls back to heuristics)
        A-04  case-insensitive: ``| Frontend | False |`` → False

    Group B — _is_frontend_story_from_seed_text respects explicit flag
        B-01  explicit ``Frontend: false`` in seed → False (overrides keywords)
        B-02  explicit ``Frontend: true`` in seed with no keywords → True
        B-03  no explicit flag, frontend keyword present → True (heuristic unchanged)
        B-04  no explicit flag, backend-only seed → False (heuristic unchanged)

    Group C — _verify_acceptance_diff uses rework target seed
        C-01  rework story with rework_of pointing at Frontend:false seed → no Playwright req (BUG FIX)
        C-02  non-rework story with Frontend:true seed → Playwright required (regression)
        C-03  non-rework story with Frontend:false seed → no Playwright requirement
        C-04  rework target seed missing → falls back, no crash
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_module_cache: dict = {}


def _get_mod():
    if "mod" in _module_cache:
        return _module_cache["mod"]
    spec = importlib.util.spec_from_file_location("sdlc_phase_runner_accdiff", str(PHASE_RUNNER_SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


def _fail(rc: int = 1, stdout: str = "", stderr: str = "fatal") -> MagicMock:
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Group A — _parse_frontend_flag_from_seed
# ---------------------------------------------------------------------------


class TestParseFrontendFlag:
    """A-01 through A-04: explicit Frontend table row parsing."""

    def test_frontend_false_returns_false(self):
        """A-01: ``| Frontend | false |`` → False."""
        mod = _get_mod()
        fn = getattr(mod, "_parse_frontend_flag_from_seed", None)
        assert fn is not None, (
            "_parse_frontend_flag_from_seed not found in sdlc_phase_runner.py — "
            "implement for STORY-642 BUG 2"
        )
        seed = (
            "# Seed: STORY-621\n\n"
            "## Overview\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Mode | bug_fix |\n"
            "| Frontend | false |\n"
            "| Scope | small |\n"
        )
        result = fn(seed)
        assert result is False, (
            f"Expected False for seed with '| Frontend | false |', got {result!r}"
        )

    def test_frontend_true_returns_true(self):
        """A-02: ``| Frontend | true |`` → True."""
        mod = _get_mod()
        fn = getattr(mod, "_parse_frontend_flag_from_seed", None)
        assert fn is not None, "_parse_frontend_flag_from_seed not found"
        seed = (
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Frontend | true |\n"
        )
        result = fn(seed)
        assert result is True, (
            f"Expected True for seed with '| Frontend | true |', got {result!r}"
        )

    def test_absent_row_returns_none(self):
        """A-03: no Frontend row → None (fall back to heuristics)."""
        mod = _get_mod()
        fn = getattr(mod, "_parse_frontend_flag_from_seed", None)
        assert fn is not None, "_parse_frontend_flag_from_seed not found"
        seed = (
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Mode | bug_fix |\n"
            "| Scope | small |\n"
        )
        result = fn(seed)
        assert result is None, (
            f"Expected None when Frontend row is absent, got {result!r}"
        )

    def test_case_insensitive_false(self):
        """A-04: ``| Frontend | False |`` (capital F) → False."""
        mod = _get_mod()
        fn = getattr(mod, "_parse_frontend_flag_from_seed", None)
        assert fn is not None, "_parse_frontend_flag_from_seed not found"
        seed = "| Frontend | False |\n"
        result = fn(seed)
        assert result is False, (
            f"Expected False for '| Frontend | False |', got {result!r}"
        )


# ---------------------------------------------------------------------------
# Group B — _is_frontend_story_from_seed_text respects explicit flag
# ---------------------------------------------------------------------------


class TestIsFrontendStoryExplicitFlag:
    """B-01 through B-04: explicit flag overrides heuristics."""

    def test_explicit_false_overrides_keywords(self):
        """B-01: ``| Frontend | false |`` + frontend keywords → False.

        This is the exact pattern in STORY-626's rework target seed: the story
        mentions 'dashboard' in passing but the explicit flag says false.
        """
        mod = _get_mod()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, "_is_frontend_story_from_seed_text not found"
        seed = (
            "# Seed: Backend Bug Fix\n\n"
            "## Overview\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Frontend | false |\n\n"
            "This story fixes a backend reconciler. No dashboard changes.\n"
            "The UI is not affected. Frontend remains unchanged.\n"
        )
        result = fn(seed)
        assert result is False, (
            f"Expected False: explicit '| Frontend | false |' must override "
            f"keyword heuristics even when seed text mentions 'dashboard'/'UI'. "
            f"Got {result!r}."
        )

    def test_explicit_true_with_no_keywords(self):
        """B-02: ``| Frontend | true |`` with no keywords → True."""
        mod = _get_mod()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, "_is_frontend_story_from_seed_text not found"
        seed = (
            "| Frontend | true |\n\n"
            "This story adds a new button to the nav bar.\n"
        )
        result = fn(seed)
        assert result is True, (
            f"Expected True: explicit '| Frontend | true |'. Got {result!r}."
        )

    def test_no_flag_keyword_heuristic_still_works(self):
        """B-03: no explicit flag + keyword present → True (heuristic unchanged)."""
        mod = _get_mod()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, "_is_frontend_story_from_seed_text not found"
        seed = "This story updates the dashboard panel with live data.\n"
        result = fn(seed)
        assert result is True, (
            f"Expected True for seed with 'dashboard' keyword (no explicit flag). "
            f"Got {result!r}."
        )

    def test_no_flag_backend_seed_false(self):
        """B-04: no explicit flag + backend-only seed → False (heuristic unchanged)."""
        mod = _get_mod()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, "_is_frontend_story_from_seed_text not found"
        seed = (
            "This story fixes git error handling in the phase runner.\n"
            "No browser, no HTML, no TypeScript, no node.\n"
        )
        result = fn(seed)
        assert result is False, (
            f"Expected False for backend-only seed with no explicit flag. Got {result!r}."
        )


# ---------------------------------------------------------------------------
# Group C — _verify_acceptance_diff uses rework target seed
# ---------------------------------------------------------------------------


class TestVerifyAcceptanceDiffReworkOf:
    """C-01 through C-04: rework_of kwarg routes frontend detection."""

    def test_rework_of_backend_seed_no_playwright_required(self, tmp_path):
        """C-01: BUG FIX — rework story with rework_of pointing at Frontend:false seed.

        STORY-626 reworks STORY-621 (Frontend: false). The AccDiff guard must NOT
        require a Playwright spec.

        Setup:
          - features/story-621-backend-fix/seed.md has ``| Frontend | false |``
            and Acceptance Diff lists backend Python files only.
          - git diff returns backend Python files only (no e2e/*.spec.ts).
          - rework_of="STORY-621"
        Expected: (True, []) — no Playwright requirement.
        """
        mod = _get_mod()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        # Create the rework TARGET's seed (story-621)
        story_dir = tmp_path / "features" / "story-621-backend-fix"
        story_dir.mkdir(parents=True)
        backend_seed = (
            "# Seed: STORY-621 — Backend Bug Fix\n\n"
            "## Overview\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Mode | bug_fix |\n"
            "| Frontend | false |\n"
            "| Scope | small |\n\n"
            "## Acceptance Diff\n\n"
            "- `deployment/hermes/sdlc_phase_runner.py` — fix reconciler\n"
        )
        (story_dir / "seed.md").write_text(backend_seed)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(stdout="deployment/hermes/sdlc_phase_runner.py\n")
            if "diff" in cmd_str:
                return _ok(stdout="+++ b/deployment/hermes/sdlc_phase_runner.py\n+fix\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, missing = fn(str(tmp_path), "STORY-626", rework_of="STORY-621")

        assert ok is True, (
            f"BUG 2 regression: _verify_acceptance_diff returned (False, {missing!r}) "
            f"for STORY-626 (rework_of=STORY-621, Frontend:false). "
            f"The guard must NOT require a Playwright spec for backend reworks."
        )
        assert missing == [], f"Expected empty missing list, got: {missing!r}"

    def test_non_rework_frontend_seed_requires_playwright(self, tmp_path):
        """C-02: non-rework story with Frontend:true seed → Playwright required.

        Regression guard: the existing behavior for non-rework frontend stories
        must be preserved.
        """
        mod = _get_mod()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        story_dir = tmp_path / "features" / "story-700-dashboard-panel"
        story_dir.mkdir(parents=True)
        frontend_seed = (
            "# Seed: STORY-700\n\n"
            "## Overview\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Frontend | true |\n\n"
            "## Acceptance Diff\n\n"
            "- `frontend/src/components/Panel.tsx` — new panel component\n"
        )
        (story_dir / "seed.md").write_text(frontend_seed)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                # Only the frontend file changed; no e2e spec
                return _ok(stdout="frontend/src/components/Panel.tsx\n")
            if "diff" in cmd_str:
                return _ok(stdout="+++ b/frontend/src/components/Panel.tsx\n+new\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, missing = fn(str(tmp_path), "STORY-700")

        assert ok is False, (
            "Regression: non-rework frontend story missing Playwright spec must still "
            f"return (False, ...). Got ok={ok}, missing={missing!r}."
        )
        assert any("playwright" in m.lower() or "e2e" in m.lower() for m in missing), (
            f"Expected Playwright/e2e mention in missing list, got: {missing!r}"
        )

    def test_non_rework_backend_seed_no_playwright_required(self, tmp_path):
        """C-03: non-rework story with Frontend:false seed → no Playwright requirement."""
        mod = _get_mod()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        story_dir = tmp_path / "features" / "story-750-backend-fix"
        story_dir.mkdir(parents=True)
        backend_seed = (
            "# Seed: STORY-750\n\n"
            "## Overview\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Frontend | false |\n\n"
            "## Acceptance Diff\n\n"
            "- `deployment/hermes/dispatch_poller.py` — fix retry logic\n"
        )
        (story_dir / "seed.md").write_text(backend_seed)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(stdout="deployment/hermes/dispatch_poller.py\n")
            if "diff" in cmd_str:
                return _ok(stdout="+++ b/deployment/hermes/dispatch_poller.py\n+fix\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, missing = fn(str(tmp_path), "STORY-750")

        assert ok is True, (
            f"Expected (True, []) for non-rework backend story (Frontend:false). "
            f"Got ok={ok}, missing={missing!r}."
        )
        assert missing == [], f"Expected empty missing list, got: {missing!r}"

    def test_rework_target_seed_missing_falls_back_no_crash(self, tmp_path):
        """C-04: rework_of set but target seed missing → falls back, no crash.

        When the rework target's seed doesn't exist, the function should either
        fall back to the current story's seed or return (True, []) — it must not
        raise an exception or block legitimate completions.
        """
        mod = _get_mod()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        # No features/ directory at all — no seeds
        # rework_of points at a non-existent story
        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(stdout="deployment/hermes/sdlc_phase_runner.py\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            try:
                ok, missing = fn(str(tmp_path), "STORY-800", rework_of="STORY-799")
            except Exception as exc:
                pytest.fail(
                    f"_verify_acceptance_diff raised {type(exc).__name__}: {exc} "
                    "when rework target seed is missing. Must not crash."
                )

        # With no seed, should fail-open (True, [])
        assert ok is True, (
            f"Expected fail-open (True, []) when rework target seed is missing. "
            f"Got ok={ok}, missing={missing!r}."
        )

    def test_all_rework_scenarios_classify_frontend_correctly(self, rework_scenario, tmp_path):
        """C-05 (STORY-646): _verify_acceptance_diff classifies frontend for all scenarios.

        Parameterized over all 4 REWORK_SCENARIOS via rework_scenario fixture.
        Before STORY-646, only specific hardcoded cases were tested (C-01..C-04).
        This test runs automatically for any new scenario added to REWORK_SCENARIOS.

        For scenarios where target_seed_frontend=False → Playwright NOT required.
        For scenarios where target_seed_frontend=True  → Playwright IS required
          (when diff contains no e2e/*.spec.ts file).
        """
        mod = _get_mod()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        # Write the appropriate seed for this scenario
        if rework_scenario.rework_of:
            num = rework_scenario.rework_of.split("-")[-1]
            story_dir = tmp_path / "features" / f"story-{num}-target"
        else:
            story_dir = tmp_path / "features" / "story-999-probe"
        story_dir.mkdir(parents=True)
        frontend_val = "true" if rework_scenario.target_seed_frontend else "false"
        (story_dir / "seed.md").write_text(
            f"# Seed\n\n"
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"| Frontend | {frontend_val} |\n\n"
            f"## Acceptance Diff\n\n"
            f"- `deployment/hermes/sdlc_phase_runner.py` — core\n"
        )

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                # Backend-only diff — no e2e spec
                return _ok(stdout="deployment/hermes/sdlc_phase_runner.py\n")
            if "diff" in cmd_str:
                return _ok(stdout="+++ b/deployment/hermes/sdlc_phase_runner.py\n+fix\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, missing = fn(str(tmp_path), "STORY-999", rework_of=rework_scenario.rework_of)

        if rework_scenario.target_seed_frontend:
            assert ok is False, (
                f"Scenario '{rework_scenario.description}':\n"
                f"  target_seed_frontend=True, backend-only diff → Playwright REQUIRED.\n"
                f"  Got ok={ok}, missing={missing!r}"
            )
            assert any(
                "playwright" in str(m).lower() or "e2e" in str(m).lower()
                for m in missing
            ), (
                f"Scenario '{rework_scenario.description}':\n"
                f"  Expected 'playwright'/'e2e' in missing list. Got: {missing!r}"
            )
        else:
            assert ok is True, (
                f"Scenario '{rework_scenario.description}':\n"
                f"  target_seed_frontend=False → Playwright NOT required.\n"
                f"  Got ok={ok}, missing={missing!r}"
            )
            assert missing == [], (
                f"Scenario '{rework_scenario.description}':\n"
                f"  Expected empty missing list. Got: {missing!r}"
            )
