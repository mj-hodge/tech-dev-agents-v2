"""STORY-1003: REPLAY-4 — retro with platform findings emits dual proposals.

SC-9: End-to-end scenario validation using golden fixture files:
      - A retro with pipeline/dbt findings must emit BOTH retro-proposal.yaml
        AND retro-proposal-gc-data-v2.yaml (dual-proposal path).
      - A retro with framework-only findings must emit ONLY retro-proposal.yaml
        (single-proposal regression guard).

  A (SC-9 REPLAY-4):     Pipeline fixture validates as a gc-data-v2 proposal
  B (SC-9 regression):   Framework-only fixture validates as a single proposal
  C (SC-9 schema):       retro/SKILL.md gc-data-v2 schema example is consistent
                         with the retro-proposal-gc-data-v2.yaml template

RED reasons (all tests fail until Phase 8):
  - tests/fixtures/sdlc/retro-proposal.pipeline-finding.yaml doesn't exist yet
  - tests/fixtures/sdlc/retro-proposal.framework-only.yaml doesn't exist yet
  - .sdlc/skills/retro/SKILL.md has no dual-proposal logic (SC-7)
  - .sdlc/templates/retro-proposal-gc-data-v2.yaml does not exist yet
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sdlc"
PIPELINE_FIXTURE = FIXTURES_DIR / "retro-proposal.pipeline-finding.yaml"
FRAMEWORK_FIXTURE = FIXTURES_DIR / "retro-proposal.framework-only.yaml"
RETRO_SKILL_MD = REPO_ROOT / ".sdlc" / "skills" / "retro" / "SKILL.md"
GC_DATA_V2_TEMPLATE = REPO_ROOT / ".sdlc" / "templates" / "retro-proposal-gc-data-v2.yaml"

# Platform keyword regex patterns (mirrors retro SKILL.md SC-7 spec)
PLATFORM_KEYWORDS = ["pipeline", "airflow", "dbt", "canon"]


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return parsed content. Fails test if file doesn't exist."""
    assert path.exists(), (
        f"Fixture not found at {path}. "
        "Phase 8 must create this fixture file."
    )
    with path.open() as f:
        return yaml.safe_load(f)


def _has_platform_keyword(text: str) -> bool:
    """Return True if text contains any platform keyword (case-insensitive)."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in PLATFORM_KEYWORDS)


# ---------------------------------------------------------------------------
# A: SC-9 REPLAY-4 — Pipeline fixture validates as gc-data-v2 proposal
# ---------------------------------------------------------------------------


class TestReplay4PipelineFixture:
    """A01–A06: Pipeline finding fixture is a valid gc-data-v2 dual-proposal."""

    def test_pipeline_fixture_exists(self):
        """A01: tests/fixtures/sdlc/retro-proposal.pipeline-finding.yaml must exist (SC-9).

        RED: Fixture has not been created yet.
        GREEN after: Phase 7 creates the golden fixture file.
        """
        assert PIPELINE_FIXTURE.exists(), (
            f"Pipeline finding fixture not found at {PIPELINE_FIXTURE}. "
            "This fixture is the golden output for the REPLAY-4 dual-proposal scenario (SC-9)."
        )

    def test_pipeline_fixture_has_target_repo_gc_data_v2(self):
        """A02: Pipeline fixture has 'target_repo: gc-data-v2' at top level (SC-9).

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates fixture with correct target_repo.
        """
        data = _load_yaml(PIPELINE_FIXTURE)
        assert data.get("target_repo") == "gc-data-v2", (
            f"Pipeline fixture must have 'target_repo: gc-data-v2' at top level (SC-9). "
            f"Got: {data.get('target_repo')!r}"
        )

    def test_pipeline_fixture_has_version_field(self):
        """A03: Pipeline fixture has a 'version:' field (SC-9).

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates fixture with version field.
        """
        data = _load_yaml(PIPELINE_FIXTURE)
        assert "version" in data, (
            "Pipeline fixture must have a 'version:' field (SC-9). "
            "This ensures schema versioning is consistent with retro-proposal.yaml."
        )

    def test_pipeline_fixture_has_proposals_list(self):
        """A04: Pipeline fixture has a 'proposals:' list with at least one entry (SC-9).

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates fixture with at least one proposal.
        """
        data = _load_yaml(PIPELINE_FIXTURE)
        assert "proposals" in data, (
            "Pipeline fixture must have a 'proposals:' key (SC-9)."
        )
        proposals = data["proposals"]
        assert isinstance(proposals, list) and len(proposals) >= 1, (
            f"Pipeline fixture 'proposals' must be a list with at least one entry (SC-9). "
            f"Got: {proposals!r}"
        )

    def test_pipeline_fixture_every_proposal_has_target_repo_gc_data_v2(self):
        """A05: Every proposal in the pipeline fixture has target_repo: gc-data-v2 (SC-9).

        All proposals in a gc-data-v2 file must carry the target_repo field at
        the proposal level — not just at the top level — so the framework owner
        can route them correctly.

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates fixture with per-proposal target_repo fields.
        """
        data = _load_yaml(PIPELINE_FIXTURE)
        proposals = data.get("proposals", [])
        assert len(proposals) >= 1, (
            "Pipeline fixture must have at least one proposal (SC-9)."
        )
        for i, proposal in enumerate(proposals):
            assert proposal.get("target_repo") == "gc-data-v2", (
                f"Pipeline fixture proposal[{i}] must have 'target_repo: gc-data-v2' (SC-9). "
                f"Got: {proposal.get('target_repo')!r}"
            )

    def test_pipeline_fixture_every_proposal_has_platform_keyword_category(self):
        """A06: Every proposal category matches a platform keyword (SC-9).

        In the REPLAY-4 fixture all proposals are platform-related, so every
        category must contain at least one of: pipeline, airflow, dbt, canon.

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates fixture with correct category values.
        """
        data = _load_yaml(PIPELINE_FIXTURE)
        proposals = data.get("proposals", [])
        assert len(proposals) >= 1, (
            "Pipeline fixture must have at least one proposal (SC-9)."
        )
        for i, proposal in enumerate(proposals):
            category = proposal.get("category", "")
            assert _has_platform_keyword(category), (
                f"Pipeline fixture proposal[{i}] category '{category}' must contain a "
                f"platform keyword ({PLATFORM_KEYWORDS}) (SC-9)."
            )


# ---------------------------------------------------------------------------
# B: SC-9 regression — Framework-only fixture validates as single proposal
# ---------------------------------------------------------------------------


class TestReplay4FrameworkOnlyFixture:
    """B07–B11: Framework-only fixture validates as a single retro-proposal (no gc-data-v2)."""

    def test_framework_fixture_exists(self):
        """B07: tests/fixtures/sdlc/retro-proposal.framework-only.yaml must exist (SC-9).

        RED: Fixture has not been created yet.
        GREEN after: Phase 7 creates the golden regression fixture file.
        """
        assert FRAMEWORK_FIXTURE.exists(), (
            f"Framework-only fixture not found at {FRAMEWORK_FIXTURE}. "
            "This fixture is the golden output for the SC-8 single-proposal regression (SC-9)."
        )

    def test_framework_fixture_has_no_top_level_target_repo(self):
        """B08: Framework fixture does NOT have 'target_repo: gc-data-v2' at top level (SC-9).

        A framework-only retro produces a single retro-proposal.yaml with no
        gc-data-v2 targeting. The top-level 'target_repo' key must be absent.

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates framework fixture without target_repo.
        """
        data = _load_yaml(FRAMEWORK_FIXTURE)
        top_level_target_repo = data.get("target_repo")
        assert top_level_target_repo != "gc-data-v2", (
            "Framework-only fixture must NOT have 'target_repo: gc-data-v2' at top level (SC-9). "
            f"Got: {top_level_target_repo!r}. "
            "This fixture represents a single-proposal retro — no gc-data-v2 output."
        )

    def test_framework_fixture_proposals_have_no_platform_keyword_categories(self):
        """B09: Framework fixture proposals do NOT have platform keyword categories (SC-9).

        In a framework-only retro all findings relate to SDLC phases (e.g., 'Phase 7',
        'Phase 8') — not to data platform topics.

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates framework fixture with non-platform categories.
        """
        data = _load_yaml(FRAMEWORK_FIXTURE)
        proposals = data.get("proposals", [])
        assert len(proposals) >= 1, (
            "Framework-only fixture must have at least one proposal (SC-9)."
        )
        for i, proposal in enumerate(proposals):
            category = proposal.get("category", "")
            assert not _has_platform_keyword(category), (
                f"Framework-only fixture proposal[{i}] category '{category}' must NOT "
                f"contain a platform keyword ({PLATFORM_KEYWORDS}) (SC-9). "
                "Use Phase-level categories like 'Phase 7' or 'Phase 8'."
            )

    def test_framework_fixture_has_version_field(self):
        """B10: Framework fixture has a 'version:' field (SC-9).

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates framework fixture with version field.
        """
        data = _load_yaml(FRAMEWORK_FIXTURE)
        assert "version" in data, (
            "Framework-only fixture must have a 'version:' field (SC-9)."
        )

    def test_framework_fixture_has_proposals_list(self):
        """B11: Framework fixture has a non-empty 'proposals:' list (SC-9).

        RED: Fixture does not exist yet.
        GREEN after: Phase 7 creates framework fixture with at least one proposal.
        """
        data = _load_yaml(FRAMEWORK_FIXTURE)
        assert "proposals" in data, (
            "Framework-only fixture must have a 'proposals:' key (SC-9)."
        )
        proposals = data["proposals"]
        assert isinstance(proposals, list) and len(proposals) >= 1, (
            f"Framework-only fixture 'proposals' must be a non-empty list (SC-9). "
            f"Got: {proposals!r}"
        )


# ---------------------------------------------------------------------------
# C: SC-9 schema — SKILL.md gc-data-v2 schema is consistent with template
# ---------------------------------------------------------------------------


class TestReplay4SchemaConsistency:
    """C12–C14: retro/SKILL.md gc-data-v2 schema example matches the template shape."""

    def test_retro_skill_md_schema_example_has_target_repo(self):
        """C12: retro/SKILL.md schema example for gc-data-v2 uses target_repo: gc-data-v2 (SC-9).

        The SKILL.md documentation must show a schema example that includes
        target_repo: gc-data-v2 so agents know what the second file looks like.

        RED: retro/SKILL.md has no dual-proposal documentation yet.
        GREEN after: Phase 8 adds gc-data-v2 schema example to SKILL.md.
        """
        assert RETRO_SKILL_MD.exists(), (
            f"retro/SKILL.md not found at {RETRO_SKILL_MD}. "
            "The skill file must exist — Phase 8 adds dual-proposal logic to it."
        )
        content = RETRO_SKILL_MD.read_text()
        assert "target_repo: gc-data-v2" in content, (
            "retro/SKILL.md must contain a schema example with 'target_repo: gc-data-v2' (SC-9). "
            "Agents need to see the expected shape of the second proposal file."
        )

    def test_gc_data_v2_template_has_same_base_shape_as_retro_proposal(self):
        """C13: retro-proposal-gc-data-v2.yaml template has same base shape as retro-proposal.yaml (SC-9).

        The gc-data-v2 template is an extension of the standard retro-proposal —
        it must have version, proposals, project, epic, date fields, plus target_repo.

        RED: Template does not exist yet.
        GREEN after: Phase 8 creates template with correct shape.
        """
        assert GC_DATA_V2_TEMPLATE.exists(), (
            f"retro-proposal-gc-data-v2.yaml not found at {GC_DATA_V2_TEMPLATE}. "
            "Phase 8 must create this template (SC-7)."
        )
        data = _load_yaml(GC_DATA_V2_TEMPLATE)
        required_fields = ["version", "proposals", "target_repo"]
        for field in required_fields:
            assert field in data, (
                f"retro-proposal-gc-data-v2.yaml template must contain '{field}' field (SC-9). "
                f"Present fields: {list(data.keys())}"
            )

    def test_gc_data_v2_template_target_repo_value_is_gc_data_v2(self):
        """C14: retro-proposal-gc-data-v2.yaml template target_repo is exactly 'gc-data-v2' (SC-9).

        RED: Template does not exist yet.
        GREEN after: Phase 8 creates template with correct target_repo value.
        """
        assert GC_DATA_V2_TEMPLATE.exists(), (
            f"retro-proposal-gc-data-v2.yaml not found at {GC_DATA_V2_TEMPLATE}. "
            "Phase 8 must create this template (SC-7)."
        )
        data = _load_yaml(GC_DATA_V2_TEMPLATE)
        assert data.get("target_repo") == "gc-data-v2", (
            f"retro-proposal-gc-data-v2.yaml template must have target_repo: gc-data-v2 (SC-9). "
            f"Got: {data.get('target_repo')!r}"
        )
