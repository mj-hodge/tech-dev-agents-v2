"""STORY-1003: canon-backport + retro + phase-9 3-question gate — Phase 7 RED tests.

SC-1: canon-backport SKILL.md exists, has correct frontmatter, documents invocation
      syntax, --dry-run, --target, idempotency guard, DRAFT PR behaviour, and
      canon-backport-results.json output path.

SC-3: gap-detection.md exists and documents the results JSON schema
      (matched_canon_docs, gaps_found, prs_drafted) plus keyword-match heuristics
      and a match score / threshold.

  A (SC-1): SKILL.md structure — frontmatter, name, invocation docs
  B (SC-1): SKILL.md flags — --dry-run, --target documented
  C (SC-1): SKILL.md idempotency — gh pr list --search pattern, DRAFT PRs
  D (SC-1): SKILL.md output — canon-backport-results.json path documented
  E (SC-3): gap-detection.md exists with heuristics and threshold
  F (SC-3): results JSON schema documented in SKILL.md or gap-detection.md
  G:         required template / support files exist

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/canon-backport/ directory does not exist yet
  - SKILL.md, gap-detection.md, templates/*.md have not been written
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / ".sdlc" / "skills" / "canon-backport"
SKILL_MD = SKILL_DIR / "SKILL.md"
GAP_DETECTION_MD = SKILL_DIR / "gap-detection.md"
TEMPLATE_GC_DATA = SKILL_DIR / "templates" / "pr-body-gc-data-v2.md"
TEMPLATE_TECH_KB = SKILL_DIR / "templates" / "pr-body-tech-gc-knowledgebase.md"
THREE_QUESTION_GATE = REPO_ROOT / ".sdlc" / "templates" / "three-question-gate.md"
RETRO_PROPOSAL_GC_DATA_TEMPLATE = REPO_ROOT / ".sdlc" / "templates" / "retro-proposal-gc-data-v2.yaml"


def _read_skill() -> str:
    """Read SKILL.md content. Fails test if file doesn't exist."""
    assert SKILL_MD.exists(), (
        f"SKILL.md not found at {SKILL_MD}. "
        "Phase 8 must create .sdlc/skills/canon-backport/SKILL.md "
        "with frontmatter, invocation syntax, flags, and output documentation."
    )
    return SKILL_MD.read_text()


def _read_gap_detection() -> str:
    """Read gap-detection.md content. Fails test if file doesn't exist."""
    assert GAP_DETECTION_MD.exists(), (
        f"gap-detection.md not found at {GAP_DETECTION_MD}. "
        "Phase 8 must create .sdlc/skills/canon-backport/gap-detection.md "
        "documenting keyword heuristics, match scoring, and results schema."
    )
    return GAP_DETECTION_MD.read_text()


def _skill_or_gap(keyword: str) -> bool:
    """Return True if keyword appears in SKILL.md or gap-detection.md."""
    skill_text = SKILL_MD.read_text() if SKILL_MD.exists() else ""
    gap_text = GAP_DETECTION_MD.read_text() if GAP_DETECTION_MD.exists() else ""
    return keyword in skill_text or keyword in gap_text


# ---------------------------------------------------------------------------
# A: SKILL.md structure (SC-1)
# ---------------------------------------------------------------------------


class TestSkillMdStructure:
    """A01–A03: SKILL.md must exist with correct frontmatter and skill name."""

    def test_skill_md_exists(self):
        """A01: .sdlc/skills/canon-backport/SKILL.md must exist (SC-1).

        RED: canon-backport skill directory has not been created yet.
        GREEN after: Phase 8 creates .sdlc/skills/canon-backport/SKILL.md.
        """
        assert SKILL_MD.exists(), (
            f"SKILL.md not found at {SKILL_MD}. "
            "Phase 8 must create the canon-backport skill directory and SKILL.md."
        )

    def test_skill_md_has_valid_frontmatter(self):
        """A02: SKILL.md has YAML frontmatter starting with '---' (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 writes SKILL.md with YAML frontmatter block.
        """
        content = _read_skill()
        assert content.startswith("---"), (
            "SKILL.md must start with YAML frontmatter delimiter '---' (SC-1)"
        )

    def test_skill_md_has_name_field(self):
        """A03: SKILL.md frontmatter contains 'name: canon-backport' (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 sets the skill name in YAML frontmatter.
        """
        content = _read_skill()
        assert "name: canon-backport" in content, (
            "SKILL.md frontmatter must contain 'name: canon-backport' (SC-1). "
            "This is the canonical skill identifier."
        )

    def test_skill_md_documents_invocation_syntax(self):
        """A04: SKILL.md references /canon-backport invocation syntax (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents how the skill is invoked.
        """
        content = _read_skill()
        assert "/canon-backport" in content, (
            "SKILL.md must document '/canon-backport' invocation syntax (SC-1). "
            "Users need to know the slash-command that triggers the skill."
        )


# ---------------------------------------------------------------------------
# B: SKILL.md flags (SC-1)
# ---------------------------------------------------------------------------


class TestSkillMdFlags:
    """B05–B06: SKILL.md documents required flags."""

    def test_skill_md_documents_dry_run_flag(self):
        """B05: SKILL.md documents --dry-run flag (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 adds --dry-run flag documentation.
        """
        content = _read_skill()
        assert "--dry-run" in content, (
            "SKILL.md must document the '--dry-run' flag (SC-1). "
            "The flag enables preview mode without opening real PRs."
        )

    def test_skill_md_documents_target_parameter(self):
        """B06: SKILL.md documents --target parameter with gc-data-v2 / tech-gc-knowledgebase (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 adds --target parameter documentation with valid values.
        """
        content = _read_skill()
        assert "--target" in content, (
            "SKILL.md must document the '--target' parameter (SC-1). "
            "Supported values are gc-data-v2 and tech-gc-knowledgebase."
        )
        assert "gc-data-v2" in content, (
            "SKILL.md must list 'gc-data-v2' as a valid --target value (SC-1)."
        )
        assert "tech-gc-knowledgebase" in content, (
            "SKILL.md must list 'tech-gc-knowledgebase' as a valid --target value (SC-1)."
        )


# ---------------------------------------------------------------------------
# C: SKILL.md idempotency and DRAFT PRs (SC-1)
# ---------------------------------------------------------------------------


class TestSkillMdIdempotencyAndDraftPrs:
    """C07–C09: SKILL.md documents idempotency guard and DRAFT PR behaviour."""

    def test_skill_md_documents_gh_pr_list_search(self):
        """C07: SKILL.md documents 'gh pr list --search' for idempotency checking (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents the gh pr list --search idiom for deduplication.
        """
        content = _read_skill()
        assert "gh pr list --search" in content, (
            "SKILL.md must document 'gh pr list --search' for idempotency checking (SC-1). "
            "The skill must not open duplicate PRs on repeated invocations."
        )

    def test_skill_md_documents_draft_prs(self):
        """C08: SKILL.md documents that PRs are opened as DRAFT (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents the DRAFT PR behaviour.
        """
        content = _read_skill()
        content_lower = content.lower()
        has_draft = "draft" in content_lower or "--draft" in content
        assert has_draft, (
            "SKILL.md must document that PRs are opened as DRAFT (SC-1). "
            "Canon-backport PRs should not be auto-merged — they need human review."
        )


# ---------------------------------------------------------------------------
# D: SKILL.md output path (SC-1)
# ---------------------------------------------------------------------------


class TestSkillMdOutputPath:
    """D10: SKILL.md documents the canon-backport-results.json output path."""

    def test_skill_md_documents_results_json_path(self):
        """D10: SKILL.md documents 'canon-backport-results.json' output path (SC-1).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents where the results JSON is written.
        """
        content = _read_skill()
        assert "canon-backport-results.json" in content, (
            "SKILL.md must document 'canon-backport-results.json' as the output path (SC-1). "
            "Other skills (e.g., phase-9, retro) read this file to determine what was backported."
        )


# ---------------------------------------------------------------------------
# E: gap-detection.md heuristics (SC-3)
# ---------------------------------------------------------------------------


class TestGapDetectionMd:
    """E11–E13: gap-detection.md exists and documents keyword heuristics / threshold."""

    def test_gap_detection_md_exists(self):
        """E11: .sdlc/skills/canon-backport/gap-detection.md must exist (SC-3).

        RED: canon-backport skill directory has not been created yet.
        GREEN after: Phase 8 creates gap-detection.md alongside SKILL.md.
        """
        assert GAP_DETECTION_MD.exists(), (
            f"gap-detection.md not found at {GAP_DETECTION_MD}. "
            "Phase 8 must create this file with keyword heuristics and scoring docs."
        )

    def test_gap_detection_md_has_keyword_heuristics(self):
        """E12: gap-detection.md contains keyword match examples (SC-3).

        The heuristics section must reference at least one domain keyword
        used for matching (e.g., KV, slot, placeholder).

        RED: gap-detection.md does not exist yet.
        GREEN after: Phase 8 documents the heuristic matching examples.
        """
        content = _read_gap_detection()
        content_lower = content.lower()
        # At least one canonical heuristic keyword must appear as an example
        heuristic_examples = ["kv", "slot", "placeholder"]
        found = [kw for kw in heuristic_examples if kw in content_lower]
        assert len(found) >= 1, (
            f"gap-detection.md must document keyword match heuristics with examples "
            f"(e.g., 'KV', 'slot', 'placeholder'). Found none of {heuristic_examples}. (SC-3)"
        )

    def test_gap_detection_md_documents_match_threshold(self):
        """E13: gap-detection.md documents a match score / threshold (SC-3).

        RED: gap-detection.md does not exist yet.
        GREEN after: Phase 8 documents the scoring threshold used for gap detection.
        """
        content = _read_gap_detection()
        content_lower = content.lower()
        has_threshold = (
            "threshold" in content_lower
            or "score" in content_lower
            or "match score" in content_lower
        )
        assert has_threshold, (
            "gap-detection.md must document a match score or threshold for gap detection (SC-3). "
            "Without a threshold, the heuristic is not deterministic."
        )


# ---------------------------------------------------------------------------
# F: Results JSON schema (SC-3)
# ---------------------------------------------------------------------------


class TestResultsJsonSchema:
    """F14–F16: SKILL.md or gap-detection.md documents the results JSON schema."""

    def test_schema_documents_matched_canon_docs(self):
        """F14: SKILL.md or gap-detection.md mentions 'matched_canon_docs' (SC-3).

        RED: neither file exists yet.
        GREEN after: Phase 8 documents the results JSON schema.
        """
        assert SKILL_MD.exists() or GAP_DETECTION_MD.exists(), (
            "At least one of SKILL.md or gap-detection.md must exist (SC-3)."
        )
        assert _skill_or_gap("matched_canon_docs"), (
            "SKILL.md or gap-detection.md must document the 'matched_canon_docs' "
            "field in the results JSON schema (SC-3)."
        )

    def test_schema_documents_gaps_found(self):
        """F15: SKILL.md or gap-detection.md mentions 'gaps_found' (SC-3).

        RED: neither file exists yet.
        GREEN after: Phase 8 documents the results JSON schema.
        """
        assert SKILL_MD.exists() or GAP_DETECTION_MD.exists(), (
            "At least one of SKILL.md or gap-detection.md must exist (SC-3)."
        )
        assert _skill_or_gap("gaps_found"), (
            "SKILL.md or gap-detection.md must document the 'gaps_found' "
            "field in the results JSON schema (SC-3)."
        )

    def test_schema_documents_prs_drafted(self):
        """F16: SKILL.md or gap-detection.md mentions 'prs_drafted' (SC-3).

        RED: neither file exists yet.
        GREEN after: Phase 8 documents the results JSON schema.
        """
        assert SKILL_MD.exists() or GAP_DETECTION_MD.exists(), (
            "At least one of SKILL.md or gap-detection.md must exist (SC-3)."
        )
        assert _skill_or_gap("prs_drafted"), (
            "SKILL.md or gap-detection.md must document the 'prs_drafted' "
            "field in the results JSON schema (SC-3)."
        )


# ---------------------------------------------------------------------------
# G: Required template and support files
# ---------------------------------------------------------------------------


class TestRequiredSupportFiles:
    """G17–G21: All required template and support files must exist."""

    def test_pr_body_gc_data_v2_template_exists(self):
        """G17: templates/pr-body-gc-data-v2.md must exist (SC-1).

        RED: canon-backport skill directory has not been created yet.
        GREEN after: Phase 8 creates the PR body template for gc-data-v2.
        """
        assert TEMPLATE_GC_DATA.exists(), (
            f"PR body template not found at {TEMPLATE_GC_DATA}. "
            "Phase 8 must create the PR description template for gc-data-v2 backports (SC-1)."
        )

    def test_pr_body_tech_gc_knowledgebase_template_exists(self):
        """G18: templates/pr-body-tech-gc-knowledgebase.md must exist (SC-1).

        RED: canon-backport skill directory has not been created yet.
        GREEN after: Phase 8 creates the PR body template for tech-gc-knowledgebase.
        """
        assert TEMPLATE_TECH_KB.exists(), (
            f"PR body template not found at {TEMPLATE_TECH_KB}. "
            "Phase 8 must create the PR description template for tech-gc-knowledgebase backports (SC-1)."
        )

    def test_three_question_gate_template_exists(self):
        """G19: .sdlc/templates/three-question-gate.md must exist (SC-4).

        RED: template has not been created yet.
        GREEN after: Phase 8 creates the three-question gate template for phase-9.
        """
        assert THREE_QUESTION_GATE.exists(), (
            f"three-question-gate.md template not found at {THREE_QUESTION_GATE}. "
            "Phase 8 must create this template — it is required by phase-9 (SC-4)."
        )

    def test_retro_proposal_gc_data_v2_template_exists(self):
        """G20: .sdlc/templates/retro-proposal-gc-data-v2.yaml must exist (SC-7).

        RED: template has not been created yet.
        GREEN after: Phase 8 creates the gc-data-v2 retro proposal template.
        """
        assert RETRO_PROPOSAL_GC_DATA_TEMPLATE.exists(), (
            f"retro-proposal-gc-data-v2.yaml template not found at {RETRO_PROPOSAL_GC_DATA_TEMPLATE}. "
            "Phase 8 must create this template — used by /retro when platform keywords match (SC-7)."
        )
