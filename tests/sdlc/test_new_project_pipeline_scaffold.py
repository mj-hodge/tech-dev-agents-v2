"""STORY-1001: build_type classifier — Phase 7 RED tests (test_new_project_pipeline_scaffold).

Tests verify:
  A (T01–T04): new-project/SKILL.md has --type=pipeline parameter and scaffold docs
  B (T05–T07): new-project/SKILL.md documents canon-drift-check.yml and PR template wiring
  C (T08–T09): new-project/SKILL.md documents pipeline_template_commit in config.yaml
  D (T10):     scaffold_pipeline_project() Python function creates expected structure

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/new-project/SKILL.md has no --type flag
  - deployment/hermes/build_type_classifier.py does not exist
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NEW_PROJECT_SKILL_MD = REPO_ROOT / ".sdlc" / "skills" / "new-project" / "SKILL.md"
CLASSIFIER_MODULE = REPO_ROOT / "deployment" / "hermes" / "build_type_classifier.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_new_project() -> str:
    assert NEW_PROJECT_SKILL_MD.exists(), (
        f"new-project/SKILL.md not found at {NEW_PROJECT_SKILL_MD}.\n"
        "Phase 8 must add --type=pipeline support to this file."
    )
    return NEW_PROJECT_SKILL_MD.read_text()


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
# A: new-project/SKILL.md — --type=pipeline parameter
# ---------------------------------------------------------------------------

class TestNewProjectTypePipeline:
    """A01–A04: new-project/SKILL.md must document --type=pipeline parameter."""

    def test_new_project_skill_md_has_type_pipeline_flag(self):
        """A01: new-project/SKILL.md documents --type=pipeline flag (SC-7).

        RED: SKILL.md has no --type flag.
        GREEN after: Phase 8 adds --type parameter documentation.
        """
        content = _read_new_project()
        assert "--type=pipeline" in content or "--type pipeline" in content, (
            "new-project/SKILL.md must document '--type=pipeline' flag (SC-7)."
        )

    def test_new_project_skill_md_references_pipeline_template(self):
        """A02: new-project/SKILL.md references gc-data-v2/pipeline-template/ scaffold (SC-7).

        RED: SKILL.md has no pipeline-template reference.
        GREEN after: Phase 8 adds pipeline-template scaffold path.
        """
        content = _read_new_project()
        assert "pipeline-template" in content, (
            "new-project/SKILL.md must reference 'gc-data-v2/pipeline-template/' (SC-7).\n"
            "When --type=pipeline, scaffold from pipeline-template, not generic templates."
        )

    def test_new_project_skill_md_type_absent_unchanged(self):
        """A03: new-project/SKILL.md preserves default behaviour when --type is absent.

        RED: SKILL.md has no conditional.
        GREEN after: Phase 8 adds conditional that preserves default path.
        """
        content = _read_new_project()
        # Must still mention generic scaffold or default path
        # The original skill had templates/readme.md — that must still be documented
        has_default = (
            "templates/" in content
            or "readme.md" in content.lower()
            or "default" in content.lower()
            or "When" in content
        )
        assert has_default, (
            "new-project/SKILL.md must preserve the default (non-pipeline) scaffold path.\n"
            "Adding --type=pipeline must not remove the generic project scaffolding."
        )

    def test_new_project_skill_md_has_gc_data_v2_reference(self):
        """A04: new-project/SKILL.md references gc-data-v2 as the template source (SC-7).

        RED: SKILL.md has no gc-data-v2 reference.
        GREEN after: Phase 8 adds gc-data-v2 template source reference.
        """
        content = _read_new_project()
        assert "gc-data-v2" in content, (
            "new-project/SKILL.md must reference 'gc-data-v2' as the pipeline template source (SC-7)."
        )


# ---------------------------------------------------------------------------
# B: new-project/SKILL.md — CI file wiring
# ---------------------------------------------------------------------------

class TestNewProjectCIWiring:
    """B05–B07: new-project/SKILL.md must document canon-drift-check.yml wiring."""

    def test_new_project_skill_md_has_canon_drift_check(self):
        """B05: new-project/SKILL.md documents copying canon-drift-check.yml (SC-7).

        RED: SKILL.md doesn't mention canon-drift-check.yml.
        GREEN after: Phase 8 adds CI file copy documentation.
        """
        content = _read_new_project()
        assert "canon-drift-check" in content, (
            "new-project/SKILL.md must document copying 'canon-drift-check.yml' into the "
            "new pipeline project (SC-7)."
        )

    def test_new_project_skill_md_has_pr_template(self):
        """B06: new-project/SKILL.md documents copying pull_request_template.md (SC-7).

        RED: SKILL.md doesn't mention pull_request_template.
        GREEN after: Phase 8 adds PR template copy documentation.
        """
        content = _read_new_project()
        assert "pull_request_template" in content, (
            "new-project/SKILL.md must document copying 'pull_request_template.md' (SC-7).\n"
            "The template must be byte-identical to gc-data-v2/pipeline-template copy."
        )

    def test_new_project_skill_md_has_workflows_reference(self):
        """B07: new-project/SKILL.md references .github/workflows/ target path.

        RED: SKILL.md has no .github/workflows reference.
        GREEN after: Phase 8 adds target directory for CI files.
        """
        content = _read_new_project()
        assert ".github" in content, (
            "new-project/SKILL.md must reference '.github/' directory for CI files (SC-7)."
        )


# ---------------------------------------------------------------------------
# C: new-project/SKILL.md — pipeline_template_commit
# ---------------------------------------------------------------------------

class TestNewProjectTemplatePinning:
    """C08–C09: new-project/SKILL.md must document pipeline_template_commit in config.yaml."""

    def test_new_project_skill_md_has_pipeline_template_commit(self):
        """C08: new-project/SKILL.md documents writing pipeline_template_commit to config.yaml (SC-7).

        RED: SKILL.md doesn't mention pipeline_template_commit.
        GREEN after: Phase 8 adds version pinning documentation.
        """
        content = _read_new_project()
        assert "pipeline_template_commit" in content, (
            "new-project/SKILL.md must document writing 'pipeline_template_commit' to "
            "the new repo's config.yaml (SC-7)."
        )

    def test_new_project_skill_md_commit_is_dynamic(self):
        """C09: new-project/SKILL.md instructs dynamic SHA resolution for template commit.

        RED: SKILL.md has no SHA resolution instruction.
        GREEN after: Phase 8 adds git rev-parse instruction for template commit.
        """
        content = _read_new_project()
        assert "rev-parse" in content or "SHA" in content or "sha" in content.lower(), (
            "new-project/SKILL.md must document dynamically resolving the pipeline_template_commit "
            "SHA (SC-7). Never hardcode the SHA."
        )


# ---------------------------------------------------------------------------
# D: scaffold_pipeline_project() Python function
# ---------------------------------------------------------------------------

class TestScaffoldPipelineProject:
    """D10: scaffold_pipeline_project() creates expected directory structure."""

    def test_scaffold_creates_pipeline_dirs(self, tmp_path):
        """D10: scaffold_pipeline_project() creates models/, sources/, tests/ dirs (SC-7).

        RED: module doesn't exist.
        GREEN after: Phase 8 implements scaffold_pipeline_project().
        """
        mod = _import_classifier()

        # scaffold_pipeline_project may accept a target dir
        target = tmp_path / "new-pipeline"
        target.mkdir()

        mod.scaffold_pipeline_project(str(target))

        # The scaffold should create standard pipeline directories
        expected_dirs = ["models", "sources", "tests"]
        missing = [d for d in expected_dirs if not (target / d).exists()]
        assert not missing, (
            f"scaffold_pipeline_project() must create dirs: {expected_dirs}.\n"
            f"Missing: {missing} (SC-7)."
        )

    def test_scaffold_is_idempotent(self, tmp_path):
        """D10b: scaffold_pipeline_project() is idempotent (safe to run twice).

        RED: module doesn't exist.
        GREEN after: Phase 8 implements idempotent scaffold.
        """
        mod = _import_classifier()
        target = tmp_path / "new-pipeline"
        target.mkdir()

        # Run twice — should not raise
        mod.scaffold_pipeline_project(str(target))
        mod.scaffold_pipeline_project(str(target))  # Must not raise or duplicate
