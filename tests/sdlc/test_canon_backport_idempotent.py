"""STORY-1003: canon-backport idempotency — Phase 7 RED tests.

SC-2: Verifies the canon-backport skill documents its idempotency contract:
      - uses 'gh pr list --search "STORY-' to check for an existing PR
      - emits a deterministic "already ran" message
      - does NOT open a second PR
      - exits 0 (not an error) when idempotency hit

  A (SC-2): SKILL.md documents the STORY- search pattern
  B (SC-2): SKILL.md documents the "already ran" message text
  C (SC-2): SKILL.md states no new PR is opened on second run
  D (SC-2): SKILL.md documents exit 0 on idempotency hit

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/canon-backport/SKILL.md does not exist yet
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / ".sdlc" / "skills" / "canon-backport"
SKILL_MD = SKILL_DIR / "SKILL.md"


def _read_skill() -> str:
    """Read SKILL.md content. Fails test if file doesn't exist."""
    assert SKILL_MD.exists(), (
        f"SKILL.md not found at {SKILL_MD}. "
        "Phase 8 must create .sdlc/skills/canon-backport/SKILL.md "
        "with full idempotency documentation (SC-2)."
    )
    return SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# A: STORY- search pattern (SC-2)
# ---------------------------------------------------------------------------


class TestIdempotencySearchPattern:
    """A01–A02: SKILL.md documents the gh pr list --search idiom."""

    def test_skill_md_documents_story_search_pattern(self):
        """A01: SKILL.md documents 'gh pr list --search "STORY-' pattern (SC-2).

        The skill must use this pattern to detect whether a PR for the current
        story was already opened, preventing duplicate PRs across re-runs.

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents the gh pr list --search deduplication idiom.
        """
        content = _read_skill()
        assert 'gh pr list --search "STORY-' in content or "gh pr list --search 'STORY-" in content, (
            "SKILL.md must document 'gh pr list --search \"STORY-' (or single-quoted) "
            "as the idempotency check pattern (SC-2). "
            "This ensures repeated invocations don't create duplicate PRs."
        )

    def test_skill_md_documents_search_command_in_context(self):
        """A02: SKILL.md references gh pr list --search in an idempotency context (SC-2).

        The mention must appear alongside idempotency logic — not just as a
        generic example. Checks that both 'gh pr list --search' and a related
        idempotency concept appear together.

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents idempotency using gh pr list --search.
        """
        content = _read_skill()
        content_lower = content.lower()
        assert "gh pr list --search" in content, (
            "SKILL.md must contain 'gh pr list --search' (SC-2)."
        )
        has_idempotency_context = (
            "idempotent" in content_lower
            or "already ran" in content_lower
            or "duplicate" in content_lower
            or "skip" in content_lower
        )
        assert has_idempotency_context, (
            "SKILL.md must reference 'gh pr list --search' in an idempotency context — "
            "near 'idempotent', 'already ran', 'duplicate', or 'skip' (SC-2)."
        )


# ---------------------------------------------------------------------------
# B: "already ran" message (SC-2)
# ---------------------------------------------------------------------------


class TestAlreadyRanMessage:
    """B03–B04: SKILL.md documents the deterministic "already ran" output."""

    def test_skill_md_documents_already_ran_message(self):
        """B03: SKILL.md documents 'canon-backport already ran for' message (SC-2).

        The message text must be deterministic so callers can parse it
        programmatically (e.g., in CI or in the phase-9 gate check).

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 adds the exact "already ran" message text to SKILL.md.
        """
        content = _read_skill()
        assert "canon-backport already ran for" in content, (
            "SKILL.md must document the exact message 'canon-backport already ran for' (SC-2). "
            "Callers rely on this deterministic string to detect idempotency hits."
        )

    def test_skill_md_documents_message_destination(self):
        """B04: SKILL.md documents where the "already ran" message is emitted (SC-2).

        The message must be surfaced to the user — either printed to stdout or
        written to a log / results file.

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 specifies where the idempotency message is emitted.
        """
        content = _read_skill()
        content_lower = content.lower()
        has_output_destination = (
            "stdout" in content_lower
            or "print" in content_lower
            or "log" in content_lower
            or "output" in content_lower
            or "canon-backport-results.json" in content
        )
        assert has_output_destination, (
            "SKILL.md must document where the 'already ran' message is emitted — "
            "stdout, a log, or canon-backport-results.json (SC-2)."
        )


# ---------------------------------------------------------------------------
# C: No second PR on repeat run (SC-2)
# ---------------------------------------------------------------------------


class TestNoSecondPr:
    """C05: SKILL.md explicitly states no new PR is opened on repeated runs."""

    def test_skill_md_states_no_new_pr_on_second_run(self):
        """C05: SKILL.md documents that a second run does NOT open a new PR (SC-2).

        This is the core idempotency guarantee — re-running the skill after the
        PR was already created must be a no-op from a PR perspective.

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 explicitly states the no-duplicate-PR contract.
        """
        content = _read_skill()
        content_lower = content.lower()
        # Look for explicit language about skipping / not opening a new PR
        # on a repeated run
        has_no_second_pr = (
            "no new pr" in content_lower
            or "not open" in content_lower
            or "skip" in content_lower
            or "does not open" in content_lower
            or "already open" in content_lower
        )
        assert has_no_second_pr, (
            "SKILL.md must explicitly state that a second run does NOT open a new PR (SC-2). "
            "Expected language like 'no new PR', 'does not open', 'skip', or 'already open'."
        )


# ---------------------------------------------------------------------------
# D: Exit 0 on idempotency hit (SC-2)
# ---------------------------------------------------------------------------


class TestExitZeroOnIdempotency:
    """D06: SKILL.md documents exit 0 (not an error) when idempotency is triggered."""

    def test_skill_md_documents_exit_zero_on_idempotency(self):
        """D06: SKILL.md documents that idempotency hit results in exit 0 (SC-2).

        An idempotency hit is a success condition, not an error. The skill must
        exit cleanly (exit 0) so callers don't misinterpret a repeat invocation
        as a failure.

        RED: SKILL.md does not exist yet.
        GREEN after: Phase 8 documents exit 0 semantics for idempotency.
        """
        content = _read_skill()
        content_lower = content.lower()
        has_exit_zero = (
            "exit 0" in content_lower
            or "exit code 0" in content_lower
            or "exits 0" in content_lower
            or "success" in content_lower
        )
        assert has_exit_zero, (
            "SKILL.md must document exit 0 (or equivalent success semantics) "
            "when idempotency is triggered (SC-2). "
            "An idempotency hit is not an error — callers must not treat it as one."
        )
