"""STORY-539: Research Dispatch Scope — Phase map and runner tests.

RED state summary
-----------------
All Group A and B tests FAIL until Phase 8 adds:
  - PHASES_RESEARCH = [(2, "Research", "research.md", "/phase-2 ...", 50)]
  - "research": PHASES_RESEARCH  in PHASE_MAP

Group A — PHASE_MAP["research"] structure (pure module inspection)
  A-01: test_research_scope_has_only_phase_2
  A-02: test_research_deliverable_is_research_md
  A-03: test_research_phase_number_is_2
  A-04: test_research_phase_name_is_research
  A-05: test_research_phase_prompt_starts_with_phase_2
  A-06: test_research_phase_prompt_passes_story_context
  A-07: test_research_max_turns_is_concrete_int

Group B — run_sdlc_phases behavior for scope="research"
  B-01: test_run_sdlc_phases_research_logs_correct_phase_count
  B-02: test_research_scope_never_runs_phase_1

Group C — Regression: existing scopes unchanged
  C-01: test_small_scope_unchanged
  C-02: test_medium_scope_unchanged
  C-03: test_phase_map_keys_include_research
"""

from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Locate and load sdlc_phase_runner
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), (
    f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC} — "
    "is this running from the repo root?"
)

_phase_runner_module_cache: dict = {}


def _get_phase_runner():
    """Import sdlc_phase_runner as a module, caching between test calls."""
    if "module" in _phase_runner_module_cache:
        return _phase_runner_module_cache["module"]

    import importlib.util

    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)

    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _phase_runner_module_cache["module"] = mod
    return mod


def _get_phase_map():
    """Return the PHASE_MAP from the phase runner module."""
    mod = _get_phase_runner()
    phase_map = getattr(mod, "PHASE_MAP", None)
    assert phase_map is not None, "PHASE_MAP not found in sdlc_phase_runner.py"
    return phase_map


# ---------------------------------------------------------------------------
# Group A — PHASE_MAP["research"] structure
# ---------------------------------------------------------------------------


class TestResearchPhasePlan:
    """A-01 through A-07: PHASE_MAP["research"] must exist and have the right shape.

    RED: PHASE_MAP has no "research" key — all tests in this class FAIL with
         a descriptive pytest.fail() message until Phase 8 adds PHASES_RESEARCH.
    """

    def _get_research_phases(self):
        """Retrieve PHASE_MAP["research"], failing with a clear RED message if absent."""
        phase_map = _get_phase_map()
        phases = phase_map.get("research")
        if phases is None:
            pytest.fail(
                'PHASE_MAP["research"] does not exist in sdlc_phase_runner.py.\n\n'
                "Phase 8 must add:\n"
                "  PHASES_RESEARCH = [\n"
                '      (2, "Research", "research.md", "/phase-2 story_id={story_id} '
                'repo={repo} story_folder={story_folder}", 50),\n'
                "  ]\n"
                '  PHASE_MAP["research"] = PHASES_RESEARCH\n'
                "\n(STORY-539 Phase 8)"
            )
        return phases

    def test_research_scope_has_only_phase_2(self):
        """A-01: PHASE_MAP["research"] is a list of exactly one tuple (Phase 2 only)."""
        phases = self._get_research_phases()
        assert len(phases) == 1, (
            f'PHASE_MAP["research"] has {len(phases)} phase(s), expected exactly 1.\n'
            "Research scope runs only Phase 2 (Research) — no Seed, Test Design, or Implementation.\n"
            f"Got: {phases}"
        )

    def test_research_deliverable_is_research_md(self):
        """A-02: The single research phase has deliverable_file "research.md"."""
        phases = self._get_research_phases()
        phase_num, phase_name, deliverable, prompt, max_turns = phases[0]
        assert deliverable == "research.md", (
            f'PHASE_MAP["research"][0] deliverable is {deliverable!r}, expected "research.md".\n'
            "The research phase must produce research.md as its sole deliverable.\n"
            "Phase 8 must set the deliverable tuple field to 'research.md'."
        )

    def test_research_phase_number_is_2(self):
        """A-03: The research phase tuple's phase_number is 2."""
        phases = self._get_research_phases()
        phase_num, phase_name, deliverable, prompt, max_turns = phases[0]
        assert phase_num == 2, (
            f"PHASE_MAP['research'][0] phase_num is {phase_num!r}, expected 2.\n"
            "Research scope invokes Phase 2 (Research) — the /phase-2 skill."
        )

    def test_research_phase_name_is_research(self):
        """A-04: The research phase name is "Research"."""
        phases = self._get_research_phases()
        phase_num, phase_name, deliverable, prompt, max_turns = phases[0]
        assert phase_name == "Research", (
            f"PHASE_MAP['research'][0] phase_name is {phase_name!r}, expected 'Research'."
        )

    def test_research_phase_prompt_starts_with_phase_2(self):
        """A-05: The prompt template starts with "/phase-2 " — a valid slash-command."""
        phases = self._get_research_phases()
        phase_num, phase_name, deliverable, prompt, max_turns = phases[0]
        assert prompt.startswith("/phase-2 "), (
            f"PHASE_MAP['research'][0] prompt does not start with '/phase-2 ': {prompt[:80]!r}.\n"
            "Per STORY-516 compliance test: every phase prompt must be a slash-command.\n"
            "Fix: prompt must begin with '/phase-2 story_id={story_id} ...'"
        )

    def test_research_phase_prompt_passes_story_context(self):
        """A-06: The prompt template includes story_id=, repo=, and story_folder=."""
        phases = self._get_research_phases()
        phase_num, phase_name, deliverable, prompt, max_turns = phases[0]
        for required in ("story_id=", "repo=", "story_folder="):
            assert required in prompt, (
                f"PHASE_MAP['research'][0] prompt missing {required!r}: {prompt!r}.\n"
                "All phase prompts must pass story_id, repo, and story_folder "
                "so the skill can locate deliverables and write to the right folder."
            )

    def test_research_max_turns_is_concrete_int(self):
        """A-07: max_turns is a concrete integer >= 1 (seed specifies 50)."""
        phases = self._get_research_phases()
        phase_num, phase_name, deliverable, prompt, max_turns = phases[0]
        assert isinstance(max_turns, int) and max_turns >= 1, (
            f"PHASE_MAP['research'][0] max_turns is {max_turns!r} (type {type(max_turns).__name__}).\n"
            "max_turns must be a concrete integer >= 1. "
            "Seed specifies 50, matching the other research-style phases."
        )


# ---------------------------------------------------------------------------
# Group B — run_sdlc_phases behavior for scope="research"
# ---------------------------------------------------------------------------


class TestRunSdlcPhasesResearch:
    """B-01 through B-02: run_sdlc_phases with scope="research" behaves correctly.

    RED state:
      B-01 — FAILS: PHASE_MAP.get("research", PHASE_MAP["small"]) returns
              PHASES_SMALL (3 phases) → log says "scope=research, 3 phases"
      B-02 — FAILS: Phase 1 (Seed) is attempted instead of jumping to Phase 2
    """

    def _run_phases(self, tmp_path, scope: str, *, capture_phase_calls: list):
        """Helper: run run_sdlc_phases with all external calls mocked.

        Phases called are appended to capture_phase_calls as (phase_num, phase_name).
        """
        mod = _get_phase_runner()

        # Create minimal workdir
        story_dir = tmp_path / "features" / "story-539-research"
        story_dir.mkdir(parents=True)
        (tmp_path / "CLAUDE.md").write_text("# Test\n")
        (tmp_path / ".git").mkdir()

        def _fake_run_phase_sdk(**kwargs):
            phase_num = kwargs.get("phase_num", -1)
            phase_name = kwargs.get("phase_name", "")
            capture_phase_calls.append((phase_num, phase_name))
            # Write deliverable so the runner marks the phase done
            deliverable_map = {
                1: "seed.md",
                2: "research.md",
                4: "analysis.md",
                6: "feature-spec.md",
                7: "test-design.md",
            }
            if phase_num in deliverable_map:
                out_file = story_dir / deliverable_map[phase_num]
                out_file.write_text(f"# Phase {phase_num} deliverable\n")
            return (0, "")

        with (
            patch.object(mod, "_run_phase_sdk", side_effect=_fake_run_phase_sdk),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-539",
                repo="tech-dev-agents",
                scope=scope,
                prompt="Research question: how does the Loki quota aggregator work?",
                workdir=str(tmp_path),
                env=os.environ.copy(),
            )

    def test_run_sdlc_phases_research_logs_correct_phase_count(self, tmp_path, capsys):
        """B-01: run_sdlc_phases with scope=research logs 'scope=research, 1 phase'."""
        called: list = []
        self._run_phases(tmp_path, "research", capture_phase_calls=called)

        captured = capsys.readouterr()
        assert "scope=research, 1 phase" in captured.out, (
            f"Expected '[DISPATCH] Starting SDLC phases for STORY-539 (scope=research, 1 phase)' "
            f"in stdout, but got:\n{captured.out[:500]}\n\n"
            "Currently FAILS: PHASE_MAP.get('research', PHASE_MAP['small']) returns PHASES_SMALL "
            "(3 phases) → log says 'scope=research, 3 phases'.\n"
            "Phase 8 must add PHASES_RESEARCH with 1 tuple to PHASE_MAP."
        )

    def test_research_scope_never_runs_phase_1(self, tmp_path):
        """B-02: scope=research starts at Phase 2 — Phase 1 (Seed) is never invoked."""
        called: list = []
        self._run_phases(tmp_path, "research", capture_phase_calls=called)

        phase_nums_called = [p[0] for p in called]

        assert 1 not in phase_nums_called, (
            f"Phase 1 (Seed) was called for scope=research — phases called: {phase_nums_called}.\n"
            "For research scope, Phase 1 must NEVER run: the research question IS the prompt, "
            "not a deliverable that needs Phase 1 to produce.\n"
            "Currently FAILS: PHASE_MAP.get('research', PHASE_MAP['small']) → PHASES_SMALL "
            "→ starts with Phase 1."
        )
        assert 2 in phase_nums_called, (
            f"Phase 2 (Research) was not called for scope=research — phases called: {phase_nums_called}.\n"
            "Phase 8 must add PHASES_RESEARCH = [(2, 'Research', ...)] to PHASE_MAP."
        )
        assert phase_nums_called == [2], (
            f"Expected only Phase 2 for scope=research, got: {phase_nums_called}.\n"
            "Research scope runs exactly one phase: Phase 2 (Research)."
        )


# ---------------------------------------------------------------------------
# Group C — Regression: existing scopes unchanged
# ---------------------------------------------------------------------------


class TestRegressionExistingScopes:
    """C-01 through C-03: Adding 'research' must not break existing scopes."""

    def test_small_scope_unchanged(self):
        """C-01: PHASES_SMALL still has exactly 3 phases: 1, 7, 8."""
        phase_map = _get_phase_map()
        phases_small = phase_map["small"]
        phase_nums = [p[0] for p in phases_small]
        assert phase_nums == [1, 7, 8], (
            f"PHASES_SMALL changed — expected [1, 7, 8], got {phase_nums}.\n"
            "Phase 8 must NOT modify PHASES_SMALL when adding research support."
        )

    def test_medium_scope_unchanged(self):
        """C-02: PHASES_MEDIUM still has exactly 5 phases: 1, 4, 6, 7, 8."""
        phase_map = _get_phase_map()
        phases_medium = phase_map["medium"]
        phase_nums = [p[0] for p in phases_medium]
        assert phase_nums == [1, 4, 6, 7, 8], (
            f"PHASES_MEDIUM changed — expected [1, 4, 6, 7, 8], got {phase_nums}."
        )

    def test_phase_map_keys_include_research(self):
        """C-03: After Phase 8, PHASE_MAP must have all four scopes."""
        phase_map = _get_phase_map()
        assert "research" in phase_map, (
            'PHASE_MAP missing "research" key.\n'
            "Phase 8 must add:\n"
            "  PHASES_RESEARCH = [(2, 'Research', 'research.md', '/phase-2 ...', 50)]\n"
            '  PHASE_MAP["research"] = PHASES_RESEARCH\n'
            "or equivalently:\n"
            '  PHASE_MAP = {"small": ..., "medium": ..., "large": ..., "research": PHASES_RESEARCH}'
        )
        assert set(phase_map.keys()) >= {"small", "medium", "large", "research"}, (
            f"PHASE_MAP is missing expected keys. Got: {set(phase_map.keys())}"
        )
