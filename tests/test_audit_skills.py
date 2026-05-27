"""STORY-508: Audit tests for Morris band-aid removal and regression guard.

Phase 7 — RED state tests. Some tests are RED because:
- AC-1: fleet-vigilance/SKILL.md still has auto-commit/push/PR creation in Check 0d
- AC-2: review-prs/SKILL.md does not yet have Partial PR exclusion
- AC-3, AC-4, AC-15: patterns currently absent (regression guards — immediately GREEN)

These tests MUST stay green after Phase 8 ships.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "deployment" / "vm" / "skills"
SCRIPTS_DIR = REPO_ROOT / "deployment" / "vm"


# ---------------------------------------------------------------------------
# AC-1: fleet-vigilance auto-commit/push/PR behavior removed
# ---------------------------------------------------------------------------


class TestFleetVigilanceBandAidRemoval:
    """AC-1: Check 0d auto-commit/push/PR creation behaviors must be removed from fleet-vigilance/SKILL.md.

    RED: The lines below currently exist in Check 0d of fleet-vigilance/SKILL.md:
      - git add -A && git commit -m 'auto-commit: uncommitted phase work'
      - git push origin HEAD
      - gh pr create --title 'STORY-XXX: <title>'

    GREEN after Phase 8 removes the Check 0d partial-PR recovery playbook.
    """

    SKILL_FILE = SKILLS_DIR / "fleet-vigilance" / "SKILL.md"

    def test_skill_file_exists(self):
        """Prerequisite: fleet-vigilance/SKILL.md must exist."""
        assert self.SKILL_FILE.exists(), (
            f"fleet-vigilance/SKILL.md not found at {self.SKILL_FILE}. "
            "This file must exist — do not delete the skill, only remove Check 0d's auto-commit section."
        )

    def test_no_auto_commit_in_fleet_vigilance(self):
        """AC-1: 'git add -A && git commit' auto-commit must be removed from fleet-vigilance/SKILL.md.

        RED: Check 0d currently has:
          git add -A && git commit -m 'auto-commit: uncommitted phase work'

        GREEN after Phase 8 removes Check 0d's auto-commit-and-push-stranded-work playbook.
        STORY-507 AC-4 creates partial PRs automatically on SIGTERM; Morris doing this creates duplicates.
        """
        if not self.SKILL_FILE.exists():
            pytest.skip("fleet-vigilance/SKILL.md not found")
        content = self.SKILL_FILE.read_text()
        assert "git add -A && git commit" not in content, (
            "fleet-vigilance/SKILL.md still contains 'git add -A && git commit' auto-commit "
            "behavior — AC-1 requires removing Check 0d's auto-commit-and-push-stranded-work playbook. "
            "STORY-507 AC-4 creates partial PRs automatically on SIGTERM; this line causes duplicates. "
            "Remove the AUTO-FIX bullet that says: "
            "\"git add -A && git commit -m 'auto-commit: uncommitted phase work' && git push origin HEAD\""
        )

    def test_no_auto_push_in_fleet_vigilance(self):
        """AC-1: 'git push origin HEAD' auto-push must be removed from fleet-vigilance/SKILL.md.

        RED: Check 0d currently has:
          git push origin HEAD

        GREEN after Phase 8 removes Check 0d's auto-push.
        STORY-507 AC-4's SIGTERM handler commits and pushes automatically.
        """
        if not self.SKILL_FILE.exists():
            pytest.skip("fleet-vigilance/SKILL.md not found")
        content = self.SKILL_FILE.read_text()
        # Exclude the "NEVER" warning lines that mention git push as a prohibition
        problem_lines = [
            line for line in content.splitlines()
            if "git push origin HEAD" in line
            and not line.strip().startswith("#")
            and "NEVER" not in line
            and "never" not in line.lower()
        ]
        assert len(problem_lines) == 0, (
            "fleet-vigilance/SKILL.md still contains 'git push origin HEAD' auto-push "
            "behavior in executable instructions:\n"
            + "\n".join(f"  {l}" for l in problem_lines) + "\n"
            "AC-1 requires removing Check 0d's auto-push. "
            "STORY-507 AC-4's SIGTERM handler handles commit+push automatically."
        )

    def test_no_auto_pr_create_in_fleet_vigilance(self):
        """AC-1: 'gh pr create --title' auto-PR creation for stranded work must be removed.

        RED: Check 0d currently has:
          gh pr create --title 'STORY-XXX: <title>'

        GREEN after Phase 8 removes this. STORY-507 AC-4 handles partial-PR creation.
        """
        if not self.SKILL_FILE.exists():
            pytest.skip("fleet-vigilance/SKILL.md not found")
        content = self.SKILL_FILE.read_text()
        lines = content.splitlines()
        # The specific stranded-work auto-PR pattern uses STORY-XXX placeholder
        auto_pr_lines = [
            line for line in lines
            if "gh pr create --title" in line and "STORY-XXX" in line
        ]
        assert len(auto_pr_lines) == 0, (
            "fleet-vigilance/SKILL.md still has auto-PR creation for stranded work:\n"
            + "\n".join(f"  {l}" for l in auto_pr_lines) + "\n"
            "AC-1 requires removing this. STORY-507 AC-4 manages partial-PR lifecycle automatically."
        )

    def test_no_partial_preserve_patterns_in_skills(self):
        """AC-1 regression: 'partial.*preserve' or 'Morris.*partial' must not appear in any skill.

        This is a GREEN regression guard — these patterns should not exist anywhere.
        It turns RED if someone accidentally adds them.
        """
        result = subprocess.run(
            ["grep", "-rni", r"partial.*preserve\|Morris.*partial", str(SKILLS_DIR)],
            capture_output=True,
            text=True,
        )
        assert result.stdout == "", (
            f"Banned pattern 'partial.*preserve|Morris.*partial' found in skills:\n{result.stdout}"
            "Remove any instruction that has Morris preserve or create partial PRs."
        )


# ---------------------------------------------------------------------------
# AC-2: review-prs Partial PR exclusion added
# ---------------------------------------------------------------------------


class TestReviewPrsPartialExclusion:
    """AC-2: review-prs/SKILL.md must contain explicit Partial PR exclusion.

    RED: review-prs/SKILL.md does not currently have any Partial PR exclusion.
    GREEN after Phase 8 adds the 'Do NOT review PRs with Partial in title' section.

    Partial PRs are created by STORY-507 AC-4's SIGTERM handler to preserve work
    mid-execution. Morris reviewing/merging/closing these PRs would destroy in-progress work.
    """

    SKILL_FILE = SKILLS_DIR / "morris" / "review-prs" / "SKILL.md"

    def test_review_prs_skill_exists(self):
        """Prerequisite: morris/review-prs/SKILL.md must exist."""
        assert self.SKILL_FILE.exists(), (
            f"morris/review-prs/SKILL.md not found at {self.SKILL_FILE}. "
            "This file must exist — do not delete the skill, only add the Partial exclusion section."
        )

    def test_review_prs_has_partial_exclusion(self):
        """AC-2: review-prs/SKILL.md must contain a 'Partial' PR exclusion instruction.

        RED: No such exclusion exists currently.

        GREEN after adding a section such as:
          ## CRITICAL: Do NOT review Partial PRs
          Do NOT review, approve, request-changes, comment on, close, or create PRs
          whose title contains "Partial". STORY-507 AC-4 manages partial-PR lifecycle.
        """
        if not self.SKILL_FILE.exists():
            pytest.skip("morris/review-prs/SKILL.md not found")
        content = self.SKILL_FILE.read_text()
        assert "Partial" in content, (
            "morris/review-prs/SKILL.md has no 'Partial' PR exclusion. "
            "AC-2 requires adding an explicit instruction such as: "
            "'Do NOT review, approve, request-changes, comment on, close, or create PRs "
            "whose title contains \"Partial\". STORY-507 AC-4 manages partial-PR lifecycle automatically.'"
        )

    def test_review_prs_exclusion_says_do_not(self):
        """AC-2: The Partial exclusion must use an explicit 'Do NOT' directive.

        A mention of 'Partial' is not enough; the instruction must forbid action.
        RED: No exclusion directive present yet.
        """
        if not self.SKILL_FILE.exists():
            pytest.skip("morris/review-prs/SKILL.md not found")
        content = self.SKILL_FILE.read_text()
        has_partial = "Partial" in content
        has_do_not = "Do NOT" in content or "do not" in content.lower()
        assert has_partial and has_do_not, (
            "morris/review-prs/SKILL.md missing explicit exclusion instruction. "
            "Must contain both 'Partial' and a 'Do NOT' directive. "
            f"has_partial={has_partial}, has_exclusion={has_do_not}. "
            "Add: '## CRITICAL: Do NOT Review Partial PRs\\n"
            "Do NOT review, approve, comment on, close, or merge PRs with \"Partial\" in the title.'"
        )

    def test_review_prs_exclusion_covers_all_actions(self):
        """AC-2: The exclusion must cover review/approve/comment — not just 'review'.

        RED: Section not yet present.

        Rationale: an incomplete exclusion (e.g., only skipping review but still approving)
        would still allow Morris to accidentally merge a partial PR.
        """
        if not self.SKILL_FILE.exists():
            pytest.skip("morris/review-prs/SKILL.md not found")
        content = self.SKILL_FILE.read_text()
        # Check that the file has 'Partial' near at least two of: review/approve/comment/close/merge
        partial_idx = content.find("Partial")
        if partial_idx == -1:
            pytest.fail(
                "morris/review-prs/SKILL.md has no 'Partial' exclusion at all. "
                "AC-2 requires adding the full exclusion block."
            )
        # Look at a 500-char window around the first Partial mention
        window = content[max(0, partial_idx - 100) : partial_idx + 400].lower()
        action_words = ["review", "approve", "comment", "close", "merge", "create"]
        found_actions = [w for w in action_words if w in window]
        assert len(found_actions) >= 2, (
            f"'Partial' appears in review-prs/SKILL.md but the surrounding context only covers "
            f"{found_actions} actions. The exclusion must cover at least review, approve, and comment "
            "to prevent all dangerous interactions with partial PRs."
        )


# ---------------------------------------------------------------------------
# AC-3: No [RETRY 1/3] loop in dispatch skill
# ---------------------------------------------------------------------------


class TestNoRetryLoopInDispatch:
    """AC-3: Morris's dispatch skill must not have [RETRY 1/3] manual retry logic.

    This is a GREEN regression guard. The pattern is currently absent.
    It becomes RED the moment someone adds a manual retry loop to any skill.

    Rationale: STORY-507 AC-6 adds automatic reclaim for paused stories via the API.
    Manual retry loops in skills create double-dispatch races.
    """

    def test_no_retry_loop_in_skills(self):
        """AC-3: '[RETRY N/N]' manual retry loop must be absent from all Morris skills.

        GREEN guard: pattern is currently absent. Turns RED if added.
        """
        result = subprocess.run(
            ["grep", "-rn", r"\[RETRY [0-9]/[0-9]\]", str(SKILLS_DIR)],
            capture_output=True,
            text=True,
        )
        assert result.stdout == "", (
            f"Manual RETRY loop pattern '[RETRY N/N]' found in skills:\n{result.stdout}\n"
            "AC-3: Remove manual [RETRY N/N] dispatch logic. "
            "STORY-507 AC-6 auto-reclaims paused stories via POST /api/dispatch/reclaim — "
            "skills should call that endpoint, not implement their own retry loops."
        )


# ---------------------------------------------------------------------------
# AC-4: No direct DB status writes
# ---------------------------------------------------------------------------


class TestNoDirectDBStatusWrites:
    """AC-4: Morris skills/scripts must not directly UPDATE dispatch_items.status.

    GREEN regression guard. Pattern is currently absent.
    All state transitions must go through the dispatch API, not raw SQL.

    Rationale: Direct DB writes bypass audit logging, claim validation, and transition guards.
    """

    def test_no_direct_db_status_writes_in_skills(self):
        """AC-4: 'UPDATE dispatch_items SET status' must be absent from all skills and scripts.

        GREEN guard: pattern currently absent. Turns RED if added.
        """
        result = subprocess.run(
            [
                "grep",
                "-rn",
                "UPDATE dispatch_items SET status",
                str(SKILLS_DIR),
                str(SCRIPTS_DIR),
            ],
            capture_output=True,
            text=True,
        )
        assert result.stdout == "", (
            f"Direct DB status write found:\n{result.stdout}\n"
            "AC-4: All dispatch state transitions go through the API. "
            "Use POST /api/dispatch/claim, /complete, /pause, /reclaim — never raw SQL."
        )


# ---------------------------------------------------------------------------
# AC-15: CI regression guard — banned patterns
# ---------------------------------------------------------------------------


BANNED_PATTERNS = [
    (
        "UPDATE dispatch_items SET status",
        "Direct DB status write banned — use POST /api/dispatch/{claim,complete,pause,reclaim}",
    ),
    (
        "enqueued_at = '20",
        (
            "Priority hack via enqueued_at manipulation banned — "
            "use POST /api/dispatch/priority to set story priority. "
            "Backdating enqueued_at to jump the queue bypasses audit logging."
        ),
    ),
]


@pytest.mark.parametrize("pattern,reason", BANNED_PATTERNS, ids=["direct-db-write", "priority-hack"])
def test_no_banned_patterns_in_skills_and_scripts(pattern: str, reason: str):
    """AC-15: CI regression guard — banned patterns must be absent from all skills and scripts.

    GREEN guards: both patterns currently absent.
    These turn RED if anyone adds a banned pattern during future development.

    Patterns:
    - 'UPDATE dispatch_items SET status': direct DB write bypassing API transition guards
    - "enqueued_at = '20": backdating enqueued_at to manipulate queue priority

    To make GREEN (if RED): remove the flagged line and use the correct API endpoint instead.
    """
    result = subprocess.run(
        ["grep", "-rn", pattern, str(SKILLS_DIR), str(SCRIPTS_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.stdout == "", (
        f"Banned pattern found: {reason}\n\nMatches:\n{result.stdout}"
    )
