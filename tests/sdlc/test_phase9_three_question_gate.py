"""STORY-1003: phase-9 3-question gate — Phase 7 RED tests.

SC-4: phase-9/SKILL.md has a '## 3-question gate' section listing all three
      mandatory questions (Canon-doc impact, Scaffold backport, Sibling sweep)
      and documents that the gate is blocking (phase 9 cannot close without it).

SC-5: SKILL.md documents that N/A answers require explicit justification and
      that bare N/A is not accepted without explanation.

SC-6: SKILL.md documents that the skill auto-invokes /canon-backport, supports
      a --no-backport escape hatch, and logs the invocation result in .project.

  A (SC-4): gate section heading present in phase-9/SKILL.md
  B (SC-4): all three questions present
  C (SC-4): gate is documented as blocking
  D (SC-5): N/A requires justification
  E (SC-6): auto-invocation documented, --no-backport flag, .project logging
  F:         three-question-gate.md template exists and contains all three questions

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/phase-9/SKILL.md exists but has no 3-question gate section
  - .sdlc/templates/three-question-gate.md has not been created yet
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE9_SKILL_MD = REPO_ROOT / ".sdlc" / "skills" / "phase-9" / "SKILL.md"
THREE_QUESTION_GATE_TEMPLATE = REPO_ROOT / ".sdlc" / "templates" / "three-question-gate.md"


def _read_phase9_skill() -> str:
    """Read phase-9/SKILL.md content. Fails test if file doesn't exist."""
    assert PHASE9_SKILL_MD.exists(), (
        f"phase-9/SKILL.md not found at {PHASE9_SKILL_MD}. "
        "The file must exist — Phase 8 adds the 3-question gate section to it."
    )
    return PHASE9_SKILL_MD.read_text()


def _read_gate_template() -> str:
    """Read three-question-gate.md content. Fails test if file doesn't exist."""
    assert THREE_QUESTION_GATE_TEMPLATE.exists(), (
        f"three-question-gate.md not found at {THREE_QUESTION_GATE_TEMPLATE}. "
        "Phase 8 must create .sdlc/templates/three-question-gate.md (SC-4)."
    )
    return THREE_QUESTION_GATE_TEMPLATE.read_text()


# ---------------------------------------------------------------------------
# A: Gate section heading (SC-4)
# ---------------------------------------------------------------------------


class TestGateSectionHeading:
    """A01: phase-9/SKILL.md must have a '## 3-question gate' heading."""

    def test_phase9_skill_md_has_three_question_gate_heading(self):
        """A01: SKILL.md contains '## 3-question gate' heading (SC-4).

        RED: phase-9/SKILL.md exists but the 3-question gate section has not
             been added yet.
        GREEN after: Phase 8 adds the '## 3-question gate' section to SKILL.md.
        """
        content = _read_phase9_skill()
        assert "## 3-question gate" in content, (
            "phase-9/SKILL.md must contain the heading '## 3-question gate' (SC-4). "
            "This section is the blocking gate that prevents Phase 9 from closing "
            "without answering all three canon-backport questions."
        )


# ---------------------------------------------------------------------------
# B: All three questions present (SC-4)
# ---------------------------------------------------------------------------


class TestThreeQuestionsPresent:
    """B02–B04: All three gate questions must be documented in phase-9/SKILL.md."""

    def test_skill_md_has_canon_doc_impact_question(self):
        """B02: SKILL.md mentions 'Canon-doc impact' (Q1) (SC-4).

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds Q1 (Canon-doc impact) to SKILL.md.
        """
        content = _read_phase9_skill()
        assert "Canon-doc impact" in content, (
            "phase-9/SKILL.md must document Q1: 'Canon-doc impact' (SC-4). "
            "This question asks whether the work changed anything canon-doc-relevant."
        )

    def test_skill_md_has_scaffold_backport_question(self):
        """B03: SKILL.md mentions 'Scaffold backport' (Q2) (SC-4).

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds Q2 (Scaffold backport) to SKILL.md.
        """
        content = _read_phase9_skill()
        assert "Scaffold backport" in content, (
            "phase-9/SKILL.md must document Q2: 'Scaffold backport' (SC-4). "
            "This question asks whether SDLC scaffold changes need backporting."
        )

    def test_skill_md_has_sibling_sweep_question(self):
        """B04: SKILL.md mentions 'Sibling sweep' (Q3) (SC-4).

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds Q3 (Sibling sweep) to SKILL.md.
        """
        content = _read_phase9_skill()
        assert "Sibling sweep" in content, (
            "phase-9/SKILL.md must document Q3: 'Sibling sweep' (SC-4). "
            "This question asks whether sibling stories need the same fix."
        )


# ---------------------------------------------------------------------------
# C: Gate is blocking (SC-4)
# ---------------------------------------------------------------------------


class TestGateIsBlocking:
    """C05: SKILL.md documents that the gate is a hard blocker for Phase 9 close."""

    def test_skill_md_documents_gate_is_blocking(self):
        """C05: SKILL.md states Phase 9 cannot close when gate is incomplete (SC-4).

        The gate must be a hard block — not a soft reminder. SKILL.md must use
        language like 'cannot close', 'gate incomplete', or 'Phase 9 cannot close'.

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds blocking language to the gate section.
        """
        content = _read_phase9_skill()
        content_lower = content.lower()
        has_blocking_language = (
            "phase 9 cannot close: 3-question gate incomplete" in content
            or "cannot close" in content_lower
            or "gate incomplete" in content_lower
            or "blocking" in content_lower
        )
        assert has_blocking_language, (
            "phase-9/SKILL.md must state that the 3-question gate is a hard blocker (SC-4). "
            "Expected language: 'Phase 9 cannot close: 3-question gate incomplete', "
            "'cannot close', or 'gate incomplete'."
        )


# ---------------------------------------------------------------------------
# D: N/A requires justification (SC-5)
# ---------------------------------------------------------------------------


class TestNARequiresJustification:
    """D06–D07: N/A answers must require explicit written justification."""

    def test_skill_md_documents_na_requires_justification(self):
        """D06: SKILL.md mentions 'N/A requires justification' (SC-5).

        A bare N/A is not accepted. The agent must explain why the question
        doesn't apply to this story.

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds N/A policy to the gate section.
        """
        content = _read_phase9_skill()
        assert "N/A requires justification" in content, (
            "phase-9/SKILL.md must contain the text 'N/A requires justification' (SC-5). "
            "A bare N/A answer is not accepted — the agent must explain why the "
            "question is inapplicable."
        )

    def test_skill_md_documents_na_not_allowed_without_explanation(self):
        """D07: SKILL.md explains that bare N/A is not allowed (SC-5).

        Beyond the keyword, SKILL.md must make clear that N/A cannot be
        submitted without a reason.

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 documents the N/A policy clearly.
        """
        content = _read_phase9_skill()
        content_lower = content.lower()
        # The explanation can be phrased several ways
        has_na_policy = (
            "n/a requires justification" in content_lower
            or "not allowed without" in content_lower
            or "bare n/a" in content_lower
            or "n/a is not accepted" in content_lower
            or "must provide" in content_lower
        )
        assert has_na_policy, (
            "phase-9/SKILL.md must document that N/A is not accepted without explanation (SC-5). "
            "Expected language: 'N/A requires justification', 'bare N/A', or similar."
        )


# ---------------------------------------------------------------------------
# E: Auto-invocation, --no-backport, .project logging (SC-6)
# ---------------------------------------------------------------------------


class TestAutoInvocationAndLogging:
    """E08–E10: SKILL.md documents canon-backport auto-invocation and escape hatch."""

    def test_skill_md_mentions_canon_backport_in_workflow(self):
        """E08: SKILL.md mentions 'canon-backport' in the phase-9 workflow (SC-6).

        Phase 9 must auto-invoke /canon-backport as part of its gate workflow.

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds canon-backport invocation step to SKILL.md.
        """
        content = _read_phase9_skill()
        assert "canon-backport" in content, (
            "phase-9/SKILL.md must reference 'canon-backport' in its workflow (SC-6). "
            "Phase 9 auto-invokes /canon-backport as part of the 3-question gate."
        )

    def test_skill_md_documents_no_backport_flag(self):
        """E09: SKILL.md documents '--no-backport' escape hatch flag (SC-6).

        Stories with no canon-doc changes need a way to skip the backport step
        without being blocked. The --no-backport flag is that escape hatch.

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 adds --no-backport flag documentation.
        """
        content = _read_phase9_skill()
        assert "--no-backport" in content, (
            "phase-9/SKILL.md must document the '--no-backport' flag (SC-6). "
            "This escape hatch skips canon-backport for stories with no relevant changes."
        )

    def test_skill_md_documents_project_logging(self):
        """E10: SKILL.md documents logging the canon-backport result in .project (SC-6).

        The invocation result must be persisted so the gate status can be
        audited and resumed after a context clear.

        RED: 3-question gate section not in SKILL.md yet.
        GREEN after: Phase 8 documents .project logging in the gate section.
        """
        content = _read_phase9_skill()
        content_lower = content.lower()
        has_project_logging = (
            ".project" in content
            and (
                "log" in content_lower
                or "record" in content_lower
                or "write" in content_lower
                or "update" in content_lower
            )
        )
        assert has_project_logging, (
            "phase-9/SKILL.md must document logging the canon-backport invocation result "
            "in .project (SC-6). The gate state must be persisted for audit and resume."
        )


# ---------------------------------------------------------------------------
# F: three-question-gate.md template (SC-4)
# ---------------------------------------------------------------------------


class TestThreeQuestionGateTemplate:
    """F11–F14: .sdlc/templates/three-question-gate.md must exist with all questions."""

    def test_gate_template_exists(self):
        """F11: .sdlc/templates/three-question-gate.md must exist (SC-4).

        RED: Template has not been created yet.
        GREEN after: Phase 8 creates the template.
        """
        assert THREE_QUESTION_GATE_TEMPLATE.exists(), (
            f"three-question-gate.md template not found at {THREE_QUESTION_GATE_TEMPLATE}. "
            "Phase 8 must create this template (SC-4)."
        )

    def test_gate_template_has_canon_doc_impact_question(self):
        """F12: three-question-gate.md contains 'Canon-doc impact' question (SC-4).

        RED: Template has not been created yet.
        GREEN after: Phase 8 creates template with all three questions.
        """
        content = _read_gate_template()
        assert "Canon-doc impact" in content, (
            "three-question-gate.md must contain the 'Canon-doc impact' question (SC-4)."
        )

    def test_gate_template_has_scaffold_backport_question(self):
        """F13: three-question-gate.md contains 'Scaffold backport' question (SC-4).

        RED: Template has not been created yet.
        GREEN after: Phase 8 creates template with all three questions.
        """
        content = _read_gate_template()
        assert "Scaffold backport" in content, (
            "three-question-gate.md must contain the 'Scaffold backport' question (SC-4)."
        )

    def test_gate_template_has_sibling_sweep_question(self):
        """F14: three-question-gate.md contains 'Sibling sweep' question (SC-4).

        RED: Template has not been created yet.
        GREEN after: Phase 8 creates template with all three questions.
        """
        content = _read_gate_template()
        assert "Sibling sweep" in content, (
            "three-question-gate.md must contain the 'Sibling sweep' question (SC-4)."
        )
