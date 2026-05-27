"""STORY-1003: canon-backport skill + retro dual-proposal + phase-9 3-question gate.

Phase 7 — RED state tests.

Tests verify:
- canon-backport/SKILL.md has valid frontmatter, when-to-run, steps, YAML format,
  and boundaries sections.
- retro/SKILL.md patch preserves original steps and adds Step 8a + gc-data-v2
  companion format section.
- phase-9/SKILL.md patch preserves the Workflow section and adds the 3-question
  gate before it.
- MANUAL-STEPS.md has numbered copy+submodule steps for Mark.

RED reasons:
- features/story-1003-canon-backport-retro/skills/canon-backport/SKILL.md does not exist
- features/story-1003-canon-backport-retro/skills/retro/SKILL.md does not exist
- features/story-1003-canon-backport-retro/skills/phase-9/SKILL.md does not exist
- features/story-1003-canon-backport-retro/MANUAL-STEPS.md does not exist

All tests pass after Phase 8 creates these files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "story-1003-canon-backport-retro"
SKILLS_OUT = FEATURE_DIR / "skills"

CANON_BACKPORT_SKILL = SKILLS_OUT / "canon-backport" / "SKILL.md"
RETRO_SKILL = SKILLS_OUT / "retro" / "SKILL.md"
PHASE9_SKILL = SKILLS_OUT / "phase-9" / "SKILL.md"
MANUAL_STEPS = FEATURE_DIR / "MANUAL-STEPS.md"


# ---------------------------------------------------------------------------
# Group A — canon-backport/SKILL.md (7 tests)
# ---------------------------------------------------------------------------


class TestCanonBackportSkill:
    """SC-1: canon-backport/SKILL.md is well-formed with all required sections."""

    def test_canon_backport_skill_exists(self):
        """A1: skills/canon-backport/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1003-canon-backport-retro/skills/
                     canon-backport/SKILL.md.
        """
        assert CANON_BACKPORT_SKILL.exists(), (
            f"canon-backport/SKILL.md not found at {CANON_BACKPORT_SKILL}. "
            "Phase 8 must create this file."
        )

    def test_canon_backport_has_valid_frontmatter(self):
        """A2: Frontmatter must contain name: canon-backport and description:.

        RED: File does not exist (skipped).
        GREEN after: Frontmatter in SKILL.md has both fields.
        """
        if not CANON_BACKPORT_SKILL.exists():
            pytest.skip("canon-backport/SKILL.md not yet created")
        content = CANON_BACKPORT_SKILL.read_text()
        assert "name: canon-backport" in content, (
            "SKILL.md frontmatter must contain 'name: canon-backport'"
        )
        assert "description:" in content, (
            "SKILL.md frontmatter must contain 'description:' field"
        )

    def test_canon_backport_has_triggers(self):
        """A3: Frontmatter must contain a triggers: list.

        RED: File does not exist (skipped).
        GREEN after: Frontmatter in SKILL.md includes triggers: list.
        """
        if not CANON_BACKPORT_SKILL.exists():
            pytest.skip("canon-backport/SKILL.md not yet created")
        content = CANON_BACKPORT_SKILL.read_text()
        assert "triggers:" in content, (
            "SKILL.md frontmatter must contain 'triggers:' list so Claude can auto-match "
            "user intents to this skill"
        )

    def test_canon_backport_has_when_to_run_section(self):
        """A4: Body must contain a ## When to run section.

        RED: File does not exist (skipped).
        GREEN after: SKILL.md contains '## When to run' heading.
        """
        if not CANON_BACKPORT_SKILL.exists():
            pytest.skip("canon-backport/SKILL.md not yet created")
        content = CANON_BACKPORT_SKILL.read_text()
        assert re.search(r"##\s+When to run", content, re.IGNORECASE), (
            "SKILL.md must contain '## When to run' section explaining the "
            "complete-story integration point"
        )

    def test_canon_backport_has_steps_section_with_gc_data_v2(self):
        """A5: ## Steps section must reference gc-data-v2/platform.

        RED: File does not exist (skipped).
        GREEN after: SKILL.md contains '## Steps' heading and 'gc-data-v2' reference.
        """
        if not CANON_BACKPORT_SKILL.exists():
            pytest.skip("canon-backport/SKILL.md not yet created")
        content = CANON_BACKPORT_SKILL.read_text()
        assert re.search(r"##\s+Steps", content), (
            "SKILL.md must contain '## Steps' section"
        )
        assert "gc-data-v2" in content, (
            "SKILL.md Steps section must reference 'gc-data-v2' (the target platform repo)"
        )

    def test_canon_backport_has_yaml_format_section(self):
        """A6: Body must contain a Canon Gap YAML format / Output Format section.

        RED: File does not exist (skipped).
        GREEN after: SKILL.md contains the YAML schema for canon-gap.yaml.
        """
        if not CANON_BACKPORT_SKILL.exists():
            pytest.skip("canon-backport/SKILL.md not yet created")
        content = CANON_BACKPORT_SKILL.read_text()
        # Accept either "Canon Gap YAML" or "Output Format" as the section title
        has_yaml_section = (
            re.search(r"##\s+Canon Gap YAML", content, re.IGNORECASE)
            or re.search(r"##\s+Output Format", content, re.IGNORECASE)
            or re.search(r"##\s+YAML Format", content, re.IGNORECASE)
        )
        assert has_yaml_section, (
            "SKILL.md must contain a YAML format section ('## Canon Gap YAML', "
            "'## Output Format', or '## YAML Format') describing the canon-gap.yaml schema"
        )
        assert "canon-gap.yaml" in content, (
            "SKILL.md must reference 'canon-gap.yaml' — the output file written to the story folder"
        )

    def test_canon_backport_has_boundaries_section(self):
        """A7: ## Boundaries section must state the skill only drafts, does not push.

        RED: File does not exist (skipped).
        GREEN after: SKILL.md contains '## Boundaries' with draft-only language.
        """
        if not CANON_BACKPORT_SKILL.exists():
            pytest.skip("canon-backport/SKILL.md not yet created")
        content = CANON_BACKPORT_SKILL.read_text()
        assert re.search(r"##\s+Boundaries", content, re.IGNORECASE), (
            "SKILL.md must contain '## Boundaries' section"
        )
        # Boundaries must say the skill does not push/open PRs automatically
        boundaries_present = (
            "does not push" in content.lower()
            or "draft" in content.lower()
            or "does not open" in content.lower()
            or "only drafts" in content.lower()
        )
        assert boundaries_present, (
            "SKILL.md Boundaries section must make clear the skill only drafts "
            "proposals and does not automatically push or open PRs"
        )


# ---------------------------------------------------------------------------
# Group B — retro/SKILL.md patch (6 tests)
# ---------------------------------------------------------------------------


class TestRetroSkillPatch:
    """SC-2: retro/SKILL.md patch preserves original content and adds gc-data-v2 companion."""

    def test_retro_patch_skill_exists(self):
        """B1: skills/retro/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1003-canon-backport-retro/skills/retro/SKILL.md.
        """
        assert RETRO_SKILL.exists(), (
            f"retro/SKILL.md not found at {RETRO_SKILL}. "
            "Phase 8 must create the patched retro skill file."
        )

    def test_retro_patch_preserves_original_steps(self):
        """B2: All original numbered steps (1-9) must still be present.

        RED: File does not exist (skipped).
        GREEN after: Patched SKILL.md retains all original step numbers.

        The original retro skill has steps 1-9. The patch must not drop any step.
        """
        if not RETRO_SKILL.exists():
            pytest.skip("retro/SKILL.md not yet created")
        content = RETRO_SKILL.read_text()
        # Original skill has at least 9 numbered steps (1. through 9.)
        # We check that steps 1 through 9 are all referenced
        for step_num in range(1, 10):
            step_pattern = rf"(?:^|\n){step_num}\.\s+\*\*"
            has_step = bool(re.search(step_pattern, content, re.MULTILINE))
            assert has_step, (
                f"retro/SKILL.md patch dropped original Step {step_num}. "
                "The patch must preserve all original numbered steps."
            )

    def test_retro_patch_has_step_8a_or_companion_step(self):
        """B3: A new step emitting the gc-data-v2 companion proposal must be present.

        RED: File does not exist (skipped).
        GREEN after: Patched SKILL.md contains '8a' or equivalent companion-emission step.
        """
        if not RETRO_SKILL.exists():
            pytest.skip("retro/SKILL.md not yet created")
        content = RETRO_SKILL.read_text()
        has_companion_step = (
            "8a" in content
            or "gc-data-v2 companion" in content.lower()
            or "companion proposal" in content.lower()
            or "retro-proposal-gc-data-v2" in content
        )
        assert has_companion_step, (
            "retro/SKILL.md patch must add Step 8a (or equivalent) that emits "
            "the gc-data-v2 companion proposal file"
        )

    def test_retro_patch_has_companion_format_section(self):
        """B4: A ## gc-data-v2 Companion Proposal section must be present.

        RED: File does not exist (skipped).
        GREEN after: Patched SKILL.md has the companion format documentation section.
        """
        if not RETRO_SKILL.exists():
            pytest.skip("retro/SKILL.md not yet created")
        content = RETRO_SKILL.read_text()
        has_section = (
            re.search(r"##\s+gc-data-v2 Companion", content, re.IGNORECASE)
            or re.search(r"##\s+Companion Proposal", content, re.IGNORECASE)
            or re.search(r"##\s+gc-data-v2 Proposal", content, re.IGNORECASE)
        )
        assert has_section, (
            "retro/SKILL.md patch must contain a '## gc-data-v2 Companion Proposal' "
            "section describing the companion proposal format"
        )

    def test_retro_patch_companion_yaml_schema(self):
        """B5: Patched SKILL.md must reference retro-proposal-gc-data-v2.yaml filename.

        RED: File does not exist (skipped).
        GREEN after: The companion format section names the output file.
        """
        if not RETRO_SKILL.exists():
            pytest.skip("retro/SKILL.md not yet created")
        content = RETRO_SKILL.read_text()
        assert "retro-proposal-gc-data-v2.yaml" in content, (
            "retro/SKILL.md patch must name the companion output file "
            "'retro-proposal-gc-data-v2.yaml' so agents know where to write it"
        )

    def test_retro_patch_references_canon_gap_yaml(self):
        """B6: Patched SKILL.md must reference canon-gap.yaml as the input source.

        RED: File does not exist (skipped).
        GREEN after: The companion step explains how to aggregate canon-gap.yaml files.
        """
        if not RETRO_SKILL.exists():
            pytest.skip("retro/SKILL.md not yet created")
        content = RETRO_SKILL.read_text()
        assert "canon-gap.yaml" in content, (
            "retro/SKILL.md patch must reference 'canon-gap.yaml' — the per-story "
            "canon gap file produced by /canon-backport that feeds the companion proposal"
        )


# ---------------------------------------------------------------------------
# Group C — phase-9/SKILL.md patch (4 tests)
# ---------------------------------------------------------------------------


class TestPhase9SkillPatch:
    """SC-3: phase-9/SKILL.md patch preserves original workflow + adds 3-question gate."""

    def test_phase9_patch_skill_exists(self):
        """C1: skills/phase-9/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1003-canon-backport-retro/skills/phase-9/SKILL.md.
        """
        assert PHASE9_SKILL.exists(), (
            f"phase-9/SKILL.md not found at {PHASE9_SKILL}. "
            "Phase 8 must create the patched phase-9 skill file."
        )

    def test_phase9_patch_preserves_workflow_section(self):
        """C2: The original ## Workflow section must still be present and intact.

        RED: File does not exist (skipped).
        GREEN after: Patched SKILL.md still has '## Workflow' with its original content.
        """
        if not PHASE9_SKILL.exists():
            pytest.skip("phase-9/SKILL.md not yet created")
        content = PHASE9_SKILL.read_text()
        assert re.search(r"##\s+Workflow", content), (
            "phase-9/SKILL.md patch must preserve the original '## Workflow' section"
        )
        # Workflow must still reference the 4 original workflow steps
        assert "Address Deferred Items" in content, (
            "phase-9/SKILL.md patch dropped 'Address Deferred Items' from the Workflow section"
        )
        assert "Polish Edge Cases" in content, (
            "phase-9/SKILL.md patch dropped 'Polish Edge Cases' from the Workflow section"
        )

    def test_phase9_patch_has_3_question_gate(self):
        """C3: A ## 3-Question Gate section must be present before ## Workflow.

        RED: File does not exist (skipped).
        GREEN after: Patched SKILL.md has the 3-question gate section before Workflow.
        """
        if not PHASE9_SKILL.exists():
            pytest.skip("phase-9/SKILL.md not yet created")
        content = PHASE9_SKILL.read_text()
        has_gate = (
            re.search(r"##\s+3-Question Gate", content, re.IGNORECASE)
            or re.search(r"##\s+Three.Question Gate", content, re.IGNORECASE)
            or re.search(r"##\s+Pre-Refinement Gate", content, re.IGNORECASE)
        )
        assert has_gate, (
            "phase-9/SKILL.md patch must contain a '## 3-Question Gate' section "
            "(or '## Pre-Refinement Gate') before the Workflow section"
        )
        # Verify gate comes before workflow
        gate_match = re.search(
            r"##\s+(?:3-Question Gate|Three.Question Gate|Pre-Refinement Gate)",
            content, re.IGNORECASE
        )
        workflow_match = re.search(r"##\s+Workflow", content)
        if gate_match and workflow_match:
            assert gate_match.start() < workflow_match.start(), (
                "The 3-Question Gate section must appear BEFORE the ## Workflow section"
            )

    def test_phase9_patch_gate_covers_all_3_questions(self):
        """C4: The gate must reference all 3 required questions.

        RED: File does not exist (skipped).
        GREEN after: Gate section covers (1) code review findings, (2) test coverage,
                     (3) canon gap / canon-backport.
        """
        if not PHASE9_SKILL.exists():
            pytest.skip("phase-9/SKILL.md not yet created")
        content = PHASE9_SKILL.read_text()

        # Q1: code review findings (from Phase 8b)
        has_q1 = (
            "code review" in content.lower()
            or "8b" in content
            or "review finding" in content.lower()
        )
        assert has_q1, (
            "phase-9/SKILL.md gate must include Q1 about Phase 8b code review findings"
        )

        # Q2: test coverage
        has_q2 = (
            "coverage" in content.lower()
            or "test coverage" in content.lower()
        )
        assert has_q2, (
            "phase-9/SKILL.md gate must include Q2 about current test coverage percentage"
        )

        # Q3: canon gap / canon-backport
        has_q3 = (
            "canon" in content.lower()
            or "canon-backport" in content.lower()
            or "canon gap" in content.lower()
        )
        assert has_q3, (
            "phase-9/SKILL.md gate must include Q3 about canon gaps and /canon-backport"
        )


# ---------------------------------------------------------------------------
# Group D — MANUAL-STEPS.md (3 tests)
# ---------------------------------------------------------------------------


class TestManualSteps:
    """SC-4: MANUAL-STEPS.md has numbered steps for Mark to deploy all three skills."""

    def test_manual_steps_exists(self):
        """D1: MANUAL-STEPS.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1003-canon-backport-retro/MANUAL-STEPS.md.
        """
        assert MANUAL_STEPS.exists(), (
            f"MANUAL-STEPS.md not found at {MANUAL_STEPS}. "
            "Phase 8 must create this file with deploy instructions for Mark."
        )

    def test_manual_steps_has_copy_instructions(self):
        """D2: MANUAL-STEPS.md must contain cp commands targeting .sdlc/skills.

        RED: File does not exist (skipped).
        GREEN after: MANUAL-STEPS.md contains copy commands with .sdlc/skills target.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        has_copy = ("cp " in content or "copy" in content.lower()) and ".sdlc/skills" in content
        assert has_copy, (
            "MANUAL-STEPS.md must contain cp commands that copy skill files into .sdlc/skills/"
        )

    def test_manual_steps_has_numbered_steps_and_git_submodule(self):
        """D3: MANUAL-STEPS.md must have ≥4 numbered steps and git -C .sdlc reference.

        RED: File does not exist (skipped).
        GREEN after: File has numbered steps list and submodule bump command.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        # Count numbered step markers (e.g., "1.", "2.", ...)
        numbered_steps = re.findall(r"^\d+\.", content, re.MULTILINE)
        assert len(numbered_steps) >= 4, (
            f"MANUAL-STEPS.md must have at least 4 numbered steps, found {len(numbered_steps)}. "
            "Steps should cover: copy canon-backport, copy retro, copy phase-9, git submodule bump."
        )
        assert "git -C .sdlc" in content, (
            "MANUAL-STEPS.md must contain 'git -C .sdlc' command for committing inside the "
            "submodule without a cd compound command"
        )
