"""STORY-794: /answer-needs-info pure-prompt skill — Phase 7 RED tests.

Tests verify the REWRITTEN SKILL.md is a pure-prompt (no Python helper)
and contains the required recipe steps, flags, and env-var documentation.

  A (T01–T03): SKILL.md structure — frontmatter, no python3 references, uses curl/gh api
  B (T04–T06): SKILL.md recipe — documents env var, dry-run flag, escalate flag
  C (T07):     SKILL.md confirmation — explicit confirmation step
  D (T08):     Python helper deleted — answer_needs_info.py must not exist
  E (T09):     No subprocess/exec references — SKILL.md doesn't invoke external scripts
  F (T10):     Output variance — two different SKILL.md tool-call steps for answer vs escalate

RED reasons (all tests fail until Phase 8):
  - .sdlc/skills/answer-needs-info/SKILL.md either doesn't exist or still
    references the python helper (pre-rewrite state)
  - .sdlc/skills/answer-needs-info/answer_needs_info.py still exists
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / ".sdlc" / "skills" / "answer-needs-info"
SKILL_MD = SKILL_DIR / "SKILL.md"
PYTHON_HELPER = SKILL_DIR / "answer_needs_info.py"


def _read_skill() -> str:
    """Read SKILL.md content. Fails test if file doesn't exist."""
    assert SKILL_MD.exists(), (
        f"SKILL.md not found at {SKILL_MD}. "
        "Phase 8 must create/rewrite .sdlc/skills/answer-needs-info/SKILL.md "
        "as a pure-prompt skill with explicit tool-call steps."
    )
    return SKILL_MD.read_text()


# ---------------------------------------------------------------------------
# A: SKILL.md structure
# ---------------------------------------------------------------------------


class TestSkillMdStructure:
    """A01–A03: SKILL.md must exist with correct frontmatter and no Python."""

    def test_skill_md_has_valid_frontmatter(self):
        """A01: SKILL.md has YAML frontmatter with name: answer-needs-info.

        RED: SKILL.md either doesn't exist or hasn't been rewritten yet.
        GREEN after: Phase 8 rewrites SKILL.md with preserved frontmatter (AC-3).
        """
        content = _read_skill()
        assert content.startswith("---"), (
            "SKILL.md must start with YAML frontmatter delimiter '---'"
        )
        assert "name: answer-needs-info" in content, (
            "SKILL.md frontmatter must contain 'name: answer-needs-info' (AC-3)"
        )
        assert "description:" in content, (
            "SKILL.md frontmatter must contain a 'description:' field (AC-3)"
        )

    def test_skill_md_has_no_python3_references(self):
        """A02: SKILL.md must not reference python3 or the Python helper (SC-1).

        RED: SKILL.md either doesn't exist or still references python3.
        GREEN after: Phase 8 removes all python3/script references from SKILL.md.
        """
        content = _read_skill()
        content_lower = content.lower()
        assert "python3" not in content_lower, (
            "SKILL.md must NOT reference 'python3'. "
            "The skill is a pure-prompt — Claude executes tool calls directly (SC-1)."
        )
        assert "answer_needs_info.py" not in content, (
            "SKILL.md must NOT reference 'answer_needs_info.py'. "
            "The Python helper is deleted in this story (SC-2, AC-2)."
        )

    def test_skill_md_uses_curl_and_gh_api(self):
        """A03: SKILL.md contains ≥ 5 curl/gh api tool-call instructions (SC-1).

        RED: SKILL.md either doesn't exist or still uses the Python helper.
        GREEN after: Phase 8 writes explicit curl + gh api steps in SKILL.md.
        """
        content = _read_skill()
        # Count occurrences of curl and gh api — SC-1 requires ≥ 5 total
        curl_count = content.lower().count("curl")
        gh_api_count = content.lower().count("gh api")
        total = curl_count + gh_api_count
        assert total >= 5, (
            f"SKILL.md must contain ≥ 5 curl/gh api references (SC-1). "
            f"Found {curl_count} curl + {gh_api_count} gh api = {total} total."
        )


# ---------------------------------------------------------------------------
# B: SKILL.md recipe content
# ---------------------------------------------------------------------------


class TestSkillMdRecipe:
    """B04–B06: SKILL.md documents env vars and flag support."""

    def test_skill_md_documents_env_var(self):
        """B04: SKILL.md documents OPS_DISPATCH_API_KEY env var requirement (SC-5, AC-4).

        RED: SKILL.md doesn't exist or lacks env var documentation.
        GREEN after: Phase 8 adds env var docs at top of SKILL.md.
        """
        content = _read_skill()
        assert "OPS_DISPATCH_API_KEY" in content, (
            "SKILL.md must document the OPS_DISPATCH_API_KEY env var (SC-5, AC-4). "
            "The skill reads the API key from this env var."
        )
        assert "X-API-Key" in content or "x-api-key" in content.lower(), (
            "SKILL.md must show how to use the API key in curl headers (X-API-Key)"
        )

    def test_skill_md_supports_dry_run_flag(self):
        """B05: SKILL.md documents --dry-run flag (SC-3, AC-5).

        RED: SKILL.md doesn't exist or lacks --dry-run documentation.
        GREEN after: Phase 8 adds --dry-run handling to SKILL.md.
        """
        content = _read_skill()
        assert "--dry-run" in content, (
            "SKILL.md must document the '--dry-run' flag (SC-3, AC-5). "
            "In dry-run mode, Claude prints 'WOULD: ...' instead of executing."
        )
        # Verify the WOULD: prefix is mentioned for dry-run behavior
        assert "WOULD" in content or "would" in content.lower(), (
            "SKILL.md must mention 'WOULD:' prefix for dry-run output (AC-5)"
        )

    def test_skill_md_supports_escalate_flag(self):
        """B06: SKILL.md documents --escalate flag (SC-3, AC-6).

        RED: SKILL.md doesn't exist or lacks --escalate documentation.
        GREEN after: Phase 8 adds --escalate handling to SKILL.md.
        """
        content = _read_skill()
        assert "--escalate" in content, (
            "SKILL.md must document the '--escalate' flag (SC-3, AC-6). "
            "In escalate mode, writes ESCALATE.md and calls /pause instead of /resume."
        )
        assert "ESCALATE.md" in content, (
            "SKILL.md must mention ESCALATE.md as the output for --escalate mode (AC-6)"
        )
        assert "/pause" in content or "pause" in content.lower(), (
            "SKILL.md must mention /pause endpoint for --escalate mode (AC-6)"
        )


# ---------------------------------------------------------------------------
# C: Confirmation step
# ---------------------------------------------------------------------------


class TestSkillMdConfirmation:
    """C07: SKILL.md includes an explicit confirmation step."""

    def test_skill_md_includes_explicit_confirmation(self):
        """C07: SKILL.md requires user confirmation before writing (AC-7).

        RED: SKILL.md doesn't exist or lacks a confirmation step.
        GREEN after: Phase 8 adds a confirmation prompt step.
        """
        content = _read_skill()
        content_lower = content.lower()
        has_confirm = (
            "confirm" in content_lower
            or "y/n" in content_lower
            or "askuserquestion" in content_lower
            or "ask the user" in content_lower
        )
        assert has_confirm, (
            "SKILL.md must include an explicit confirmation step before writing "
            "ANSWER.md (AC-7). E.g., 'Confirm? [y/N]' or use AskUserQuestion tool."
        )


# ---------------------------------------------------------------------------
# D: Python helper deleted
# ---------------------------------------------------------------------------


class TestPythonHelperDeleted:
    """D08: The Python helper file must not exist (SC-2, AC-2)."""

    def test_python_helper_file_is_deleted(self):
        """D08: answer_needs_info.py must be removed (SC-2, AC-2).

        RED: answer_needs_info.py still exists from STORY-770.
        GREEN after: Phase 8 deletes the file.
        """
        assert not PYTHON_HELPER.exists(), (
            f"answer_needs_info.py must be DELETED (SC-2, AC-2). "
            f"Found at {PYTHON_HELPER}. The skill is now a pure-prompt — "
            "no external scripts."
        )


# ---------------------------------------------------------------------------
# E: No subprocess/exec references
# ---------------------------------------------------------------------------


class TestNoExecReferences:
    """E09: SKILL.md must not instruct Claude to call external scripts."""

    def test_skill_md_has_no_subprocess_or_exec_patterns(self):
        """E09: SKILL.md must not reference subprocess, Popen, os.system, etc.

        RED: SKILL.md doesn't exist.
        GREEN after: Phase 8 writes a clean pure-prompt with no exec patterns.
        """
        content = _read_skill()
        content_lower = content.lower()
        # These patterns indicate the skill is delegating to a script, not
        # giving Claude direct tool-call instructions
        forbidden = [
            "subprocess.run",
            "subprocess.popen",
            "os.system(",
            "exec(",
            "eval(",
        ]
        for pattern in forbidden:
            assert pattern not in content_lower, (
                f"SKILL.md must NOT contain '{pattern}'. "
                "A pure-prompt skill instructs Claude to use Bash/Read/Write tools, "
                "not to invoke Python subprocess calls."
            )


# ---------------------------------------------------------------------------
# F: Output variance — answer vs escalate paths
# ---------------------------------------------------------------------------


class TestOutputVariance:
    """F10: SKILL.md describes distinct paths for answer vs escalate mode."""

    def test_skill_md_has_distinct_answer_and_escalate_paths(self):
        """F10: SKILL.md must describe different file targets and API calls
        for normal mode (ANSWER.md + /resume) vs escalate mode (ESCALATE.md + /pause).

        This verifies the recipe isn't a stub that handles both modes identically.

        RED: SKILL.md doesn't exist.
        GREEN after: Phase 8 writes both paths into SKILL.md.
        """
        content = _read_skill()
        # Normal mode: writes ANSWER.md and calls /resume
        assert "ANSWER.md" in content, (
            "SKILL.md must describe writing ANSWER.md in normal mode"
        )
        assert "/resume" in content or "resume" in content, (
            "SKILL.md must reference the /resume endpoint for normal mode"
        )
        # Escalate mode: writes ESCALATE.md and calls /pause
        assert "ESCALATE.md" in content, (
            "SKILL.md must describe writing ESCALATE.md in escalate mode"
        )
        # Both must be mentioned — they must be DIFFERENT targets
        has_resume = "resume" in content.lower()
        has_pause = "pause" in content.lower()
        assert has_resume and has_pause, (
            "SKILL.md must describe BOTH /resume (normal) and /pause (escalate) paths. "
            f"Found resume={has_resume}, pause={has_pause}."
        )
