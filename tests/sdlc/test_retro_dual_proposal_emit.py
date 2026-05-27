"""STORY-1003: retro dual-proposal emit — Phase 7 RED tests.

SC-7: retro/SKILL.md documents that when a retro finds platform-keyword findings
      (pipeline, airflow, dbt, canon) it emits a SECOND proposal file targeted
      at gc-data-v2 (retro-proposal-gc-data-v2.yaml).

SC-8: retro/SKILL.md explicitly states the dual-proposal path is conditional —
      when NO platform keywords match, only the standard retro-proposal.yaml
      is emitted (single-proposal regression guard).

  A (SC-7): retro/SKILL.md documents retro-proposal-gc-data-v2.yaml output
  B (SC-7): SKILL.md documents target_repo: gc-data-v2
  C (SC-7): SKILL.md documents the platform-keyword regex
  D (SC-7): SKILL.md describes emitting a second file when keywords match
  E (SC-8): SKILL.md explicitly states second file NOT emitted when no match
  F (SC-8): SKILL.md uses conditional language for dual-proposal trigger
  G:         retro-proposal-gc-data-v2.yaml template exists with required fields

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/retro/SKILL.md has no dual-proposal logic (SC-7, SC-8)
  - .sdlc/templates/retro-proposal-gc-data-v2.yaml does not exist (SC-7)
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RETRO_SKILL_MD = REPO_ROOT / ".sdlc" / "skills" / "retro" / "SKILL.md"
RETRO_GC_DATA_TEMPLATE = REPO_ROOT / ".sdlc" / "templates" / "retro-proposal-gc-data-v2.yaml"

# Platform keywords that trigger dual-proposal emit (SC-7)
PLATFORM_KEYWORDS = ["pipeline", "airflow", "dbt", "canon"]


def _read_retro_skill() -> str:
    """Read retro/SKILL.md content. Fails test if file doesn't exist."""
    assert RETRO_SKILL_MD.exists(), (
        f"retro/SKILL.md not found at {RETRO_SKILL_MD}. "
        "The skill file must exist — Phase 8 adds dual-proposal logic to it."
    )
    return RETRO_SKILL_MD.read_text()


def _read_gc_data_template() -> str:
    """Read retro-proposal-gc-data-v2.yaml content. Fails test if file doesn't exist."""
    assert RETRO_GC_DATA_TEMPLATE.exists(), (
        f"retro-proposal-gc-data-v2.yaml not found at {RETRO_GC_DATA_TEMPLATE}. "
        "Phase 8 must create .sdlc/templates/retro-proposal-gc-data-v2.yaml (SC-7)."
    )
    return RETRO_GC_DATA_TEMPLATE.read_text()


# ---------------------------------------------------------------------------
# A: retro-proposal-gc-data-v2.yaml output documented (SC-7)
# ---------------------------------------------------------------------------


class TestDualProposalOutputDocumented:
    """A01: SKILL.md documents retro-proposal-gc-data-v2.yaml as an output."""

    def test_skill_md_mentions_gc_data_v2_proposal_file(self):
        """A01: SKILL.md mentions 'retro-proposal-gc-data-v2.yaml' (SC-7).

        RED: retro/SKILL.md exists but has no dual-proposal documentation.
        GREEN after: Phase 8 adds dual-proposal emit logic to SKILL.md.
        """
        content = _read_retro_skill()
        assert "retro-proposal-gc-data-v2.yaml" in content, (
            "retro/SKILL.md must document 'retro-proposal-gc-data-v2.yaml' as a "
            "conditional output (SC-7). "
            "This file is emitted when platform-keyword findings are found."
        )


# ---------------------------------------------------------------------------
# B: target_repo: gc-data-v2 (SC-7)
# ---------------------------------------------------------------------------


class TestTargetRepoDocumented:
    """B02: SKILL.md documents target_repo: gc-data-v2 field."""

    def test_skill_md_documents_target_repo_gc_data_v2(self):
        """B02: SKILL.md mentions 'target_repo: gc-data-v2' (SC-7).

        The gc-data-v2 proposals use target_repo to signal the framework owner
        which downstream repo should receive the backport.

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds target_repo field documentation.
        """
        content = _read_retro_skill()
        assert "target_repo: gc-data-v2" in content, (
            "retro/SKILL.md must document 'target_repo: gc-data-v2' as the field "
            "that marks a proposal as targeting the gc-data-v2 repo (SC-7)."
        )


# ---------------------------------------------------------------------------
# C: Platform-keyword regex (SC-7)
# ---------------------------------------------------------------------------


class TestPlatformKeywordRegex:
    """C03–C06: SKILL.md documents the platform-keyword regex used for detection."""

    def test_skill_md_documents_pipeline_keyword(self):
        """C03: SKILL.md mentions 'pipeline' as a platform keyword (SC-7).

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds the platform-keyword regex to SKILL.md.
        """
        content = _read_retro_skill()
        assert "pipeline" in content, (
            "retro/SKILL.md must mention 'pipeline' as a platform detection keyword (SC-7)."
        )

    def test_skill_md_documents_airflow_keyword(self):
        """C04: SKILL.md mentions 'airflow' as a platform keyword (SC-7).

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds the platform-keyword regex to SKILL.md.
        """
        content = _read_retro_skill()
        assert "airflow" in content, (
            "retro/SKILL.md must mention 'airflow' as a platform detection keyword (SC-7)."
        )

    def test_skill_md_documents_dbt_keyword(self):
        """C05: SKILL.md mentions 'dbt' as a platform keyword (SC-7).

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds the platform-keyword regex to SKILL.md.
        """
        content = _read_retro_skill()
        assert "dbt" in content, (
            "retro/SKILL.md must mention 'dbt' as a platform detection keyword (SC-7)."
        )

    def test_skill_md_documents_canon_keyword(self):
        """C06: SKILL.md mentions 'canon' as a platform keyword (SC-7).

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds the platform-keyword regex to SKILL.md.
        """
        content = _read_retro_skill()
        assert "canon" in content, (
            "retro/SKILL.md must mention 'canon' as a platform detection keyword (SC-7)."
        )


# ---------------------------------------------------------------------------
# D: Second file emitted when keywords match (SC-7)
# ---------------------------------------------------------------------------


class TestSecondFileEmittedOnMatch:
    """D07: SKILL.md describes emitting a second file when platform keywords match."""

    def test_skill_md_describes_second_file_emit(self):
        """D07: SKILL.md describes emitting the gc-data-v2 file when keywords match (SC-7).

        The documentation must describe the trigger condition — not just list the
        file name. It must explain WHEN the second file is emitted.

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds the conditional emit logic description.
        """
        content = _read_retro_skill()
        content_lower = content.lower()
        # Must mention both the file and a conditional emit context
        has_gc_data_file = "retro-proposal-gc-data-v2.yaml" in content
        has_conditional_emit = (
            "when" in content_lower
            or "if" in content_lower
            or "emit" in content_lower
            or "second" in content_lower
            or "additional" in content_lower
        )
        assert has_gc_data_file and has_conditional_emit, (
            "retro/SKILL.md must describe emitting 'retro-proposal-gc-data-v2.yaml' "
            "conditionally when platform keywords match (SC-7). "
            f"has_gc_data_file={has_gc_data_file}, has_conditional_emit={has_conditional_emit}."
        )


# ---------------------------------------------------------------------------
# E: Second file NOT emitted when no keywords match (SC-8)
# ---------------------------------------------------------------------------


class TestSecondFileNotEmittedWithoutMatch:
    """E08: SKILL.md explicitly states the second file is not emitted without match."""

    def test_skill_md_states_no_second_file_without_platform_keywords(self):
        """E08: SKILL.md explicitly states second file is NOT emitted when no platform
        keywords match (SC-8).

        This is the regression guard: a standard (framework-only) retro must
        continue to emit exactly one proposal file.

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 adds explicit single-file negative case to SKILL.md.
        """
        content = _read_retro_skill()
        content_lower = content.lower()
        has_not_emitted = (
            "not emitted" in content_lower
            or "only one" in content_lower
            or "single proposal" in content_lower
            or "no platform" in content_lower
            or "not emit" in content_lower
        )
        assert has_not_emitted, (
            "retro/SKILL.md must explicitly state that the gc-data-v2 proposal file "
            "is NOT emitted when no platform keywords match (SC-8). "
            "Expected language: 'not emitted', 'only one', 'single proposal', or similar."
        )


# ---------------------------------------------------------------------------
# F: Conditional language for dual-proposal trigger (SC-8)
# ---------------------------------------------------------------------------


class TestConditionalLanguageForTrigger:
    """F09–F10: SKILL.md uses conditional language to describe dual-proposal trigger."""

    def test_skill_md_uses_when_or_if_for_trigger(self):
        """F09: SKILL.md uses 'when' or 'if any finding' language for the trigger (SC-8).

        The dual-proposal emit must be described as conditional, not as always-on.
        Using 'when' or 'if any finding' signals that the default is single-proposal.

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 uses explicit conditional language in SKILL.md.
        """
        content = _read_retro_skill()
        content_lower = content.lower()
        has_conditional = (
            "when" in content_lower
            or "if any finding" in content_lower
            or "if any" in content_lower
            or "only if" in content_lower
            or "only when" in content_lower
        )
        assert has_conditional, (
            "retro/SKILL.md must use conditional language ('when', 'if any finding', "
            "'only when', etc.) for the dual-proposal trigger (SC-8). "
            "The dual emit is not the default path."
        )

    def test_skill_md_describes_trigger_condition_near_gc_data_reference(self):
        """F10: Conditional language appears in the same context as gc-data-v2 (SC-8).

        The condition must be co-located with the gc-data-v2 file reference —
        not just mentioned somewhere else in the document.

        RED: retro/SKILL.md has no dual-proposal documentation.
        GREEN after: Phase 8 documents the trigger condition adjacent to the file name.
        """
        content = _read_retro_skill()
        # Find the index of the gc-data-v2 reference
        idx = content.find("retro-proposal-gc-data-v2.yaml")
        assert idx != -1, (
            "retro/SKILL.md must reference 'retro-proposal-gc-data-v2.yaml' (SC-7)."
        )
        # Check ±500 chars around the reference for conditional language
        window_start = max(0, idx - 500)
        window_end = min(len(content), idx + 500)
        window = content[window_start:window_end].lower()
        has_conditional_near_ref = (
            "when" in window
            or "if" in window
            or "only" in window
            or "platform" in window
        )
        assert has_conditional_near_ref, (
            "retro/SKILL.md must use conditional language near the "
            "'retro-proposal-gc-data-v2.yaml' reference (SC-8). "
            "The trigger condition ('when', 'if', 'only', 'platform') must appear "
            "within ±500 chars of the file name."
        )


# ---------------------------------------------------------------------------
# G: retro-proposal-gc-data-v2.yaml template (SC-7)
# ---------------------------------------------------------------------------


class TestGcDataV2Template:
    """G11–G14: .sdlc/templates/retro-proposal-gc-data-v2.yaml exists with required fields."""

    def test_template_exists(self):
        """G11: .sdlc/templates/retro-proposal-gc-data-v2.yaml must exist (SC-7).

        RED: Template has not been created yet.
        GREEN after: Phase 8 creates the gc-data-v2 retro proposal template.
        """
        assert RETRO_GC_DATA_TEMPLATE.exists(), (
            f"retro-proposal-gc-data-v2.yaml not found at {RETRO_GC_DATA_TEMPLATE}. "
            "Phase 8 must create this template (SC-7)."
        )

    def test_template_has_target_repo_gc_data_v2(self):
        """G12: template contains 'target_repo: gc-data-v2' (SC-7).

        RED: Template does not exist yet.
        GREEN after: Phase 8 creates template with target_repo field.
        """
        content = _read_gc_data_template()
        assert "target_repo: gc-data-v2" in content, (
            "retro-proposal-gc-data-v2.yaml template must contain 'target_repo: gc-data-v2' (SC-7)."
        )

    def test_template_has_version_field(self):
        """G13: template contains a 'version:' field (SC-7).

        RED: Template does not exist yet.
        GREEN after: Phase 8 creates template with version field.
        """
        content = _read_gc_data_template()
        assert "version:" in content, (
            "retro-proposal-gc-data-v2.yaml template must contain a 'version:' field (SC-7)."
        )

    def test_template_has_proposals_key(self):
        """G14: template contains a 'proposals:' key (SC-7).

        RED: Template does not exist yet.
        GREEN after: Phase 8 creates template with proposals list.
        """
        content = _read_gc_data_template()
        assert "proposals:" in content, (
            "retro-proposal-gc-data-v2.yaml template must contain a 'proposals:' key (SC-7). "
            "The template must have the same shape as retro-proposal.yaml but with target_repo."
        )
