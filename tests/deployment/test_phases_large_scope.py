"""STORY-505/528 root-cause fix: PHASES_LARGE must include all 10 phases.

Before 2026-04-23, PHASES_LARGE = PHASES_MEDIUM, skipping Research (2),
Approaches (3), Selection (5), Refinement (9), Operations (10). Every
Large-scope story jumped straight to Phase 4 with no priors — causing
silent rc=0 exits and ghost completions.

These tests lock the Large-scope contract to CLAUDE.md so any future
refactor can't regress it invisibly.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


def _phase_numbers(phase_list):
    return [p[0] for p in phase_list]


def _phase_deliverables(phase_list):
    return {p[0]: p[2] for p in phase_list}


class TestPhasesLargeContract:
    """Per CLAUDE.md, Large scope runs 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10."""

    def test_phases_large_contains_all_ten_phases_in_order(self):
        from sdlc_phase_runner import PHASES_LARGE
        assert _phase_numbers(PHASES_LARGE) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    def test_phases_large_is_not_aliased_to_phases_medium(self):
        """Prevent regression to the pre-2026-04-23 state where
        `PHASES_LARGE = PHASES_MEDIUM` aliased the two lists."""
        from sdlc_phase_runner import PHASES_LARGE, PHASES_MEDIUM
        assert PHASES_LARGE is not PHASES_MEDIUM, (
            "PHASES_LARGE must be its own list; aliasing to PHASES_MEDIUM "
            "causes Large stories to skip Phases 2,3,5,9,10 (the STORY-505 bug)"
        )
        assert len(PHASES_LARGE) > len(PHASES_MEDIUM), (
            "PHASES_LARGE must have strictly more phases than PHASES_MEDIUM"
        )

    def test_phases_large_deliverables_match_claude_md_spec(self):
        """CLAUDE.md spec:
          1 → seed.md        2 → research.md      3 → expansion.md
          4 → analysis.md    5 → selection.md     6 → specification.md (Large)
          7 → test-design.md 8 → (code, no file)  9 → refinement-report.md
          10 → site-reliability.md
        """
        from sdlc_phase_runner import PHASES_LARGE
        d = _phase_deliverables(PHASES_LARGE)
        assert d[1]  == "seed.md"
        assert d[2]  == "research.md"
        assert d[3]  == "expansion.md"
        assert d[4]  == "analysis.md"
        assert d[5]  == "selection.md"
        assert d[6]  == "specification.md", (
            "Large uses specification.md (not feature-spec.md — that's Medium)"
        )
        assert d[7]  == "test-design.md"
        assert d[8]  is None
        assert d[9]  == "refinement-report.md"
        assert d[10] == "site-reliability.md"

    def test_phases_medium_uses_feature_spec_not_specification(self):
        """Medium and Large diverge on Phase 6: Medium → feature-spec.md,
        Large → specification.md (plus sibling files, but feature-spec.md
        stays the single deliverable key for Medium)."""
        from sdlc_phase_runner import PHASES_MEDIUM
        d = _phase_deliverables(PHASES_MEDIUM)
        assert d[6] == "feature-spec.md"

    def test_phases_small_skips_middle_phases(self):
        """CLAUDE.md: Small = 1 → 7 → 8 → Done"""
        from sdlc_phase_runner import PHASES_SMALL
        assert _phase_numbers(PHASES_SMALL) == [1, 7, 8]

    def test_phase_map_has_scope_entries_for_all_four_scopes(self):
        from sdlc_phase_runner import PHASE_MAP
        for scope in ("small", "medium", "large", "research"):
            assert scope in PHASE_MAP, f"PHASE_MAP missing entry for scope={scope}"

    def test_large_phase_prompts_all_use_slash_command_format(self):
        """Per sdlc_phase_runner comment: prompts MUST be slash-commands so
        claude_sdk_tool.py can rewrite them into skill-reading SDK calls."""
        from sdlc_phase_runner import PHASES_LARGE
        for phase_num, phase_name, deliverable, prompt_template, max_turns in PHASES_LARGE:
            assert prompt_template.startswith("/phase-"), (
                f"Phase {phase_num} ({phase_name}) prompt must start with "
                f"/phase-N slash-command format; got: {prompt_template[:30]!r}"
            )
            assert "{story_id}" in prompt_template
            assert "{repo}" in prompt_template
            assert "{story_folder}" in prompt_template

    def test_max_turns_is_positive_for_every_phase(self):
        """Phases with max_turns <= 0 would never actually run. Catch
        typos/regressions that would silently kill a scope."""
        from sdlc_phase_runner import PHASES_SMALL, PHASES_MEDIUM, PHASES_LARGE, PHASES_RESEARCH
        for name, phase_list in [
            ("small", PHASES_SMALL), ("medium", PHASES_MEDIUM),
            ("large", PHASES_LARGE), ("research", PHASES_RESEARCH),
        ]:
            for phase_num, phase_name, _, _, max_turns in phase_list:
                assert max_turns > 0, (
                    f"Scope '{name}' phase {phase_num} ({phase_name}) has "
                    f"max_turns={max_turns} — must be positive"
                )

    def test_implementation_phase_8_has_no_deliverable_file(self):
        """Phase 8 is the only phase without a deliverable file (code is the
        deliverable; the Phase 8 ghost-commit guard catches zero-commit
        ghost completions). If someone adds a file here it would change
        the semantics of that guard."""
        from sdlc_phase_runner import PHASES_LARGE, PHASES_MEDIUM, PHASES_SMALL
        for phase_list in (PHASES_LARGE, PHASES_MEDIUM, PHASES_SMALL):
            d = _phase_deliverables(phase_list)
            assert d.get(8) is None, (
                "Phase 8 must have deliverable=None so the ghost-commit "
                "guard (git rev-list count main..HEAD) is the sole gate"
            )
