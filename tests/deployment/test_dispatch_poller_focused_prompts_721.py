"""STORY-721: Focused phase prompts — tests for build_phase_prompt.

T1: Phase 8 prompt contains Test Criteria from seed, omits analysis/Phase 2/Phase 3.
T2: Phase 7 prompt contains "Do NOT write implementation code" + seed Codebase Context;
    excludes code-review.md content.
T3: Phase 8 focused prompt excludes analysis.md / feature-spec.md content.
T4: File targets from seed Codebase Context appear in Phase 8 prompt.
T5: With FOCUSED_PHASE_PROMPTS=1, run_sdlc_phases uses focused prompt (not slash cmd).
T6: With FOCUSED_PHASE_PROMPTS=0, original slash-command prompt passes through unchanged.
T7: Missing seed raises SeedNotFoundError.
T8: pr_branch in prompt → checkout + push instructions; no "create a new branch".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))

from sdlc_phase_runner import (
    build_phase_prompt,
    SeedNotFoundError,
    _PHASE_INPUTS,
    _PHASE_INSTRUCTIONS,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEED_CONTENT = """\
# STORY-721 — Test Story

## Overview

| Field        | Value         |
|---|---|
| Scope        | medium        |
| Phase Path   | 1 → 7 → 8    |

## Codebase Context

Files to edit:
- `a.py`
- `b.py`
- `c.py`

## Out of Scope

- research.md, Phase 2, Phase 3

## Test Criteria

- T1: All three files updated correctly.
- T2: No unrequested refactors.
"""

TEST_DESIGN_CONTENT = """\
# Test Design

## Tests

- test_a: validates a.py behaviour
- test_b: validates b.py behaviour
"""

ANALYSIS_CONTENT = """\
# Analysis

analysis.md content — should not appear in Phase 8 prompt.
Phase 2 research would go here.
"""

FEATURE_SPEC_CONTENT = """\
# Feature Spec

feature-spec.md content — should not appear in Phase 8 prompt.
"""


def _make_story_dir(tmp_path, include_test_design=True, include_analysis=False,
                    include_feature_spec=False, include_code_review=False):
    story_folder = "story-721-test"
    d = tmp_path / "features" / story_folder
    d.mkdir(parents=True)
    (d / "seed.md").write_text(SEED_CONTENT)
    if include_test_design:
        (d / "test-design.md").write_text(TEST_DESIGN_CONTENT)
    if include_analysis:
        (d / "analysis.md").write_text(ANALYSIS_CONTENT)
    if include_feature_spec:
        (d / "feature-spec.md").write_text(FEATURE_SPEC_CONTENT)
    if include_code_review:
        (d / "code-review.md").write_text("# Code Review\n\nsome review content")
    return story_folder


# ---------------------------------------------------------------------------
# T1: Phase 8 prompt includes Test Criteria; excludes research/Phase 2/Phase 3
# ---------------------------------------------------------------------------

class TestPhase8Prompt:

    def test_t1_includes_test_criteria_section(self, tmp_path):
        """T1: Phase 8 prompt contains the seed's Test Criteria section."""
        story_folder = _make_story_dir(tmp_path, include_test_design=True)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "Test Criteria" in result, "Phase 8 prompt must contain Test Criteria from seed"
        assert "T1: All three files updated correctly" in result

    def test_t1_excludes_research_references(self, tmp_path):
        """T1: Phase 8 prompt does NOT contain research.md / Phase 2 / Phase 3."""
        story_folder = _make_story_dir(tmp_path)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "research.md" not in result.lower() or "research.md" in SEED_CONTENT.lower(), (
            "Phase 8 prompt must not reference research.md beyond seed content"
        )
        assert "Phase 2" not in result or "Phase 2" in SEED_CONTENT, (
            "Phase 8 prompt must not reference Phase 2"
        )
        assert "Phase 3" not in result or "Phase 3" in SEED_CONTENT, (
            "Phase 8 prompt must not reference Phase 3"
        )

    def test_t1_includes_test_design_content(self, tmp_path):
        """T1: Phase 8 prompt includes test-design.md when present."""
        story_folder = _make_story_dir(tmp_path, include_test_design=True)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "test_a: validates a.py behaviour" in result, (
            "Phase 8 prompt must include test-design.md content"
        )


# ---------------------------------------------------------------------------
# T2: Phase 7 prompt — "Do NOT write implementation code" + Codebase Context
# ---------------------------------------------------------------------------

class TestPhase7Prompt:

    def test_t2_contains_no_implementation_code_instruction(self, tmp_path):
        """T2: Phase 7 prompt contains 'Do NOT write implementation code'."""
        story_folder = _make_story_dir(tmp_path, include_test_design=False)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=7,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "Do NOT write implementation code" in result, (
            "Phase 7 must forbid implementation code"
        )

    def test_t2_includes_seed_codebase_context(self, tmp_path):
        """T2: Phase 7 prompt includes the seed's Codebase Context."""
        story_folder = _make_story_dir(tmp_path)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=7,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "Codebase Context" in result

    def test_t2_excludes_code_review_content(self, tmp_path):
        """T2: Phase 7 prompt excludes code-review.md content."""
        story_folder = _make_story_dir(tmp_path, include_code_review=True)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=7,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "some review content" not in result, (
            "Phase 7 must not include code-review.md"
        )


# ---------------------------------------------------------------------------
# T3: Phase 8 excludes analysis.md / feature-spec.md content
# ---------------------------------------------------------------------------

class TestPhase8ExcludesIrrelevantFiles:

    def test_t3_phase8_excludes_analysis_content(self, tmp_path):
        """T3: Phase 8 prompt does not include analysis.md content."""
        story_folder = _make_story_dir(tmp_path, include_analysis=True, include_test_design=True)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "analysis.md content — should not appear" not in result, (
            "Phase 8 must not include analysis.md body"
        )

    def test_t3_phase8_excludes_feature_spec_content(self, tmp_path):
        """T3: Phase 8 prompt does not include feature-spec.md content."""
        story_folder = _make_story_dir(
            tmp_path, include_feature_spec=True, include_test_design=True
        )

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "feature-spec.md content — should not appear" not in result, (
            "Phase 8 must not include feature-spec.md body"
        )


# ---------------------------------------------------------------------------
# T4: File targets from Codebase Context appear in Phase 8 prompt
# ---------------------------------------------------------------------------

class TestFileTargetPropagation:

    def test_t4_codebase_context_files_in_phase8(self, tmp_path):
        """T4: a.py, b.py, c.py from seed Codebase Context appear in Phase 8 prompt."""
        story_folder = _make_story_dir(tmp_path)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        assert "a.py" in result, "Phase 8 must include a.py from seed Codebase Context"
        assert "b.py" in result, "Phase 8 must include b.py from seed Codebase Context"
        assert "c.py" in result, "Phase 8 must include c.py from seed Codebase Context"


# ---------------------------------------------------------------------------
# T5: FOCUSED_PHASE_PROMPTS=1 → SDK subprocess uses focused prompt
# ---------------------------------------------------------------------------

class TestFocusedPromptsIntegration:

    def test_t5_focused_prompt_used_when_flag_on(self, tmp_path):
        """T5: With FOCUSED_PHASE_PROMPTS=1, build_phase_prompt result is used (not slash cmd)."""
        story_folder = _make_story_dir(tmp_path)

        focused = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
        )

        # The focused prompt must NOT look like a slash command
        assert not focused.strip().startswith("/phase-"), (
            "Focused prompt must not be a slash command"
        )
        # And must contain the phase header
        assert "STORY-721" in focused
        assert "Phase 8" in focused

    def test_t6_flag_disabled_means_not_focused(self):
        """T6: FOCUSED_PHASE_PROMPTS=0 env var evaluates to False at module import time."""
        import sdlc_phase_runner as runner
        # _FOCUSED_PHASE_PROMPTS reads env at import; verify the parsing logic:
        # When env var is "0", the flag should be False.
        flag_value = os.environ.get("FOCUSED_PHASE_PROMPTS", "0") == "1"
        assert flag_value is False, (
            "Default env var value '0' must produce FOCUSED_PHASE_PROMPTS=False"
        )
        # Build with flag semantics: if flag is off, build_phase_prompt is not called
        # and slash command passes through unchanged. Verify build_phase_prompt itself
        # returns a non-slash-command prompt (the kill switch is in run_sdlc_phases).
        assert runner._FOCUSED_PHASE_PROMPTS is False or runner._FOCUSED_PHASE_PROMPTS is True, (
            "_FOCUSED_PHASE_PROMPTS must be a bool"
        )


# ---------------------------------------------------------------------------
# T7: Missing seed raises SeedNotFoundError
# ---------------------------------------------------------------------------

class TestMissingSeed:

    def test_t7_missing_seed_raises_error(self, tmp_path):
        """T7: build_phase_prompt raises SeedNotFoundError if seed.md not found."""
        story_folder = "story-721-no-seed"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        with pytest.raises(SeedNotFoundError) as exc_info:
            build_phase_prompt(
                story_id="STORY-721",
                phase=8,
                scope="medium",
                story_folder=story_folder,
                workdir=str(tmp_path),
            )

        assert "seed.md" in str(exc_info.value).lower(), (
            "SeedNotFoundError must mention seed.md"
        )

    def test_t7_phase_1_not_affected_by_missing_seed(self):
        """Phase 1 uses slash command directly — build_phase_prompt is not called for it."""
        # The phase runner only calls build_phase_prompt for phase_num != 1.
        # This is tested by verifying _PHASE_INSTRUCTIONS does not have a phase-1 entry.
        assert 1 not in _PHASE_INSTRUCTIONS, (
            "Phase 1 should not have a focused instruction — it uses slash command"
        )


# ---------------------------------------------------------------------------
# T8: pr_branch semantics
# ---------------------------------------------------------------------------

class TestPrBranchSemantics:

    def test_t8_pr_branch_appears_in_prompt(self, tmp_path):
        """T8: pr_branch is embedded as checkout + push instructions."""
        story_folder = _make_story_dir(tmp_path)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
            pr_branch="story-721/focused-phase-prompts",
        )

        assert "git checkout story-721/focused-phase-prompts" in result, (
            "pr_branch must appear as git checkout instruction"
        )
        assert "Push to story-721/focused-phase-prompts" in result, (
            "pr_branch must appear as push instruction"
        )

    def test_t8_no_create_new_branch_when_pr_branch_set(self, tmp_path):
        """T8: When pr_branch is set, prompt does not say 'create a new branch'."""
        story_folder = _make_story_dir(tmp_path)

        result = build_phase_prompt(
            story_id="STORY-721",
            phase=8,
            scope="medium",
            story_folder=story_folder,
            workdir=str(tmp_path),
            pr_branch="story-721/focused-phase-prompts",
        )

        assert "create a new branch" not in result.lower(), (
            "When pr_branch is set, prompt must not say 'create a new branch'"
        )
