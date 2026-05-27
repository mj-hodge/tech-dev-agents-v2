"""STORY-1004: scaffold-drift-check + pipeline-kickoff skills.

Phase 7 — RED state tests.

Tests verify:
- scaffold-drift-check SKILL.md has valid frontmatter, steps section, canonical-file
  references, and drift-reporting language.
- pipeline-kickoff SKILL.md has valid frontmatter, steps section, sources.yaml
  validation, and references the prompt template.
- pipeline-kickoff-prompt.md exists and contains required planning sections.
- MANUAL-STEPS.md has numbered steps for Mark covering copy + submodule bump.

RED reasons:
- features/story-1004-scaffold-drift-kickoff/skills/scaffold-drift-check/SKILL.md
  does not yet exist
- features/story-1004-scaffold-drift-kickoff/skills/pipeline-kickoff/SKILL.md
  does not yet exist
- features/story-1004-scaffold-drift-kickoff/pipeline-kickoff-prompt.md
  does not yet exist
- features/story-1004-scaffold-drift-kickoff/MANUAL-STEPS.md does not yet exist

All tests pass after Phase 8 creates these files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "story-1004-scaffold-drift-kickoff"
SKILLS_OUT = FEATURE_DIR / "skills"

SCAFFOLD_DRIFT_SKILL = SKILLS_OUT / "scaffold-drift-check" / "SKILL.md"
PIPELINE_KICKOFF_SKILL = SKILLS_OUT / "pipeline-kickoff" / "SKILL.md"
PIPELINE_KICKOFF_PROMPT = FEATURE_DIR / "pipeline-kickoff-prompt.md"
MANUAL_STEPS = FEATURE_DIR / "MANUAL-STEPS.md"


# ---------------------------------------------------------------------------
# Group A — scaffold-drift-check SKILL.md (5 tests)
# ---------------------------------------------------------------------------


class TestScaffoldDriftCheckSkill:
    """AC-1: scaffold-drift-check SKILL.md is well-formed and checks canonical files."""

    def test_scaffold_drift_check_skill_exists(self):
        """A1: skills/scaffold-drift-check/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1004-scaffold-drift-kickoff/skills/
                     scaffold-drift-check/SKILL.md.
        """
        assert SCAFFOLD_DRIFT_SKILL.exists(), (
            f"scaffold-drift-check/SKILL.md not found at {SCAFFOLD_DRIFT_SKILL}. "
            "Phase 8 must create this file with YAML frontmatter and drift-check steps."
        )

    def test_scaffold_drift_check_has_valid_frontmatter(self):
        """A2: SKILL.md must start with YAML frontmatter containing required fields.

        RED: File does not yet exist.
        GREEN after: File starts with '---', contains 'name: scaffold-drift-check'
                     and a 'description:' field.
        """
        if not SCAFFOLD_DRIFT_SKILL.exists():
            pytest.skip("scaffold-drift-check/SKILL.md not yet created")
        content = SCAFFOLD_DRIFT_SKILL.read_text()
        assert content.startswith("---"), (
            "scaffold-drift-check/SKILL.md must start with YAML frontmatter (---)."
        )
        assert "name: scaffold-drift-check" in content, (
            "YAML frontmatter must include 'name: scaffold-drift-check'. "
            "This is the identifier the Claude Code skill system uses to match the command."
        )
        assert "description:" in content, (
            "YAML frontmatter must include a 'description:' field."
        )

    def test_scaffold_drift_check_has_steps_section(self):
        """A3: SKILL.md body must include a ## Steps section.

        RED: File does not yet exist.
        GREEN after: Body contains a '## Steps' heading listing the drift checks.
        """
        if not SCAFFOLD_DRIFT_SKILL.exists():
            pytest.skip("scaffold-drift-check/SKILL.md not yet created")
        content = SCAFFOLD_DRIFT_SKILL.read_text()
        assert "## Steps" in content or "## steps" in content.lower(), (
            "scaffold-drift-check/SKILL.md must have a '## Steps' section listing "
            "the individual drift checks to run."
        )

    def test_scaffold_drift_check_references_canonical_files(self):
        """A4: SKILL.md must reference the canonical scaffold files it checks.

        Required references: CLAUDE.md, AGENTS.md, config.yaml

        RED: File does not yet exist.
        GREEN after: Body mentions each of the three canonical files.
        """
        if not SCAFFOLD_DRIFT_SKILL.exists():
            pytest.skip("scaffold-drift-check/SKILL.md not yet created")
        content = SCAFFOLD_DRIFT_SKILL.read_text()
        for required_ref in ("CLAUDE.md", "AGENTS.md", "config.yaml"):
            assert required_ref in content, (
                f"scaffold-drift-check/SKILL.md must reference '{required_ref}' — "
                f"the skill checks this canonical scaffold file."
            )

    def test_scaffold_drift_check_has_drift_reporting(self):
        """A5: SKILL.md must contain drift detection / PASS/FAIL/FIX reporting language.

        RED: File does not yet exist.
        GREEN after: Body contains at least two of: PASS, FAIL, FIX, drift.
        """
        if not SCAFFOLD_DRIFT_SKILL.exists():
            pytest.skip("scaffold-drift-check/SKILL.md not yet created")
        content = SCAFFOLD_DRIFT_SKILL.read_text()
        reporting_terms = ["PASS", "FAIL", "FIX", "drift"]
        found = [term for term in reporting_terms if term in content]
        assert len(found) >= 2, (
            "scaffold-drift-check/SKILL.md must contain drift-reporting language. "
            f"Expected at least 2 of {reporting_terms}, found: {found}. "
            "The skill must report each check result as PASS/FAIL/FIX."
        )


# ---------------------------------------------------------------------------
# Group B — pipeline-kickoff SKILL.md (5 tests)
# ---------------------------------------------------------------------------


class TestPipelineKickoffSkill:
    """AC-2: pipeline-kickoff SKILL.md is well-formed, validates source catalog,
    and references the prompt template.
    """

    def test_pipeline_kickoff_skill_exists(self):
        """B1: skills/pipeline-kickoff/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1004-scaffold-drift-kickoff/skills/
                     pipeline-kickoff/SKILL.md.
        """
        assert PIPELINE_KICKOFF_SKILL.exists(), (
            f"pipeline-kickoff/SKILL.md not found at {PIPELINE_KICKOFF_SKILL}. "
            "Phase 8 must create this file with YAML frontmatter and kickoff steps."
        )

    def test_pipeline_kickoff_has_valid_frontmatter(self):
        """B2: SKILL.md must start with YAML frontmatter containing required fields.

        RED: File does not yet exist.
        GREEN after: File starts with '---', contains 'name: pipeline-kickoff'
                     and a 'description:' field.
        """
        if not PIPELINE_KICKOFF_SKILL.exists():
            pytest.skip("pipeline-kickoff/SKILL.md not yet created")
        content = PIPELINE_KICKOFF_SKILL.read_text()
        assert content.startswith("---"), (
            "pipeline-kickoff/SKILL.md must start with YAML frontmatter (---)."
        )
        assert "name: pipeline-kickoff" in content, (
            "YAML frontmatter must include 'name: pipeline-kickoff'."
        )
        assert "description:" in content, (
            "YAML frontmatter must include a 'description:' field."
        )

    def test_pipeline_kickoff_has_steps_section(self):
        """B3: SKILL.md body must include a ## Steps section.

        RED: File does not yet exist.
        GREEN after: Body contains a '## Steps' heading.
        """
        if not PIPELINE_KICKOFF_SKILL.exists():
            pytest.skip("pipeline-kickoff/SKILL.md not yet created")
        content = PIPELINE_KICKOFF_SKILL.read_text()
        assert "## Steps" in content or "## steps" in content.lower(), (
            "pipeline-kickoff/SKILL.md must have a '## Steps' section."
        )

    def test_pipeline_kickoff_validates_sources_yaml(self):
        """B4: SKILL.md must reference sources.yaml or data-sources.yaml validation.

        RED: File does not yet exist.
        GREEN after: Body contains reference to 'sources.yaml' or 'data-sources.yaml'
                     in the context of validation / checking.
        """
        if not PIPELINE_KICKOFF_SKILL.exists():
            pytest.skip("pipeline-kickoff/SKILL.md not yet created")
        content = PIPELINE_KICKOFF_SKILL.read_text()
        has_sources_ref = (
            "sources.yaml" in content
            or "data-sources.yaml" in content
        )
        assert has_sources_ref, (
            "pipeline-kickoff/SKILL.md must reference 'sources.yaml' or "
            "'data-sources.yaml' — the skill validates that the pipeline's data source "
            "is registered in the shared catalog before kicking off the story."
        )

    def test_pipeline_kickoff_wraps_prompt_template(self):
        """B5: SKILL.md must reference the pipeline-kickoff-prompt.md template.

        RED: File does not yet exist.
        GREEN after: Body contains 'pipeline-kickoff-prompt.md'.
        """
        if not PIPELINE_KICKOFF_SKILL.exists():
            pytest.skip("pipeline-kickoff/SKILL.md not yet created")
        content = PIPELINE_KICKOFF_SKILL.read_text()
        assert "pipeline-kickoff-prompt.md" in content, (
            "pipeline-kickoff/SKILL.md must reference 'pipeline-kickoff-prompt.md'. "
            "The skill wraps this template to structure the Phase 1 seed for a new "
            "pipeline story."
        )


# ---------------------------------------------------------------------------
# Group C — pipeline-kickoff-prompt.md (2 tests)
# ---------------------------------------------------------------------------


class TestPipelineKickoffPrompt:
    """AC-3: pipeline-kickoff-prompt.md template exists with required sections."""

    def test_pipeline_kickoff_prompt_exists(self):
        """C1: pipeline-kickoff-prompt.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1004-scaffold-drift-kickoff/
                     pipeline-kickoff-prompt.md.
        """
        assert PIPELINE_KICKOFF_PROMPT.exists(), (
            f"pipeline-kickoff-prompt.md not found at {PIPELINE_KICKOFF_PROMPT}. "
            "Phase 8 must create this template for pipeline story Phase 1 seeds."
        )

    def test_pipeline_kickoff_prompt_has_required_sections(self):
        """C2: Prompt template must contain required planning sections.

        Required: source system, target tables, schedule, latency

        RED: File does not yet exist.
        GREEN after: File contains all four required section headings or keywords.
        """
        if not PIPELINE_KICKOFF_PROMPT.exists():
            pytest.skip("pipeline-kickoff-prompt.md not yet created")
        content = PIPELINE_KICKOFF_PROMPT.read_text().lower()
        required_sections = [
            ("source system", "source"),
            ("target tables", "target"),
            ("schedule", "schedule"),
            ("latency", "latency"),
        ]
        missing = []
        for primary, fallback in required_sections:
            if primary not in content and fallback not in content:
                missing.append(primary)
        assert not missing, (
            f"pipeline-kickoff-prompt.md is missing required sections: {missing}. "
            "The template must cover: source system, target tables, schedule, latency, "
            "dependencies, and test strategy."
        )


# ---------------------------------------------------------------------------
# Group D — MANUAL-STEPS.md (4 tests)
# ---------------------------------------------------------------------------


class TestManualSteps:
    """AC-4: MANUAL-STEPS.md has numbered instructions for Mark covering copy + submodule bump."""

    def test_manual_steps_exists(self):
        """D1: MANUAL-STEPS.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-1004-scaffold-drift-kickoff/MANUAL-STEPS.md.
        """
        assert MANUAL_STEPS.exists(), (
            f"MANUAL-STEPS.md not found at {MANUAL_STEPS}. "
            "Phase 8 must create this file with step-by-step instructions for Mark."
        )

    def test_manual_steps_has_copy_instructions(self):
        """D2: MANUAL-STEPS.md must contain instructions to copy skills to .sdlc/skills/.

        RED: File does not yet exist.
        GREEN after: Contains 'cp' or 'copy' alongside '.sdlc/skills'.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        has_copy = (
            ("cp " in content and ".sdlc/skills" in content)
            or ("copy" in content.lower() and ".sdlc/skills" in content)
        )
        assert has_copy, (
            "MANUAL-STEPS.md must instruct Mark to copy the skill files to "
            ".sdlc/skills/. Expected 'cp' or 'copy' together with '.sdlc/skills'."
        )

    def test_manual_steps_has_git_submodule_reference(self):
        """D3: MANUAL-STEPS.md must reference git -C .sdlc for submodule operations.

        RED: File does not yet exist.
        GREEN after: Contains 'git -C .sdlc'.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        assert "git -C .sdlc" in content, (
            "MANUAL-STEPS.md must reference 'git -C .sdlc' for committing and pushing "
            "inside the .sdlc submodule. This is the safe submodule git command pattern."
        )

    def test_manual_steps_has_numbered_steps(self):
        """D4: MANUAL-STEPS.md must have at least 4 numbered steps.

        Steps required (minimum):
        1. Copy scaffold-drift-check to .sdlc/skills/
        2. Copy pipeline-kickoff to .sdlc/skills/
        3. Commit + push inside .sdlc submodule
        4. Bump submodule reference in tech-dev-agents

        RED: File does not yet exist.
        GREEN after: Contains at least 4 numbered list items.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        numbered_pattern = re.compile(r"^\s*\d+[\.\)]\s", re.MULTILINE)
        steps = numbered_pattern.findall(content)
        assert len(steps) >= 4, (
            f"MANUAL-STEPS.md has {len(steps)} numbered steps but needs at least 4. "
            "Required: (1) copy scaffold-drift-check, (2) copy pipeline-kickoff, "
            "(3) commit+push in .sdlc, (4) bump submodule reference."
        )
