"""STORY-1002: load-canon skill — Phase 7 RED tests (test_load_canon_skill).

Tests verify the load-canon SKILL.md structure and the canon_helpers Python module.

  A (A-01..A-05): SKILL.md structure — frontmatter, required sections, phase-slices
  B (B-01..B-03): canon_helpers — load_pipeline_standard(), compute_canon_hash()
  C (C-01..C-03): canon_helpers — .project state write/read/idempotency
  D (D-01..D-03): canon_helpers — error handling + is_canon_loaded()

RED reasons:
  - B/C/D: tech_dev_agents.sdlc.canon_helpers module does not exist yet (Phase 8)
  - A-03: SKILL.md missing '## Idempotency' section (Phase 8 adds it)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / ".sdlc" / "skills" / "load-canon"
SKILL_MD = SKILL_DIR / "SKILL.md"
PHASE_SLICES_MD = SKILL_DIR / "phase-slices.md"
FIXTURE_PIPELINE_STANDARD = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "sdlc"
    / "gc-data-v2-snapshot"
    / "platform"
    / "pipeline-standard.md"
)


def _read_skill() -> str:
    assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"
    return SKILL_MD.read_text()


def _read_phase_slices() -> str:
    assert PHASE_SLICES_MD.exists(), f"phase-slices.md not found at {PHASE_SLICES_MD}"
    return PHASE_SLICES_MD.read_text()


# ---------------------------------------------------------------------------
# A: SKILL.md structure
# ---------------------------------------------------------------------------


class TestLoadCanonSkillMd:
    """Verifies load-canon SKILL.md has correct structure per spec."""

    def test_skill_md_exists(self):  # A-01
        """SKILL.md must exist at .sdlc/skills/load-canon/SKILL.md."""
        assert SKILL_MD.exists(), f"SKILL.md not found at {SKILL_MD}"

    def test_skill_md_has_yaml_frontmatter(self):  # A-02
        """SKILL.md must start with YAML frontmatter containing name and description."""
        content = _read_skill()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter (---)"
        end = content.index("---", 3)
        frontmatter = content[3:end]
        assert "name: load-canon" in frontmatter, "Frontmatter must contain 'name: load-canon'"
        assert "description:" in frontmatter, "Frontmatter must contain 'description:'"

    def test_skill_md_has_required_sections(self):  # A-03
        """SKILL.md must have Workflow, Outputs, Error Handling, and Idempotency sections."""
        content = _read_skill()
        assert "## Workflow" in content, "SKILL.md must have '## Workflow' section"
        assert "## Outputs" in content, "SKILL.md must have '## Outputs' section"
        assert "## Error Handling" in content, "SKILL.md must have '## Error Handling' section"
        # NOTE: This assertion is RED until Phase 8 adds the section
        assert "## Idempotency" in content, (
            "SKILL.md must have '## Idempotency' section. "
            "Phase 8 must add this section to make the test GREEN."
        )

    def test_phase_slices_md_exists(self):  # A-04
        """phase-slices.md must exist alongside SKILL.md."""
        assert PHASE_SLICES_MD.exists(), f"phase-slices.md not found at {PHASE_SLICES_MD}"

    def test_phase_slices_has_all_phases(self):  # A-05
        """phase-slices.md must document slices for phases 1, 6, 7, and 10."""
        content = _read_phase_slices()
        for phase in ["**1**", "**6**", "**7**", "**10**"]:
            assert phase in content, f"phase-slices.md must document slice for phase {phase}"


# ---------------------------------------------------------------------------
# B: canon_helpers module — parsing and hashing
# ---------------------------------------------------------------------------


class TestCanonHelpersPipelineLoading:
    """Tests for canon_helpers.load_pipeline_standard() and compute_canon_hash()."""

    def test_canon_helpers_module_exists(self):  # B-01
        """tech_dev_agents.sdlc.canon_helpers must be importable."""
        try:
            from tech_dev_agents.sdlc import canon_helpers  # noqa: F401
        except ImportError as exc:
            pytest.fail(
                f"tech_dev_agents.sdlc.canon_helpers not importable: {exc}. "
                "Phase 8 must create this module at tech_dev_agents/sdlc/canon_helpers.py"
            )

    def test_load_pipeline_standard_parses_axes(self):  # B-02
        """load_pipeline_standard() must parse 14 axes from the fixture pipeline-standard.md."""
        from tech_dev_agents.sdlc.canon_helpers import load_pipeline_standard

        assert FIXTURE_PIPELINE_STANDARD.exists(), (
            f"Fixture pipeline-standard.md not found at {FIXTURE_PIPELINE_STANDARD}. "
            "Phase 8 must create tests/fixtures/sdlc/gc-data-v2-snapshot/platform/pipeline-standard.md"
        )
        axes = load_pipeline_standard(str(FIXTURE_PIPELINE_STANDARD))
        assert isinstance(axes, dict), f"Expected dict, got {type(axes)}"
        assert len(axes) == 14, (
            f"Expected exactly 14 axes, found {len(axes)}. "
            f"Axes found: {list(axes.keys())}"
        )
        # Verify the 14 canonical axis names from STORY-1002 seed.md
        expected_axes = [
            "Repo Structure",
            "Layers",
            "Ingestion Patterns",
            "Materialiser",
            "Contracts",
            "DAG Shape",
            "Auth",
            "Observability",
            "dbt",
            "CI",
            "Deploy",
            "Runbook",
            "SLOs",
            "Security",
        ]
        for name in expected_axes:
            assert name in axes, (
                f"Missing axis '{name}' from load_pipeline_standard() output. "
                f"Found axes: {list(axes.keys())}"
            )

    def test_compute_canon_hash_format(self):  # B-03
        """compute_canon_hash() must return a 12-character hex string."""
        from tech_dev_agents.sdlc.canon_helpers import compute_canon_hash

        assert FIXTURE_PIPELINE_STANDARD.exists(), (
            f"Fixture not found at {FIXTURE_PIPELINE_STANDARD}. Phase 8 must create it."
        )
        hash_val = compute_canon_hash(str(FIXTURE_PIPELINE_STANDARD))
        assert isinstance(hash_val, str), f"Expected str, got {type(hash_val)}"
        assert len(hash_val) == 12, (
            f"Expected 12-char hash, got {len(hash_val)} chars: '{hash_val}'"
        )
        # Must be valid lowercase hex
        assert all(c in "0123456789abcdef" for c in hash_val), (
            f"Hash must be lowercase hex, got: '{hash_val}'"
        )


# ---------------------------------------------------------------------------
# C: .project state management
# ---------------------------------------------------------------------------


class TestProjectStateManagement:
    """Tests for canon_helpers read/write of .project state."""

    _SAMPLE_PROJECT = (
        "# Project State\n\n"
        "## Phase Routing\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Current Phase | 6 |\n\n"
        "## Project Overview\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Mode | feature_update |\n"
    )

    def test_write_canon_state_adds_section(self):  # C-01
        """write_canon_state() must insert a ## Canon State table into .project content."""
        from tech_dev_agents.sdlc.canon_helpers import write_canon_state

        state = {
            "canon_loaded": "true",
            "canon_version": "abc123def456",
            "canon_loaded_at": "2026-05-19T00:00:00Z",
            "canon_source": "gc-data-v2/platform/pipeline-standard.md",
            "canon_axes_count": "14",
        }
        result = write_canon_state(self._SAMPLE_PROJECT, state)

        assert "## Canon State" in result, "Must insert '## Canon State' section"
        assert "canon_loaded" in result, "Must include 'canon_loaded' row"
        assert "abc123def456" in result, "Must include canon_version value"

        # Section order: Phase Routing < Canon State < Project Overview
        routing_pos = result.index("## Phase Routing")
        canon_pos = result.index("## Canon State")
        overview_pos = result.index("## Project Overview")
        assert routing_pos < canon_pos < overview_pos, (
            "Canon State must appear between Phase Routing and Project Overview"
        )

    def test_read_canon_state_extracts_fields(self):  # C-02
        """read_canon_state() must extract all key/value rows from ## Canon State table."""
        from tech_dev_agents.sdlc.canon_helpers import read_canon_state

        content = (
            "# Project State\n\n"
            "## Canon State\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| canon_loaded | true |\n"
            "| canon_version | abc123def456 |\n"
            "| canon_loaded_at | 2026-05-19T00:00:00Z |\n"
            "| canon_source | gc-data-v2/platform/pipeline-standard.md |\n"
            "| canon_axes_count | 14 |\n\n"
            "## Project Overview\n"
        )
        state = read_canon_state(content)
        assert state.get("canon_loaded") == "true", f"Expected 'true', got {state.get('canon_loaded')!r}"
        assert state.get("canon_version") == "abc123def456"
        assert state.get("canon_source") == "gc-data-v2/platform/pipeline-standard.md"
        assert state.get("canon_axes_count") == "14"

    def test_write_canon_state_idempotent(self):  # C-03
        """write_canon_state() called twice must not create duplicate ## Canon State sections."""
        from tech_dev_agents.sdlc.canon_helpers import write_canon_state

        state_v1 = {
            "canon_loaded": "true",
            "canon_version": "abc123def456",
            "canon_loaded_at": "2026-05-19T00:00:00Z",
            "canon_source": "gc-data-v2/platform/pipeline-standard.md",
            "canon_axes_count": "14",
        }
        state_v2 = dict(state_v1)
        state_v2["canon_loaded_at"] = "2026-05-19T12:00:00Z"
        state_v2["canon_version"] = "def456abc789"

        result_1 = write_canon_state(self._SAMPLE_PROJECT, state_v1)
        result_2 = write_canon_state(result_1, state_v2)

        assert result_2.count("## Canon State") == 1, (
            f"Must have exactly one '## Canon State' section, found {result_2.count('## Canon State')}"
        )
        assert "2026-05-19T12:00:00Z" in result_2, "Must contain updated timestamp"
        assert "2026-05-19T00:00:00Z" not in result_2, "Old timestamp must be replaced"
        assert "def456abc789" in result_2, "Must contain updated canon_version"
        assert "abc123def456" not in result_2, "Old canon_version must be replaced"


# ---------------------------------------------------------------------------
# D: Error handling + is_canon_loaded
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error conditions and gating helpers."""

    def test_load_pipeline_standard_missing_file(self):  # D-01
        """load_pipeline_standard() with a nonexistent path must raise FileNotFoundError."""
        from tech_dev_agents.sdlc.canon_helpers import load_pipeline_standard

        with pytest.raises(FileNotFoundError):
            load_pipeline_standard("/nonexistent/path/pipeline-standard.md")

    def test_load_pipeline_standard_malformed_content(self):  # D-02
        """load_pipeline_standard() with content missing 14 axes must raise ValueError."""
        from tech_dev_agents.sdlc.canon_helpers import load_pipeline_standard

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Pipeline Standard\n\nNo axis headings here.\n")
            tmp_path = f.name

        with pytest.raises(ValueError, match="14 axes"):
            load_pipeline_standard(tmp_path)

    def test_is_canon_loaded(self):  # D-03
        """is_canon_loaded() must return correct boolean based on state dict."""
        from tech_dev_agents.sdlc.canon_helpers import is_canon_loaded

        assert is_canon_loaded({"canon_loaded": "true"}) is True
        assert is_canon_loaded({"canon_loaded": "false"}) is False
        assert is_canon_loaded({}) is False
        assert is_canon_loaded({"canon_loaded": "True"}) is False  # case-sensitive
