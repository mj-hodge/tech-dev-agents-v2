"""STORY-1001: build_type classifier — Phase 7 RED tests (test_spec_classifier).

Tests verify:
  A (T01–T05): Python classifier module — classify_build_type() logic
  B (T06–T09): Python classifier — validate_source() normalisation + validation
  C (T10–T12): Python classifier — resolve_gc_data_v2_commit() behaviour
  D (T13–T17): spec/SKILL.md content — classifier step, enum, persistence, heuristic
  E (T18–T20): spec/codex.md Codex parity (SC-8)

RED reasons (all tests fail until Phase 8):
  - deployment/hermes/build_type_classifier.py does not exist yet
  - .sdlc/skills/spec/SKILL.md has no build_type step
  - .sdlc/skills/spec/codex.md has no build_type documentation
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLASSIFIER_MODULE = REPO_ROOT / "deployment" / "hermes" / "build_type_classifier.py"
SPEC_SKILL_MD = REPO_ROOT / ".sdlc" / "skills" / "spec" / "SKILL.md"
SPEC_CODEX_MD = REPO_ROOT / ".sdlc" / "skills" / "spec" / "codex.md"
BUILD_TYPE_CLASSIFIER_DOC = REPO_ROOT / ".sdlc" / "skills" / "spec" / "build-type-classifier.md"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sdlc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_classifier():
    """Import build_type_classifier, failing the test with a helpful message if absent."""
    assert CLASSIFIER_MODULE.exists(), (
        f"deployment/hermes/build_type_classifier.py not found at {CLASSIFIER_MODULE}.\n"
        "Phase 8 must create this module with classify_build_type(), validate_source(),\n"
        "resolve_gc_data_v2_commit(), and load_pipeline_canon() (per feature-spec.md)."
    )
    spec = importlib.util.spec_from_file_location("build_type_classifier", CLASSIFIER_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_spec_skill() -> str:
    assert SPEC_SKILL_MD.exists(), (
        f"spec/SKILL.md not found at {SPEC_SKILL_MD}.\n"
        "Phase 8 must add the build_type classifier step to this file."
    )
    return SPEC_SKILL_MD.read_text()


def _read_codex() -> str:
    assert SPEC_CODEX_MD.exists(), (
        f"spec/codex.md not found at {SPEC_CODEX_MD}.\n"
        "Phase 8 must update codex.md to document the build_type flow (SC-8 parity)."
    )
    return SPEC_CODEX_MD.read_text()


# ---------------------------------------------------------------------------
# A: classify_build_type — keyword classification
# ---------------------------------------------------------------------------

class TestClassifyBuildType:
    """A01–A05: classify_build_type() must map descriptions to the correct build type."""

    def test_pipeline_keyword_dag(self):
        """A01: 'DAG' in description → pipeline.

        RED: module doesn't exist yet.
        GREEN after: Phase 8 creates build_type_classifier.py with pipeline keywords.
        """
        mod = _import_classifier()
        result = mod.classify_build_type("Add walmart-supplier orders DAG for marketplaces")
        assert result == "pipeline", (
            f"Expected 'pipeline' for description containing 'DAG', got '{result}'.\n"
            "classify_build_type() must detect pipeline keywords (SC-1)."
        )

    def test_pipeline_keyword_airflow(self):
        """A02: 'Airflow' in description → pipeline.

        RED: module doesn't exist.
        GREEN after: Phase 8 implements pipeline keyword matching.
        """
        mod = _import_classifier()
        result = mod.classify_build_type("Refactor Airflow sensor for bronze ingestion")
        assert result == "pipeline", (
            f"Expected 'pipeline' for 'Airflow', got '{result}'."
        )

    def test_feature_default(self):
        """A03: No keywords → feature (default).

        RED: module doesn't exist.
        GREEN after: Phase 8 returns 'feature' as default.
        """
        mod = _import_classifier()
        result = mod.classify_build_type("Add dark mode toggle to the settings page")
        assert result == "feature", (
            f"Expected 'feature' default for non-matching description, got '{result}' (SC-6)."
        )

    def test_bug_keyword(self):
        """A04: 'fix'/'bug' in description → bug.

        RED: module doesn't exist.
        GREEN after: Phase 8 implements bug keyword matching.
        """
        mod = _import_classifier()
        result = mod.classify_build_type("Fix off-by-one error in order count calculation")
        assert result == "bug", (
            f"Expected 'bug' for fix/off-by-one description, got '{result}'."
        )

    def test_pipeline_source_slug_as_keyword(self):
        """A05: Source slug in description acts as a pipeline keyword.

        RED: module doesn't exist.
        GREEN after: Phase 8 includes source slugs in pipeline keyword list.
        """
        mod = _import_classifier()
        result = mod.classify_build_type("walmart-supplier orders endpoint enhancement")
        assert result == "pipeline", (
            f"Expected 'pipeline' because 'walmart-supplier' is a pipeline source slug, got '{result}'."
        )

    def test_pipeline_fixture_input(self):
        """A05b: Pipeline fixture input classifies as pipeline.

        RED: module doesn't exist.
        GREEN after: Phase 8 creates the classifier.
        """
        mod = _import_classifier()
        text = (FIXTURES_DIR / "spec-pipeline-input.txt").read_text().strip()
        result = mod.classify_build_type(text)
        assert result == "pipeline", (
            f"Fixture '{text}' expected to classify as 'pipeline', got '{result}'."
        )

    def test_feature_fixture_input(self):
        """A05c: Feature fixture input classifies as feature.

        RED: module doesn't exist.
        GREEN after: Phase 8 creates the classifier.
        """
        mod = _import_classifier()
        text = (FIXTURES_DIR / "spec-feature-input.txt").read_text().strip()
        result = mod.classify_build_type(text)
        assert result == "feature", (
            f"Fixture '{text}' expected to classify as 'feature', got '{result}'."
        )


# ---------------------------------------------------------------------------
# B: validate_source — normalisation + validation
# ---------------------------------------------------------------------------

class TestValidateSource:
    """B06–B09: validate_source() normalises and validates source slugs."""

    def _sources_yaml(self) -> str:
        return str(FIXTURES_DIR / "gc-data-v2-snapshot" / "sources.yaml")

    def test_exact_slug_passes(self):
        """B06: Exact kebab-case slug validates successfully.

        RED: module doesn't exist.
        GREEN after: Phase 8 implements validate_source().
        """
        mod = _import_classifier()
        result = mod.validate_source("walmart-supplier", self._sources_yaml())
        assert result == "walmart-supplier", (
            f"Expected 'walmart-supplier', got '{result}'."
        )

    def test_underscore_normalisation(self):
        """B07: Underscore slug is normalised to kebab-case.

        RED: module doesn't exist.
        GREEN after: Phase 8 normalises _ → -.
        """
        mod = _import_classifier()
        result = mod.validate_source("amazon_sp_api", self._sources_yaml())
        assert result == "amazon-sp-api", (
            f"Expected 'amazon-sp-api' after normalising 'amazon_sp_api', got '{result}' (OQ-2)."
        )

    def test_unknown_source_raises(self):
        """B08: Unknown source raises ValueError with exact message (SC-5).

        RED: module doesn't exist.
        GREEN after: Phase 8 raises ValueError with exact error text.
        """
        mod = _import_classifier()
        with pytest.raises(ValueError) as exc_info:
            mod.validate_source("foobar", self._sources_yaml())
        msg = str(exc_info.value)
        assert "Unknown source 'foobar'" in msg, (
            f"Expected error message to contain \"Unknown source 'foobar'\", got: {msg}"
        )
        assert "gc-data-v2/sources.yaml" in msg, (
            f"Expected error message to reference 'gc-data-v2/sources.yaml', got: {msg}"
        )
        assert "'other'" in msg or "other" in msg, (
            f"Expected error to suggest using 'other', got: {msg}"
        )

    def test_other_passes(self):
        """B09: 'other' slug passes validation without YAML check.

        RED: module doesn't exist.
        GREEN after: Phase 8 allows 'other' unconditionally.
        """
        mod = _import_classifier()
        result = mod.validate_source("other", self._sources_yaml())
        assert result == "other", (
            f"Expected 'other' to pass validation, got '{result}' (OQ-3)."
        )


# ---------------------------------------------------------------------------
# C: resolve_gc_data_v2_commit — SHA resolution
# ---------------------------------------------------------------------------

class TestResolveCommit:
    """C10–C12: resolve_gc_data_v2_commit() must resolve a real 40-char SHA."""

    def test_raises_when_no_paths(self):
        """C10: RuntimeError raised when no gc-data-v2 checkout found.

        RED: module doesn't exist.
        GREEN after: Phase 8 raises RuntimeError for missing checkout (Escalation #1).
        """
        mod = _import_classifier()
        with pytest.raises(RuntimeError) as exc_info:
            mod.resolve_gc_data_v2_commit(repo_paths=["/nonexistent/path/gc-data-v2"])
        msg = str(exc_info.value)
        assert "gc-data-v2" in msg.lower(), (
            f"RuntimeError must mention gc-data-v2. Got: {msg}"
        )

    def test_sha_format_if_present(self, tmp_path):
        """C11: Returns 40-char hex SHA when valid git repo found.

        RED: module doesn't exist.
        GREEN after: Phase 8 resolves SHA via git rev-parse HEAD.

        Uses a temp git repo as a fixture so the test is self-contained.
        """
        mod = _import_classifier()
        # Create a minimal git repo to use as a fixture
        import subprocess
        repo = tmp_path / "gc-data-v2"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo), check=True, capture_output=True
        )
        (repo / "README.md").write_text("placeholder")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True
        )
        sha = mod.resolve_gc_data_v2_commit(repo_paths=[str(repo)])
        assert len(sha) == 40, (
            f"Expected 40-char hex SHA, got '{sha}' (len={len(sha)}) (SC-4)."
        )
        assert all(c in "0123456789abcdef" for c in sha), (
            f"SHA must be lowercase hex, got '{sha}'."
        )

    def test_sha_never_hardcoded(self):
        """C12: SHA must be resolved dynamically (boundary: never hardcode a SHA).

        RED: module doesn't exist.
        GREEN after: Phase 8 implements dynamic SHA resolution.

        This test reads the source file to detect hardcoded 40-char hex strings.
        """
        assert CLASSIFIER_MODULE.exists(), (
            f"build_type_classifier.py not found. Phase 8 must create it."
        )
        source = CLASSIFIER_MODULE.read_text()
        import re
        # Find any 40-char hex literals (SHA-like strings in quotes)
        sha_pattern = re.compile(r'["\']([0-9a-f]{40})["\']')
        matches = sha_pattern.findall(source)
        assert not matches, (
            f"build_type_classifier.py contains hardcoded SHA(s): {matches}.\n"
            "SHA must always be resolved dynamically via git rev-parse HEAD (SC-4 boundary)."
        )


# ---------------------------------------------------------------------------
# D: spec/SKILL.md — classifier step content
# ---------------------------------------------------------------------------

class TestSpecSkillMdClassifier:
    """D13–D17: spec/SKILL.md must document the build_type classifier step."""

    def test_skill_md_has_build_type_step(self):
        """D13: spec/SKILL.md contains a build_type classifier step (SC-1).

        RED: SKILL.md has no build_type content yet.
        GREEN after: Phase 8 adds classifier step to spec/SKILL.md.
        """
        content = _read_spec_skill()
        assert "build_type" in content, (
            "spec/SKILL.md must contain a 'build_type' classifier step (SC-1).\n"
            "Add step 1.5 between context-read and Phase 1 activation."
        )

    def test_skill_md_has_five_value_enum(self):
        """D14: spec/SKILL.md documents the 5-value build_type enum (SC-1).

        RED: SKILL.md has no build_type enum.
        GREEN after: Phase 8 adds the closed enum.
        """
        content = _read_spec_skill()
        for value in ["pipeline", "feature", "bug", "ops", "infra"]:
            assert value in content, (
                f"spec/SKILL.md must document build_type value '{value}'.\n"
                "All 5 accepted values must appear (SC-1)."
            )

    def test_skill_md_persists_to_config_yaml(self):
        """D15: spec/SKILL.md documents writing build_type to config.yaml (SC-2).

        RED: SKILL.md doesn't mention config.yaml persistence.
        GREEN after: Phase 8 adds persistence documentation.
        """
        content = _read_spec_skill()
        assert "config.yaml" in content, (
            "spec/SKILL.md must document writing build_type to config.yaml (SC-2)."
        )

    def test_skill_md_persists_to_project(self):
        """D16: spec/SKILL.md documents writing build_type to .project (SC-2).

        RED: SKILL.md doesn't mention .project persistence.
        GREEN after: Phase 8 adds .project persistence documentation.
        """
        content = _read_spec_skill()
        assert ".project" in content, (
            "spec/SKILL.md must document writing build_type to .project (SC-2)."
        )

    def test_build_type_classifier_doc_exists(self):
        """D17: skills/spec/build-type-classifier.md helper doc exists (seed § Files).

        RED: build-type-classifier.md doesn't exist yet.
        GREEN after: Phase 8 creates it with keyword YAML block.
        """
        assert BUILD_TYPE_CLASSIFIER_DOC.exists(), (
            f"skills/spec/build-type-classifier.md not found at {BUILD_TYPE_CLASSIFIER_DOC}.\n"
            "Phase 8 must create this helper doc with heuristic keywords as a YAML block\n"
            "so the list can be updated by retro without modifying SKILL.md."
        )

    def test_build_type_classifier_doc_has_yaml_keywords(self):
        """D17b: build-type-classifier.md contains pipeline keywords as YAML block.

        RED: file doesn't exist.
        GREEN after: Phase 8 creates the file with keyword data.
        """
        assert BUILD_TYPE_CLASSIFIER_DOC.exists(), (
            f"build-type-classifier.md not found. Phase 8 must create it."
        )
        content = BUILD_TYPE_CLASSIFIER_DOC.read_text()
        # Must contain YAML-formatted keyword data
        assert "pipeline" in content.lower(), (
            "build-type-classifier.md must list pipeline keywords."
        )
        for kw in ["DAG", "Airflow", "bronze", "silver"]:
            assert kw in content, (
                f"build-type-classifier.md must include keyword '{kw}' in the pipeline list."
            )


# ---------------------------------------------------------------------------
# E: spec/codex.md Codex parity (SC-8)
# ---------------------------------------------------------------------------

class TestCodexParity:
    """E18–E20: codex.md must mirror the build_type flow (SC-8)."""

    def test_codex_md_has_build_type(self):
        """E18: codex.md documents build_type (SC-8).

        RED: codex.md has no build_type content.
        GREEN after: Phase 8 adds classifier flow to codex.md.
        """
        content = _read_codex()
        assert "build_type" in content, (
            "spec/codex.md must document the build_type classifier flow (SC-8).\n"
            "Codex executions must get the same classifier prompt as Claude."
        )

    def test_codex_md_has_five_value_enum(self):
        """E19: codex.md documents the same 5-value enum (SC-8).

        RED: codex.md has no enum.
        GREEN after: Phase 8 ensures enum parity between SKILL.md and codex.md.
        """
        content = _read_codex()
        for value in ["pipeline", "feature", "bug", "ops", "infra"]:
            assert value in content, (
                f"codex.md must document build_type value '{value}' (SC-8 parity)."
            )

    def test_codex_md_has_source_selection(self):
        """E20: codex.md documents the source-selection sub-prompt (SC-8).

        RED: codex.md has no source selection.
        GREEN after: Phase 8 adds source sub-prompt to codex.md.
        """
        content = _read_codex()
        assert "source" in content.lower(), (
            "codex.md must document the source-selection sub-prompt for pipeline builds (SC-8)."
        )
        assert "amazon-sp-api" in content or "walmart-supplier" in content, (
            "codex.md must list source enum values (SC-8 parity with SKILL.md source prompt)."
        )
