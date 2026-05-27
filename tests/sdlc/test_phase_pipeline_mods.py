"""STORY-1002: phase-6/7/10 pipeline-aware modifications — Phase 7 RED tests.

Tests verify SKILL.md files have the pipeline branches and templates exist.

  E (E-01..E-06): Phase 6 pipeline-aware branch + pipeline-design-validation template
  F (F-01..F-04): Phase 7 branched MANDATORY CHECK (contract-test gate)
  G (G-01..G-06): Phase 10 pipeline variant + pipeline-site-reliability template

RED reasons:
  Most tests reference existing SKILL.md content that was written as part of
  STORY-1002 Phase 6 modifications. Tests that are RED indicate missing sections.
  Run this file first to confirm which assertions need Phase 8 fixes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SDLC = REPO_ROOT / ".sdlc"
PHASE6_SKILL = SDLC / "skills" / "phase-6" / "SKILL.md"
PHASE7_SKILL = SDLC / "skills" / "phase-7" / "SKILL.md"
PHASE10_SKILL = SDLC / "skills" / "phase-10" / "SKILL.md"
PDV_TEMPLATE = SDLC / "templates" / "pipeline-design-validation.md"
PSR_TEMPLATE = SDLC / "templates" / "pipeline-site-reliability-additions.md"


def _read(path: Path) -> str:
    assert path.exists(), f"Required file not found: {path}"
    return path.read_text()


# ---------------------------------------------------------------------------
# E: Phase 6 pipeline-aware branch
# ---------------------------------------------------------------------------


class TestPhase6PipelineMod:
    """Verifies phase-6/SKILL.md has the pipeline-aware branch per STORY-1002."""

    def test_phase6_has_pipeline_branch_section(self):  # E-01
        """phase-6/SKILL.md must have a pipeline-aware branch section."""
        content = _read(PHASE6_SKILL)
        assert "Pipeline-Aware Branch" in content, (
            "phase-6/SKILL.md must contain 'Pipeline-Aware Branch' section. "
            "STORY-1002 Phase 6 must add this."
        )

    def test_phase6_has_load_canon_step(self):  # E-02
        """phase-6/SKILL.md must invoke /load-canon --phase=6."""
        content = _read(PHASE6_SKILL)
        assert "/load-canon --phase=6" in content, (
            "phase-6/SKILL.md pipeline branch must invoke '/load-canon --phase=6'"
        )

    def test_phase6_has_14_axis_validation_step(self):  # E-03
        """phase-6/SKILL.md must reference 14-axis validation producing pipeline-design-validation.md."""
        content = _read(PHASE6_SKILL)
        assert "14" in content and "pipeline-design-validation.md" in content, (
            "phase-6/SKILL.md must reference 14-axis validation and pipeline-design-validation.md"
        )

    def test_phase6_advance_gate_blocks_on_fail(self):  # E-04
        """phase-6/SKILL.md must define the advance gate that blocks on fail axis."""
        content = _read(PHASE6_SKILL)
        assert "cannot advance" in content, (
            "phase-6/SKILL.md must contain 'cannot advance' message for fail-axis gate"
        )
        assert "deviation" in content.lower(), (
            "phase-6/SKILL.md must reference 'deviation' approval requirement"
        )

    def test_pipeline_design_validation_template_exists(self):  # E-05
        """templates/pipeline-design-validation.md must exist."""
        assert PDV_TEMPLATE.exists(), (
            f"Template not found at {PDV_TEMPLATE}. STORY-1002 Phase 6 must create it."
        )

    def test_pipeline_design_validation_template_has_14_axes(self):  # E-06
        """pipeline-design-validation.md template must contain ## Axis 1 through ## Axis 14."""
        content = _read(PDV_TEMPLATE)
        for i in range(1, 15):
            assert f"## Axis {i}" in content, (
                f"pipeline-design-validation.md must contain '## Axis {i}' section"
            )


# ---------------------------------------------------------------------------
# F: Phase 7 branched MANDATORY CHECK
# ---------------------------------------------------------------------------


class TestPhase7PipelineMod:
    """Verifies phase-7/SKILL.md has the branched contract-test requirement."""

    def test_phase7_has_branched_mandatory_check(self):  # F-01
        """phase-7/SKILL.md must branch by build_type == pipeline."""
        content = _read(PHASE7_SKILL)
        assert "build_type == pipeline" in content, (
            "phase-7/SKILL.md must contain 'build_type == pipeline' branch condition"
        )

    def test_phase7_requires_contract_test_entities_yaml(self):  # F-02
        """phase-7/SKILL.md must require test_entities_yaml.py for pipeline builds."""
        content = _read(PHASE7_SKILL)
        assert "test_entities_yaml.py" in content, (
            "phase-7/SKILL.md must reference 'test_entities_yaml.py' as required contract test"
        )

    def test_phase7_requires_contract_test_conformance(self):  # F-03
        """phase-7/SKILL.md must require test_conformance.py for pipeline builds."""
        content = _read(PHASE7_SKILL)
        assert "test_conformance.py" in content, (
            "phase-7/SKILL.md must reference 'test_conformance.py' as required contract test"
        )

    def test_phase7_non_pipeline_gate_2a_preserved(self):  # F-04
        """phase-7/SKILL.md must preserve Gate 2a for non-pipeline builds (SC-9 regression)."""
        content = _read(PHASE7_SKILL)
        assert "Gate 2a" in content, (
            "phase-7/SKILL.md must preserve 'Gate 2a' for non-pipeline builds. "
            "SC-9: non-pipeline behaviour must be unchanged."
        )


# ---------------------------------------------------------------------------
# G: Phase 10 pipeline variant
# ---------------------------------------------------------------------------


class TestPhase10PipelineMod:
    """Verifies phase-10/SKILL.md has the pipeline variant section."""

    def test_phase10_has_pipeline_variant_section(self):  # G-01
        """phase-10/SKILL.md must have a Pipeline Variant section."""
        content = _read(PHASE10_SKILL)
        assert "Pipeline Variant" in content, (
            "phase-10/SKILL.md must contain 'Pipeline Variant' section. "
            "STORY-1002 Phase 6 must add this."
        )

    def test_phase10_airflow_dag_observability(self):  # G-02
        """phase-10/SKILL.md pipeline variant must reference Airflow DAG observability."""
        content = _read(PHASE10_SKILL)
        assert "Airflow" in content, (
            "phase-10/SKILL.md pipeline variant must reference Airflow"
        )
        assert "DAG" in content, (
            "phase-10/SKILL.md pipeline variant must reference DAG observability"
        )

    def test_phase10_failure_modes_crosslink(self):  # G-03
        """phase-10/SKILL.md must require failure-modes.md cross-links for every alert."""
        content = _read(PHASE10_SKILL)
        assert "failure-modes.md" in content, (
            "phase-10/SKILL.md must require cross-links to failure-modes.md"
        )

    def test_phase10_deploy_gates_requirement(self):  # G-04
        """phase-10/SKILL.md must reference failure-modes.md Deploy gates 1-5."""
        content = _read(PHASE10_SKILL)
        assert "Deploy gates 1-5" in content or "gates 1-5" in content, (
            "phase-10/SKILL.md must reference 'Deploy gates 1-5' from failure-modes.md"
        )

    def test_pipeline_site_reliability_template_exists(self):  # G-05
        """templates/pipeline-site-reliability-additions.md must exist."""
        assert PSR_TEMPLATE.exists(), (
            f"Template not found at {PSR_TEMPLATE}. STORY-1002 Phase 6 must create it."
        )

    def test_pipeline_site_reliability_template_airflow_section(self):  # G-06
        """pipeline-site-reliability-additions.md must have ## Airflow DAG Observability."""
        content = _read(PSR_TEMPLATE)
        assert "## Airflow DAG Observability" in content, (
            "pipeline-site-reliability-additions.md must contain '## Airflow DAG Observability' section"
        )
        # Must have at least 4 DAG-specific SLI rows (SC-8)
        sli_lines = [line for line in content.splitlines() if "DAG" in line and "|" in line]
        assert len(sli_lines) >= 4, (
            f"Template must have at least 4 DAG-specific SLI rows (SC-8), found {len(sli_lines)}: "
            f"{sli_lines}"
        )
