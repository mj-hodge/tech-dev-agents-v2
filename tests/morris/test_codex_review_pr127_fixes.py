"""STORY-722: Regression tests for PR #127 review findings.

Phase 7 -- RED-state tests targeting five specific bugs found in Morris's
review of STORY-720 (codex review severity + debt log).

Test groups:
  A. Duplicate issue over-match (CRITICAL) -- _is_duplicate_issue fallback    (3 tests)
  B. Process review: two different P2s both create issues                      (1 test)
  C. Fingerprint window range (MEDIUM) -- only 5-10, not 1-14                 (4 tests)
  D. Bare except logging (LOW) -- exception not silently swallowed            (1 test)
  E. Output variance -- different duplicate lists -> different skip counts     (1 test)

RED reasons (before Phase 8 fixes):
  - Module does not exist on this branch -> ImportError -> all tests skip
  - Once source is merged from story-720: _is_duplicate_issue fallback causes
    test_different_finding_id_returns_false to FAIL (returns True, not False)
  - check_fingerprint_in_file matches 3-line windows (should not)
  - check_fingerprint_in_file misses 12-line windows only if >10 (range(1,15))
  - Bare except does not log -> logging assertion fails

Total: 10 tests
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import target modules -- will ImportError until Phase 8 brings them over
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "deployment" / "morris" / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from codex_review_post import (
        _is_duplicate_issue,
        process_review,
        check_code_comment_override,
    )
    from codex_debt_autoclose import check_fingerprint_in_file

    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _MODULE_AVAILABLE,
    reason="codex_review_post / codex_debt_autoclose not yet on this branch (Phase 8)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_issue(finding_id: str, title: str = "[codex-debt] some advisory") -> dict:
    """Construct a mock GitHub issue dict with a specific finding_id."""
    return {
        "number": 99,
        "title": title,
        "body": (
            f"Flagged in `src/foo.py` by Codex review on PR #100.\n\n"
            f"**Finding:** {title}\n\n"
            f"codex-finding-id:{finding_id}\n\n"
            f"<!-- codex-fingerprint: abc123 -->\n"
        ),
    }


# ===========================================================================
# Group A: _is_duplicate_issue over-match (CRITICAL -- Fix 1)
# ===========================================================================


class TestDuplicateIssueOverMatch:
    """A: _is_duplicate_issue must match ONLY on exact finding_id, not
    on the mere presence of [codex-debt] in title + codex-finding-id: in body.

    Bug: lines 284-286 return True for ANY existing codex-debt issue,
    preventing new P2 debt issues from ever being created once one exists.
    """

    def test_exact_finding_id_match_returns_true(self):
        """A1: Issue body contains the exact finding_id marker -> True."""
        finding = {"finding_id": "abc123def456"}
        existing_issues = [_make_issue("abc123def456")]

        result = _is_duplicate_issue(finding, existing_issues)

        assert result is True, "Exact finding_id match should return True"

    def test_different_finding_id_returns_false(self):
        """A2: Existing codex-debt issue has DIFFERENT finding_id -> False.

        THIS IS THE CRITICAL REGRESSION: before the fix, the fallback block
        (lines 284-286) returns True because the existing issue has
        '[codex-debt]' in title AND 'codex-finding-id:' in body -- even
        though the finding_id doesn't match the current finding.
        """
        finding = {"finding_id": "NEW_FINDING_999"}
        existing_issues = [_make_issue("OLD_FINDING_111")]

        result = _is_duplicate_issue(finding, existing_issues)

        assert result is False, (
            "_is_duplicate_issue should return False when existing issue has "
            "a different finding_id. The title-based fallback is the bug."
        )

    def test_no_existing_issues_returns_false(self):
        """A3: Empty issue list -> False."""
        finding = {"finding_id": "abc123"}

        result = _is_duplicate_issue(finding, [])

        assert result is False


# ===========================================================================
# Group B: Process review -- two different P2s both create issues
# ===========================================================================


class TestTwoDifferentP2sBothCreateIssues:
    """B: When one P2 debt issue already exists, a second P2 with a
    different finding_id must still create its own debt issue."""

    def test_second_p2_creates_issue_when_first_exists(self):
        """B1: Two different P2 findings processed; one already has an issue.
        The second P2 must NOT be skipped.

        End-to-end regression for SC-2: 'A new P2 finding creates a debt
        issue even when other unrelated P2 debt issues already exist.'
        """
        # Two distinct P2 findings
        codex_output = """\
## P2: Advisory -- missing null check in handler
File: src/handler.py
Lines: 10-12
No null check before accessing .items().

## P2: Advisory -- SQL injection risk in search
File: src/search.py
Lines: 20-25
User input concatenated directly into SQL query.
"""
        # Simulate: first finding already has a debt issue
        existing_issue = _make_issue("first_finding_id", "[codex-debt] missing null check")

        with patch("codex_review_post.gh_create_review_comment"), \
             patch("codex_review_post.gh_create_issue") as mock_issue, \
             patch("codex_review_post.gh_submit_review"), \
             patch("codex_review_post.gh_list_reviews", return_value=[]), \
             patch("codex_review_post.gh_list_pr_comments", return_value=[]), \
             patch("codex_review_post.gh_list_repo_issues", return_value=[existing_issue]):

            result = process_review(
                pr_number=127,
                repo="tech-dev-agents",
                codex_output=codex_output,
                pr_body="",
            )

        # At least one new debt issue should be created (for the second P2
        # whose finding_id differs from the existing issue's finding_id)
        assert mock_issue.call_count >= 1, (
            "Second P2 finding with a different finding_id must create "
            "its own debt issue, even when an unrelated debt issue exists."
        )


# ===========================================================================
# Group C: Fingerprint window range (MEDIUM -- Fix 3)
# ===========================================================================


class TestFingerprintWindowRange:
    """C: check_fingerprint_in_file must only check window sizes 5-10.

    Bug: current code uses range(1, min(len+1, 15)), matching 1-14 line
    windows. Spec says fingerprints are 5-10 line snippets.
    """

    def _fingerprint(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def test_3_line_snippet_not_matched(self):
        """C1: A 3-line snippet fingerprint should NOT match (below min window of 5)."""
        snippet_3 = "line1\nline2\nline3"
        fp = self._fingerprint(snippet_3)
        # File contains the snippet embedded in more lines
        file_content = "header\n" + snippet_3 + "\nfooter\nmore"

        result = check_fingerprint_in_file(file_content, fp)

        assert result is False, (
            "3-line window should not be checked; spec says 5-10 only"
        )

    def test_5_line_snippet_matched(self):
        """C2: A 5-line snippet fingerprint DOES match (min window)."""
        snippet_5 = "line1\nline2\nline3\nline4\nline5"
        fp = self._fingerprint(snippet_5)
        file_content = "header\n" + snippet_5 + "\nfooter"

        result = check_fingerprint_in_file(file_content, fp)

        assert result is True, "5-line window is within spec range (5-10)"

    def test_10_line_snippet_matched(self):
        """C3: A 10-line snippet fingerprint DOES match (max window)."""
        lines = [f"line{i}" for i in range(1, 11)]
        snippet_10 = "\n".join(lines)
        fp = self._fingerprint(snippet_10)
        file_content = "header\n" + snippet_10 + "\nfooter"

        result = check_fingerprint_in_file(file_content, fp)

        assert result is True, "10-line window is within spec range (5-10)"

    def test_12_line_snippet_not_matched(self):
        """C4: A 12-line snippet fingerprint should NOT match (above max window of 10)."""
        lines = [f"line{i}" for i in range(1, 13)]
        snippet_12 = "\n".join(lines)
        fp = self._fingerprint(snippet_12)
        file_content = snippet_12

        result = check_fingerprint_in_file(file_content, fp)

        assert result is False, (
            "12-line window should not be checked; spec says 5-10 only"
        )


# ===========================================================================
# Group D: Bare except logging (LOW -- Fix 4)
# ===========================================================================


class TestBareExceptLogging:
    """D: The except block in check_code_comment_override must log,
    not silently swallow the exception."""

    def test_exception_in_read_file_lines_is_logged(self, caplog):
        """D1: When read_file_lines raises, the exception is logged
        (not silently swallowed with bare 'pass').

        SC-5: 'No bare except Exception: pass in codex_review_post.py.'
        """
        with patch(
            "codex_review_post.read_file_lines",
            side_effect=RuntimeError("connection failed"),
        ):
            with caplog.at_level(logging.DEBUG):
                result = check_code_comment_override("src/foo.py", 10, 15)

        # Function returns False (no override found) -- that's fine
        assert result is False

        # The exception must have been logged, not silently swallowed
        log_text = caplog.text.lower()
        assert "connection failed" in log_text or len(caplog.records) > 0, (
            "Exception in read_file_lines must be logged. "
            "Bare 'except Exception: pass' is not acceptable."
        )


# ===========================================================================
# Group E: Output variance
# ===========================================================================


class TestOutputVariance:
    """E: Different duplicate-issue lists produce different skip counts."""

    def test_different_existing_issues_produce_different_skip_counts(self):
        """E1: Process review with no existing issues vs with existing issues
        produces different action counts (output-variance gate)."""
        codex_output = """\
## P2: Advisory -- missing validation
File: src/handler.py
Lines: 10-12
No input validation.
"""
        with patch("codex_review_post.gh_create_review_comment"), \
             patch("codex_review_post.gh_create_issue") as mock_issue_a, \
             patch("codex_review_post.gh_submit_review"), \
             patch("codex_review_post.gh_list_reviews", return_value=[]), \
             patch("codex_review_post.gh_list_pr_comments", return_value=[]), \
             patch("codex_review_post.gh_list_repo_issues", return_value=[]):

            result_a = process_review(
                pr_number=127, repo="r", codex_output=codex_output, pr_body="",
            )
            issue_count_a = mock_issue_a.call_count

        # Now with an existing issue that matches the finding
        from codex_review_post import parse_codex_findings

        findings = parse_codex_findings(codex_output)
        matching_issue = _make_issue(findings[0]["finding_id"])

        with patch("codex_review_post.gh_create_review_comment"), \
             patch("codex_review_post.gh_create_issue") as mock_issue_b, \
             patch("codex_review_post.gh_submit_review"), \
             patch("codex_review_post.gh_list_reviews", return_value=[]), \
             patch("codex_review_post.gh_list_pr_comments", return_value=[]), \
             patch("codex_review_post.gh_list_repo_issues", return_value=[matching_issue]):

            result_b = process_review(
                pr_number=127, repo="r", codex_output=codex_output, pr_body="",
            )
            issue_count_b = mock_issue_b.call_count

        assert issue_count_a != issue_count_b, (
            "No existing issues -> creates issue; matching existing issue -> skips. "
            "Issue creation counts must differ."
        )
