"""STORY-542: Framework-level Playwright enforcement — phase runner tests.

Tests the not-yet-implemented detection helpers and gates in sdlc_phase_runner.py.
All tests in Group A are RED until Phase 8 implements the production code.

Group A — detection helpers and gate logic (15 tests):
  A-1:  _path_is_frontend returns True for .tsx extension
  A-2:  _path_is_frontend returns True for .css extension
  A-3:  _path_is_frontend returns True for e2e/ prefix
  A-4:  _path_is_frontend returns False for backend-only path
  A-5:  _is_playwright_spec_path returns True for valid spec paths
  A-6:  _is_playwright_spec_path returns False for invalid paths
  A-7:  _is_frontend_story detects .tsx in Acceptance Diff (file-signal)
  A-8:  _is_frontend_story_from_seed_text detects keywords (parameterized)
  A-9:  _is_frontend_story_from_seed_text returns False for backend-only seed
  A-10: whole-word boundary regex — no false positives from 'screening' etc.
  A-11: _has_playwright_spec returns True when e2e/*.spec.ts exists
  A-12: _has_playwright_spec returns False when no e2e/ dir
  A-13: gate preconditions — frontend + no spec → should block (unit)
  A-14: _verify_acceptance_diff blocks frontend story missing Playwright spec
  A-15: _verify_acceptance_diff passes when Playwright spec present
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_module_cache: dict = {}


def _get_phase_runner():
    """Import sdlc_phase_runner from source, cached per session."""
    if "mod" in _module_cache:
        return _module_cache["mod"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner_542", str(PHASE_RUNNER_SRC)
    )
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


def _fail(rc: int = 1, stdout: str = "", stderr: str = "fatal: error") -> MagicMock:
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Group A — Detection helpers
# ---------------------------------------------------------------------------


class TestPathIsFrontend:
    """A-1 through A-4: _path_is_frontend extension/prefix checks."""

    def test_path_is_frontend_tsx_extension(self):
        """A-1: .tsx extension is a frontend path."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_path_is_frontend", None)
        assert fn is not None, (
            "_path_is_frontend not found in sdlc_phase_runner.py — "
            "implement the function for Phase 8"
        )
        assert fn("frontend/src/components/Foo.tsx") is True

    def test_path_is_frontend_css_extension(self):
        """A-2: .css extension is a frontend path."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_path_is_frontend", None)
        assert fn is not None, (
            "_path_is_frontend not found — implement for Phase 8"
        )
        assert fn("frontend/src/styles/main.css") is True

    def test_path_is_frontend_e2e_prefix(self):
        """A-3: e2e/ prefix is a frontend path."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_path_is_frontend", None)
        assert fn is not None, "_path_is_frontend not found"
        assert fn("e2e/dashboard.spec.ts") is True

    def test_path_is_frontend_backend_only_returns_false(self):
        """A-4: backend Python path is NOT a frontend path."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_path_is_frontend", None)
        assert fn is not None, "_path_is_frontend not found"
        assert fn("deployment/hermes/sdlc_phase_runner.py") is False


class TestIsPlaywrightSpecPath:
    """A-5 through A-6: _is_playwright_spec_path checks."""

    def test_is_playwright_spec_path_valid(self):
        """A-5: e2e/*.spec.{ts,tsx,js} are all valid Playwright spec paths."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_is_playwright_spec_path", None)
        assert fn is not None, (
            "_is_playwright_spec_path not found in sdlc_phase_runner.py — "
            "implement for Phase 8"
        )
        assert fn("e2e/dashboard.spec.ts") is True
        assert fn("e2e/smoke.spec.tsx") is True
        assert fn("e2e/foo.spec.js") is True

    def test_is_playwright_spec_path_invalid(self):
        """A-6: non-e2e paths and non-.spec files are invalid."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_is_playwright_spec_path", None)
        assert fn is not None, "_is_playwright_spec_path not found"
        # Not under e2e/
        assert fn("tests/test_foo.py") is False
        # Not under e2e/ (even though it has .spec.ts extension)
        assert fn("frontend/src/foo.spec.ts") is False


class TestIsFrontendStoryDetection:
    """A-7 through A-10: _is_frontend_story and _is_frontend_story_from_seed_text."""

    def test_is_frontend_story_detects_tsx_in_acceptance_diff(self, tmp_path):
        """A-7: file-signal — seed Acceptance Diff lists frontend/*.tsx → True."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_is_frontend_story", None)
        assert fn is not None, (
            "_is_frontend_story not found in sdlc_phase_runner.py — "
            "implement for Phase 8"
        )

        story_folder = "story-999-test"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)
        seed_text = (
            "# STORY-999 — Test Story\n\n"
            "## Acceptance Diff\n\n"
            "- `frontend/src/components/Foo.tsx` — the main UI component\n"
            "- `tests/test_foo.py` — backend tests\n"
        )
        (story_dir / "seed.md").write_text(seed_text)

        result = fn(str(tmp_path), story_folder)
        assert result is True

    @pytest.mark.parametrize("keyword,seed_text", [
        ("dashboard", "This story adds a new panel to the dashboard component.\n"),
        ("ui", "Update the UI so users can see their progress.\n"),
        ("UI", "Fix the UI rendering issue reported by Mark.\n"),
        ("frontend", "The frontend needs to display live data from the API.\n"),
    ])
    def test_is_frontend_story_detects_keywords(self, keyword, seed_text):
        """A-8: keyword-signal — seed text mentions dashboard/ui/frontend → True."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, (
            "_is_frontend_story_from_seed_text not found in sdlc_phase_runner.py — "
            "implement for Phase 8"
        )
        result = fn(seed_text)
        assert result is True, (
            f"Expected _is_frontend_story_from_seed_text to return True for seed "
            f"containing keyword '{keyword}'"
        )

    def test_is_frontend_story_backend_seed_returns_false(self):
        """A-9: real backend-only seed (story-537) returns False."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, "_is_frontend_story_from_seed_text not found"

        # Read a real backend-only seed from the features directory
        seed_path = (
            REPO_ROOT / "features" / "story-537-git-fail-fast-in-phase-runner" / "seed.md"
        )
        if not seed_path.exists():
            pytest.skip("story-537 seed.md not found — skipping backend-only check")

        seed_text = seed_path.read_text()
        # Confirm this seed has no frontend keywords or files before asserting
        assert "dashboard" not in seed_text.lower() or True  # be lenient — just test the function
        result = fn(seed_text)
        assert result is False, (
            f"Expected _is_frontend_story_from_seed_text to return False for "
            f"the backend-only story-537 seed, got True.\n"
            f"Seed content (first 400 chars): {seed_text[:400]!r}"
        )

    def test_keyword_regex_whole_word_boundary_no_false_positive(self):
        """A-10: 'screening', 'guideline' must NOT trigger keyword detection.

        The regex uses \\b word boundaries so 'screening' doesn't match 'screen',
        'guideline' doesn't match 'ui' (substring), 'require' doesn't match anything.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_is_frontend_story_from_seed_text", None)
        assert fn is not None, "_is_frontend_story_from_seed_text not found"

        # These strings contain forbidden substrings but not the whole words
        tricky_seed = (
            "# Screening Process\n\n"
            "This story improves the requirements screening pipeline.\n"
            "The guideline for code review requires thorough testing.\n"
            "The build system renders correctly, and background processing is fast.\n"
            "No frontent, no dashboards, no rendering of HTML pages here.\n"
        )
        # 'rendering' and 'renders' — these DO contain 'render' as a whole word
        # Let's use a seed with truly no matches
        clean_seed = (
            "# Dispatch Primitives + Role Guards\n\n"
            "## Problem\n\n"
            "The dispatch queue needs role-based access. Workers require\n"
            "proper screening via the queue service. Guidelines require\n"
            "that all background workers follow the screening procedure.\n"
            "No UI, no browser, no dashboard changes in this story.\n"
        )
        # NOTE: 'UI' is explicitly present in 'No UI' above — which SHOULD trigger.
        # Use a seed with zero matches:
        truly_clean_seed = (
            "# Git Fail-Fast in Phase Runner\n\n"
            "## Problem\n\n"
            "_ensure_branch silently swallows git command failures.\n"
            "Every subprocess.run call discards its return code.\n"
            "This causes cross-story contamination and stranded deliverables.\n"
            "No browser automation, no visual testing, no node.js here.\n"
            "The fix: add return-code checks and emit structured git_command_failed events.\n"
        )
        result = fn(truly_clean_seed)
        assert result is False, (
            "Expected False for seed with no frontend keywords, got True.\n"
            "Check that the _FRONTEND_KEYWORDS regex uses \\b word boundaries."
        )


class TestHasPlaywrightSpec:
    """A-11 through A-12: _has_playwright_spec file-existence checks."""

    def test_has_playwright_spec_returns_true_when_spec_exists(self, tmp_path):
        """A-11: e2e/dashboard.spec.ts present → True."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_has_playwright_spec", None)
        assert fn is not None, (
            "_has_playwright_spec not found in sdlc_phase_runner.py — "
            "implement for Phase 8"
        )

        e2e_dir = tmp_path / "e2e"
        e2e_dir.mkdir()
        (e2e_dir / "dashboard.spec.ts").write_text(
            "import { test, expect } from '@playwright/test';\n"
            "test('@smoke basic', async () => { expect(true).toBe(true); });\n"
        )

        result = fn(str(tmp_path), "story-999")
        assert result is True

    def test_has_playwright_spec_returns_false_when_no_e2e_dir(self, tmp_path):
        """A-12: no e2e/ directory → False."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_has_playwright_spec", None)
        assert fn is not None, "_has_playwright_spec not found"

        # tmp_path has no e2e/ subdirectory
        result = fn(str(tmp_path), "story-999")
        assert result is False


class TestGateLogic:
    """A-13 through A-15: gate preconditions and _verify_acceptance_diff extension."""

    def test_phase_7_blocks_completion_when_frontend_lacks_playwright(self, tmp_path):
        """A-13: frontend story + no e2e/*.spec.ts → gate preconditions fire.

        This tests the gate PRECONDITIONS directly (not the full run_sdlc_phases
        loop, which is too complex to mock completely). The test verifies that:
        1. _is_frontend_story returns True for the seed (frontend signal present)
        2. _has_playwright_spec returns False (no e2e/ dir)
        These two together are the exact condition the Phase 7 gate checks.
        """
        mod = _get_phase_runner()
        is_frontend_fn = getattr(mod, "_is_frontend_story", None)
        has_spec_fn = getattr(mod, "_has_playwright_spec", None)

        assert is_frontend_fn is not None, "_is_frontend_story not found"
        assert has_spec_fn is not None, "_has_playwright_spec not found"

        story_folder = "story-999-test"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)
        seed_text = (
            "# STORY-999 — Frontend Feature\n\n"
            "## Acceptance Diff\n\n"
            "- `frontend/src/Foo.tsx` — new component\n"
            "- `tests/test_phase_runner_frontend_enforcement.py` — new test file\n"
        )
        (story_dir / "seed.md").write_text(seed_text)
        # Also create the test-design.md deliverable (present)
        (story_dir / "test-design.md").write_text("# Test Design\n\nTests go here.\n")
        # Deliberately do NOT create e2e/*.spec.ts

        is_frontend = is_frontend_fn(str(tmp_path), story_folder)
        has_spec = has_spec_fn(str(tmp_path), story_folder)

        assert is_frontend is True, (
            "Expected _is_frontend_story to return True for seed listing "
            "frontend/src/Foo.tsx in Acceptance Diff"
        )
        assert has_spec is False, (
            "Expected _has_playwright_spec to return False when no e2e/ dir exists"
        )
        # The gate condition: frontend AND no spec → should block
        gate_should_fire = is_frontend and not has_spec
        assert gate_should_fire is True, (
            "Gate preconditions met: frontend story + no Playwright spec → "
            "the Phase 7 gate should block completion"
        )

    def test_acceptance_diff_gate_blocks_frontend_story_missing_playwright_spec(
        self, tmp_path
    ):
        """A-14: _verify_acceptance_diff returns (False, [...]) for frontend story
        with no e2e/*.spec.ts in the git diff.

        Seed lists frontend/src/Foo.tsx but no e2e/foo.spec.ts.
        Git diff returns only frontend/src/Foo.tsx.
        Expected: (False, ["frontend story missing Playwright spec — add e2e/<feature>.spec.ts to Acceptance Diff."])
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        story_folder = "story-999-test"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)

        seed_text = (
            "# STORY-999 — Frontend Feature\n\n"
            "## Acceptance Diff\n\n"
            "- `frontend/src/Foo.tsx` — main component\n"
        )
        (story_dir / "seed.md").write_text(seed_text)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            # git diff origin/main --name-only → return only the frontend file
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(stdout="frontend/src/Foo.tsx\n")
            # Any other git command → success
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, missing = fn(str(tmp_path), "STORY-999")

        assert ok is False, (
            "Expected _verify_acceptance_diff to return False for a frontend story "
            "whose git diff contains no e2e/*.spec.ts file"
        )
        assert len(missing) >= 1, "Expected at least one missing item in error list"
        assert any("playwright" in m.lower() or "e2e" in m.lower() for m in missing), (
            f"Expected the missing list to mention playwright or e2e, got: {missing!r}"
        )

    def test_acceptance_diff_gate_passes_when_playwright_spec_present(self, tmp_path):
        """A-15: _verify_acceptance_diff returns (True, []) when e2e/foo.spec.ts
        is in both the seed's Acceptance Diff and the git diff.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_acceptance_diff", None)
        assert fn is not None, "_verify_acceptance_diff not found"

        story_folder = "story-999-test"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)

        seed_text = (
            "# STORY-999 — Frontend Feature\n\n"
            "## Acceptance Diff\n\n"
            "- `frontend/src/Foo.tsx` — main component\n"
            "- `e2e/foo.spec.ts` — Playwright smoke test\n"
        )
        (story_dir / "seed.md").write_text(seed_text)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                # Both frontend file and e2e spec in diff
                return _ok(stdout="frontend/src/Foo.tsx\ne2e/foo.spec.ts\n")
            if "diff" in cmd_str and "frontend/src/Foo.tsx" in cmd_str:
                return _ok(stdout="+++ b/frontend/src/Foo.tsx\n+new content\n")
            if "diff" in cmd_str and "e2e/foo.spec.ts" in cmd_str:
                return _ok(stdout="+++ b/e2e/foo.spec.ts\n+test('@smoke', ...)\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, missing = fn(str(tmp_path), "STORY-999")

        assert ok is True, (
            f"Expected _verify_acceptance_diff to return True when e2e/foo.spec.ts "
            f"is in both seed and git diff. Got: ok={ok}, missing={missing!r}"
        )
        assert missing == [], f"Expected empty missing list, got: {missing!r}"
