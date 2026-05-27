"""STORY-1001: build_type classifier — Phase 7 RED tests (test_phase1_canon_load).

Tests verify:
  A (T01–T04): phase-1/SKILL.md contains pipeline branch with canon loading
  B (T05–T07): phase-1/SKILL.md documents source selection sub-prompt
  C (T08–T10): phase-1/SKILL.md documents gc_data_v2_commit SHA pinning
  D (T11–T13): phase-1/SKILL.md documents ## Canon loaded + ## Source seed sections
  E (T14–T15): load_pipeline_canon() Python function returns correct structure
  F (T16):     pipeline-seed-additions.md template exists

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/phase-1/SKILL.md has no build_type/pipeline branch
  - deployment/hermes/build_type_classifier.py does not exist
  - .sdlc/templates/pipeline-seed-additions.md does not exist
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE1_SKILL_MD = REPO_ROOT / ".sdlc" / "skills" / "phase-1" / "SKILL.md"
CLASSIFIER_MODULE = REPO_ROOT / "deployment" / "hermes" / "build_type_classifier.py"
PIPELINE_SEED_ADDITIONS = REPO_ROOT / ".sdlc" / "templates" / "pipeline-seed-additions.md"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sdlc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_phase1() -> str:
    assert PHASE1_SKILL_MD.exists(), (
        f"phase-1/SKILL.md not found at {PHASE1_SKILL_MD}.\n"
        "Phase 8 must add the pipeline build_type branch to this file."
    )
    return PHASE1_SKILL_MD.read_text()


def _import_classifier():
    assert CLASSIFIER_MODULE.exists(), (
        f"build_type_classifier.py not found at {CLASSIFIER_MODULE}.\n"
        "Phase 8 must create this module."
    )
    spec = importlib.util.spec_from_file_location("build_type_classifier", CLASSIFIER_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# A: phase-1/SKILL.md — pipeline branch presence
# ---------------------------------------------------------------------------

class TestPhase1PipelineBranch:
    """A01–A04: phase-1/SKILL.md must have a pipeline-specific branch."""

    def test_phase1_skill_md_has_build_type(self):
        """A01: phase-1/SKILL.md mentions build_type (SC-3).

        RED: SKILL.md has no build_type content yet.
        GREEN after: Phase 8 adds pipeline branch to step 4.
        """
        content = _read_phase1()
        assert "build_type" in content, (
            "phase-1/SKILL.md must mention 'build_type' (SC-3).\n"
            "Add pipeline-aware branch in step 4 per seed § Scope."
        )

    def test_phase1_skill_md_references_pipeline_standard(self):
        """A02: phase-1/SKILL.md references pipeline-standard.md for pipeline builds (SC-3).

        RED: SKILL.md doesn't reference gc-data-v2 docs.
        GREEN after: Phase 8 adds canon reading list to pipeline branch.
        """
        content = _read_phase1()
        assert "pipeline-standard.md" in content, (
            "phase-1/SKILL.md must reference gc-data-v2/platform/pipeline-standard.md (SC-3)."
        )

    def test_phase1_skill_md_references_failure_modes(self):
        """A03: phase-1/SKILL.md references failure-modes.md (SC-3).

        RED: SKILL.md doesn't reference failure-modes.md.
        GREEN after: Phase 8 adds full canon reading list.
        """
        content = _read_phase1()
        assert "failure-modes.md" in content, (
            "phase-1/SKILL.md must reference gc-data-v2/platform/failure-modes.md (SC-3)."
        )

    def test_phase1_skill_md_references_new_pipeline_repo(self):
        """A04: phase-1/SKILL.md references new-pipeline-repo.md (SC-3).

        RED: SKILL.md doesn't reference new-pipeline-repo.md.
        GREEN after: Phase 8 adds full canon reading list.
        """
        content = _read_phase1()
        assert "new-pipeline-repo.md" in content, (
            "phase-1/SKILL.md must reference gc-data-v2/platform/new-pipeline-repo.md (SC-3)."
        )


# ---------------------------------------------------------------------------
# B: phase-1/SKILL.md — source selection sub-prompt
# ---------------------------------------------------------------------------

class TestPhase1SourceSelection:
    """B05–B07: phase-1/SKILL.md must document the source selection sub-prompt."""

    def test_phase1_skill_md_has_which_source_prompt(self):
        """B05: phase-1/SKILL.md documents 'Which source?' prompt.

        RED: SKILL.md has no source prompt.
        GREEN after: Phase 8 adds source selection to pipeline branch.
        """
        content = _read_phase1()
        assert "source" in content.lower(), (
            "phase-1/SKILL.md must include source selection prompt for pipeline builds."
        )

    def test_phase1_skill_md_has_source_enum_values(self):
        """B06: phase-1/SKILL.md documents the closed source enum (SC-1).

        RED: SKILL.md has no source enum.
        GREEN after: Phase 8 adds the 8+other source values.
        """
        content = _read_phase1()
        for slug in ["amazon-sp-api", "walmart-supplier", "shopify", "netsuite"]:
            assert slug in content, (
                f"phase-1/SKILL.md must list source slug '{slug}' in the source enum."
            )

    def test_phase1_skill_md_has_other_option(self):
        """B07: 'other' is a valid source choice in phase-1/SKILL.md.

        RED: SKILL.md has no 'other' source option.
        GREEN after: Phase 8 includes 'other' in the source enum.
        """
        content = _read_phase1()
        assert "other" in content, (
            "phase-1/SKILL.md must include 'other' as a valid source option (OQ-3)."
        )


# ---------------------------------------------------------------------------
# C: phase-1/SKILL.md — SHA pinning
# ---------------------------------------------------------------------------

class TestPhase1SHAPinning:
    """C08–C10: phase-1/SKILL.md must document gc_data_v2_commit SHA pinning (SC-4)."""

    def test_phase1_skill_md_has_gc_data_v2_commit(self):
        """C08: phase-1/SKILL.md documents gc_data_v2_commit key (SC-4).

        RED: SKILL.md has no gc_data_v2_commit mention.
        GREEN after: Phase 8 adds SHA pinning step.
        """
        content = _read_phase1()
        assert "gc_data_v2_commit" in content, (
            "phase-1/SKILL.md must document gc_data_v2_commit SHA pinning (SC-4)."
        )

    def test_phase1_skill_md_uses_git_rev_parse(self):
        """C09: phase-1/SKILL.md instructs use of git rev-parse HEAD (SC-4).

        RED: SKILL.md has no git rev-parse instruction.
        GREEN after: Phase 8 adds dynamic SHA resolution step.
        """
        content = _read_phase1()
        assert "rev-parse" in content, (
            "phase-1/SKILL.md must instruct resolving SHA via 'git rev-parse HEAD' (SC-4).\n"
            "SHA must never be hardcoded."
        )

    def test_phase1_skill_md_pins_sha_to_project(self):
        """C10: phase-1/SKILL.md documents writing gc_data_v2_commit to .project.

        RED: SKILL.md doesn't mention .project persistence for SHA.
        GREEN after: Phase 8 adds .project SHA write step.
        """
        content = _read_phase1()
        assert ".project" in content, (
            "phase-1/SKILL.md must document writing gc_data_v2_commit to .project (SC-4)."
        )


# ---------------------------------------------------------------------------
# D: phase-1/SKILL.md — Canon loaded / Source seed sections
# ---------------------------------------------------------------------------

class TestPhase1SeedSections:
    """D11–D13: phase-1/SKILL.md documents the new seed.md sections required for pipeline."""

    def test_phase1_skill_md_has_canon_loaded_section(self):
        """D11: phase-1/SKILL.md documents '## Canon loaded' section for pipeline seeds (SC-3).

        RED: SKILL.md doesn't mention Canon loaded section.
        GREEN after: Phase 8 adds Canon loaded section spec.
        """
        content = _read_phase1()
        assert "Canon loaded" in content, (
            "phase-1/SKILL.md must document the '## Canon loaded' section required "
            "in pipeline seeds (SC-3)."
        )

    def test_phase1_skill_md_has_source_section(self):
        """D12: phase-1/SKILL.md documents '## Source' section for pipeline seeds (SC-3).

        RED: SKILL.md doesn't mention Source section.
        GREEN after: Phase 8 adds Source section spec.
        """
        content = _read_phase1()
        # Must mention the ## Source seed section
        assert "## Source" in content or "seed.md" in content, (
            "phase-1/SKILL.md must document the '## Source' section in pipeline seeds (SC-3)."
        )

    def test_phase1_skill_md_non_pipeline_path_unchanged(self):
        """D13: phase-1/SKILL.md preserves non-pipeline flow (SC-6).

        RED: SKILL.md has no conditional branch.
        GREEN after: Phase 8 wraps pipeline steps in a build_type == pipeline conditional.
        """
        content = _read_phase1()
        # The pipeline branch must be conditional, not unconditional
        assert "pipeline" in content, (
            "phase-1/SKILL.md must gate pipeline steps on build_type == pipeline (SC-6)."
        )
        # Must still have original step 4 text or the conditional marker
        has_conditional = (
            "build_type == pipeline" in content
            or "build_type: pipeline" in content
            or "When build_type" in content
            or "if build_type" in content.lower()
        )
        assert has_conditional, (
            "phase-1/SKILL.md must use a conditional to guard pipeline steps (SC-6).\n"
            "Non-pipeline builds must be byte-identical to the pre-story flow."
        )


# ---------------------------------------------------------------------------
# E: load_pipeline_canon() Python function
# ---------------------------------------------------------------------------

class TestLoadPipelineCanon:
    """E14–E15: load_pipeline_canon() returns correct structure."""

    def test_load_pipeline_canon_returns_dict(self, tmp_path):
        """E14: load_pipeline_canon() returns a dict with 'docs', 'warnings', 'commit'.

        RED: module doesn't exist.
        GREEN after: Phase 8 implements load_pipeline_canon().
        """
        mod = _import_classifier()
        # Create minimal gc-data-v2 fixture
        gc_root = tmp_path / "gc-data-v2"
        platform = gc_root / "platform"
        platform.mkdir(parents=True)
        sources_dir = gc_root / "sources" / "walmart-supplier"
        sources_dir.mkdir(parents=True)

        for fname in ["pipeline-standard.md", "failure-modes.md", "new-pipeline-repo.md"]:
            (platform / fname).write_text(f"# {fname}")
        (sources_dir / "README.md").write_text("# walmart-supplier README")

        # Init git repo so commit SHA can be resolved
        import subprocess
        subprocess.run(["git", "init", str(gc_root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(gc_root), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(gc_root), check=True, capture_output=True
        )
        subprocess.run(["git", "add", "."], cwd=str(gc_root), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(gc_root), check=True, capture_output=True
        )

        result = mod.load_pipeline_canon(str(gc_root), "walmart-supplier")

        assert isinstance(result, dict), "load_pipeline_canon() must return a dict"
        assert "docs" in result, "Result must have 'docs' key"
        assert "warnings" in result, "Result must have 'warnings' key"
        assert "commit" in result, "Result must have 'commit' key"

    def test_load_pipeline_canon_lists_four_docs(self, tmp_path):
        """E15: load_pipeline_canon() lists all 4 required docs when all present (SC-3).

        RED: module doesn't exist.
        GREEN after: Phase 8 returns all 4 canon paths.
        """
        mod = _import_classifier()
        gc_root = tmp_path / "gc-data-v2"
        platform = gc_root / "platform"
        platform.mkdir(parents=True)
        sources_dir = gc_root / "sources" / "walmart-supplier"
        sources_dir.mkdir(parents=True)

        for fname in ["pipeline-standard.md", "failure-modes.md", "new-pipeline-repo.md"]:
            (platform / fname).write_text(f"# {fname}")
        (sources_dir / "README.md").write_text("# README")

        import subprocess
        subprocess.run(["git", "init", str(gc_root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(gc_root), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(gc_root), check=True, capture_output=True
        )
        subprocess.run(["git", "add", "."], cwd=str(gc_root), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(gc_root), check=True, capture_output=True
        )

        result = mod.load_pipeline_canon(str(gc_root), "walmart-supplier")
        docs = result["docs"]

        assert len(docs) == 4, (
            f"Expected 4 docs in canon list (3 platform + 1 source README), got {len(docs)}.\n"
            "SC-3: pipeline-standard.md, failure-modes.md, new-pipeline-repo.md, sources/*/README.md"
        )
        doc_names = [Path(d).name for d in docs]
        for expected in ["pipeline-standard.md", "failure-modes.md", "new-pipeline-repo.md", "README.md"]:
            assert expected in doc_names, (
                f"Expected '{expected}' in canon docs list, got: {doc_names}"
            )


# ---------------------------------------------------------------------------
# F: pipeline-seed-additions.md template
# ---------------------------------------------------------------------------

class TestPipelineSeedAdditionsTemplate:
    """F16: pipeline-seed-additions.md template must exist."""

    def test_pipeline_seed_additions_template_exists(self):
        """F16: templates/pipeline-seed-additions.md exists (seed § Files NEW).

        RED: file doesn't exist.
        GREEN after: Phase 8 creates the canonical pipeline seed sections template.
        """
        assert PIPELINE_SEED_ADDITIONS.exists(), (
            f"templates/pipeline-seed-additions.md not found at {PIPELINE_SEED_ADDITIONS}.\n"
            "Phase 8 must create this template with '## Canon loaded' and '## Source' sections."
        )

    def test_pipeline_seed_additions_has_canon_loaded_section(self):
        """F16b: pipeline-seed-additions.md contains '## Canon loaded' template text.

        RED: file doesn't exist.
        GREEN after: Phase 8 creates the template.
        """
        assert PIPELINE_SEED_ADDITIONS.exists(), (
            f"pipeline-seed-additions.md not found. Phase 8 must create it."
        )
        content = PIPELINE_SEED_ADDITIONS.read_text()
        assert "Canon loaded" in content, (
            "pipeline-seed-additions.md must contain a '## Canon loaded' section."
        )
        assert "## Source" in content, (
            "pipeline-seed-additions.md must contain a '## Source' section."
        )
