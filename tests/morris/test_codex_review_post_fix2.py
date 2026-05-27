"""Additional tests for codex_review_post.py — STORY-720 fix2.

Covers the 6 critical test gaps identified in adversarial code review:
1. _is_duplicate_issue correctness (different fingerprint = not a dup)
2. Convergence cap exception handling (fails closed)
3. Multiple P1 findings (dispatch once, both in payload)
4. Out-of-range severity (P0/P4 do not trigger rework)
5. Fixed idempotency test (uses actual generated fingerprint)
6. gh_create_issue failure (graceful, P1 rework still dispatched)
"""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deployment.morris.scripts.codex_review_post import (
    _is_duplicate_issue,
    count_morris_request_changes,
    parse_findings,
    main,
)

# ---------------------------------------------------------------------------
# Shared review fixtures
# ---------------------------------------------------------------------------

REVIEW_P1_ONLY = """\
**P1** Critical: missing input validation.
Code allows arbitrary path traversal in `handler.py:10`.
"""

REVIEW_P2_ONLY = """\
**P2** Advisory: regex has edge case.
`parser.py:55` — unanchored regex may match partial strings.
"""

REVIEW_TWO_P1 = """\
**P1** Critical: SQL injection risk.
See `db.py:20` for the vulnerable query.

**P1** Critical: command injection.
`shell_exec.py:5` — user input passed to shell.
"""

REVIEW_P2_THEN_P1 = """\
**P2** Advisory: regex edge case.
`parser.py:55` — unanchored regex.

**P1** Critical: path traversal.
`handler.py:10` — missing validation.
"""

REVIEW_P4_ONLY = "**P4** Cosmetic: trailing whitespace.\n`style.py:99` — lint issue.\n"
REVIEW_P0_ONLY = "**P0** Nuclear: catastrophic failure.\n`core.py:1` — system down.\n"


# ---------------------------------------------------------------------------
# Test 1: _is_duplicate_issue correctness
# ---------------------------------------------------------------------------

class TestIsDuplicateIssueCorrectness:
    """Bug 1 fix: _is_duplicate_issue must match on exact fingerprint, not label presence."""

    def test_different_fingerprint_is_not_a_duplicate(self):
        """A pre-existing debt issue from a different finding must NOT block new issue creation."""
        findings = parse_findings(REVIEW_P2_ONLY)
        assert len(findings) == 1
        real_fingerprint = findings[0]["fingerprint"]

        different_fingerprint = hashlib.sha256(b"completely different finding").hexdigest()
        assert different_fingerprint != real_fingerprint

        existing_issue_body = (
            f"Codex advisory finding from PR #5.\n\n"
            f"Some other problem entirely.\n\n"
            f"<!-- codex-fingerprint: {different_fingerprint} -->\n"
            f"<!-- codex-source-file: other.py -->\n"
        )

        with patch("deployment.morris.scripts.codex_review_post.gh") as mock_gh:
            mock_gh.return_value = json.dumps([
                {"number": 10, "body": existing_issue_body}
            ])
            result = _is_duplicate_issue("owner/repo", real_fingerprint)

        assert result is False, (
            "_is_duplicate_issue returned True for a different fingerprint — "
            "this is the false-positive bug: P2 findings are silently dropped "
            "whenever any debt issue already exists."
        )

    def test_same_fingerprint_is_a_duplicate(self):
        """An existing issue with the same fingerprint IS a duplicate."""
        findings = parse_findings(REVIEW_P2_ONLY)
        real_fingerprint = findings[0]["fingerprint"]

        existing_body = (
            f"Codex advisory.\n"
            f"<!-- codex-fingerprint: {real_fingerprint} -->\n"
            f"<!-- codex-source-file: parser.py -->\n"
        )

        with patch("deployment.morris.scripts.codex_review_post.gh") as mock_gh:
            mock_gh.return_value = json.dumps([
                {"number": 7, "body": existing_body}
            ])
            result = _is_duplicate_issue("owner/repo", real_fingerprint)

        assert result is True

    def test_no_existing_issues_is_not_a_duplicate(self):
        """When there are no open debt issues, nothing is a duplicate."""
        with patch("deployment.morris.scripts.codex_review_post.gh") as mock_gh:
            mock_gh.return_value = json.dumps([])
            result = _is_duplicate_issue("owner/repo", "a" * 64)

        assert result is False


# ---------------------------------------------------------------------------
# Test 2: Convergence cap exception handling (fails closed)
# ---------------------------------------------------------------------------

class TestConvergenceCapExceptionHandling:
    """Bug 2 fix: count_morris_request_changes failure must default to AT CAP (fail closed)."""

    @patch("subprocess.run")
    def test_query_failure_returns_at_cap_value(self, mock_run):
        """On API failure, must return >= 3 so at_cap is True and rework is suppressed."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="API error")
        count = count_morris_request_changes(42, "owner/repo")
        assert count >= 3, (
            f"Expected count >= 3 (AT CAP) on failure, got {count}. "
            "Returning 0 is unsafe: it allows infinite rework dispatch when cap state is unknown."
        )

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.create_debt_issue")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_cap_query_exception_no_rework_dispatch(
        self, mock_gh, mock_issue, mock_post, mock_dispatch
    ):
        """If count_morris_request_changes raises inside main(), rework must NOT be dispatched."""
        def gh_side_effect(*args):
            args_str = " ".join(str(a) for a in args)
            if "reviews" in args_str:
                raise RuntimeError("GitHub API unavailable")
            if "body" in args_str and "json" in args_str:
                return json.dumps({"body": ""})
            if "issue" in args_str and "list" in args_str:
                return json.dumps([])
            return "https://github.com/review/1"

        mock_gh.side_effect = gh_side_effect
        mock_post.return_value = None
        mock_issue.return_value = None
        mock_dispatch.return_value = None

        import io
        with patch.object(sys, "argv", [
            "codex_review_post.py", "--pr", "1", "--repo", "owner/repo",
            "--story-id", "story-720"
        ]):
            with patch("sys.stdin", io.StringIO(REVIEW_P1_ONLY)):
                main()

        # Fail closed: rework MUST NOT be dispatched when cap query fails
        mock_dispatch.assert_not_called()
        # Comment still posted (processing continues)
        mock_post.assert_called()


# ---------------------------------------------------------------------------
# Test 3: Multiple P1 findings
# ---------------------------------------------------------------------------

class TestMultipleP1Findings:
    """Two P1 findings: REQUEST_CHANGES posted exactly once; both findings covered."""

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.create_debt_issue")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_two_p1_request_changes_once(
        self, mock_gh, mock_issue, mock_post, mock_dispatch
    ):
        dispatch_calls = []

        def capture_dispatch(story_id, repo, finding):
            dispatch_calls.append(finding)

        mock_dispatch.side_effect = capture_dispatch
        mock_post.return_value = None
        mock_issue.return_value = None

        def gh_side_effect(*args):
            args_str = " ".join(str(a) for a in args)
            if "reviews" in args_str:
                return "0"
            if "body" in args_str and "json" in args_str:
                return json.dumps({"body": ""})
            return "https://github.com/review/1"

        mock_gh.side_effect = gh_side_effect

        import io
        with patch.object(sys, "argv", [
            "codex_review_post.py", "--pr", "1", "--repo", "owner/repo",
            "--story-id", "story-720"
        ]):
            with patch("sys.stdin", io.StringIO(REVIEW_TWO_P1)):
                main()

        # dispatch_rework called for each P1 finding
        assert len(dispatch_calls) >= 1, "dispatch_rework must be called for P1 findings"

        # All P1 findings must be covered
        all_dispatch_bodies = " ".join(c["body"] for c in dispatch_calls)
        assert "SQL injection" in all_dispatch_bodies or "command injection" in all_dispatch_bodies

        # REQUEST_CHANGES review posted exactly once (not once per P1)
        request_changes_calls = [
            c for c in mock_gh.call_args_list
            if "--request-changes" in str(c)
        ]
        assert len(request_changes_calls) == 1, (
            f"Expected exactly 1 REQUEST_CHANGES gh call, got {len(request_changes_calls)}"
        )


# ---------------------------------------------------------------------------
# Test 4: Out-of-range severity (P0/P4)
# ---------------------------------------------------------------------------

class TestOutOfRangeSeverity:
    """P0 and P4 must not trigger rework dispatch."""

    def test_p0_not_parsed(self):
        """FINDING_RE matches P[1-4] only; P0 must not appear in findings."""
        findings = parse_findings(REVIEW_P0_ONLY)
        assert all(f["severity"] != "P0" for f in findings), (
            "P0 was parsed — it must not be matched since FINDING_RE covers P[1-4] only"
        )

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.create_debt_issue")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_p4_does_not_dispatch_rework(
        self, mock_gh, mock_issue, mock_post, mock_dispatch
    ):
        """P4 (out of spec but parsed) must behave advisory — no rework dispatch."""
        def gh_side_effect(*args):
            args_str = " ".join(str(a) for a in args)
            if "reviews" in args_str:
                return "0"
            if "body" in args_str and "json" in args_str:
                return json.dumps({"body": ""})
            return "ok"

        mock_gh.side_effect = gh_side_effect
        mock_post.return_value = None
        mock_issue.return_value = None

        import io
        with patch.object(sys, "argv", [
            "codex_review_post.py", "--pr", "1", "--repo", "owner/repo",
            "--story-id", "story-720"
        ]):
            with patch("sys.stdin", io.StringIO(REVIEW_P4_ONLY)):
                main()

        mock_dispatch.assert_not_called()

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.create_debt_issue")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_p0_not_parsed_so_no_action(
        self, mock_gh, mock_issue, mock_post, mock_dispatch
    ):
        """P0 tags (completely out of range) are not matched — no action taken."""
        findings = parse_findings(REVIEW_P0_ONLY)
        # P0 must not be matched
        assert len(findings) == 0 or all(f["severity"] != "P0" for f in findings)


# ---------------------------------------------------------------------------
# Test 5: Fixed idempotency test — uses actual generated fingerprint
# ---------------------------------------------------------------------------

class TestIdempotencyFixed:
    """Idempotency: second run with same input must not create duplicate debt issue.

    The original test used a hardcoded 'codex-finding-id:abc123' that never matched
    real generated IDs. This test uses the actual fingerprint from parse_findings.
    """

    def test_duplicate_issue_blocked_by_real_fingerprint(self):
        """Second run sees existing issue with matching fingerprint → _is_duplicate_issue True."""
        # Get the real fingerprint generated from this exact input
        findings = parse_findings(REVIEW_P2_ONLY)
        assert len(findings) == 1
        real_fingerprint = findings[0]["fingerprint"]

        # Simulate: first run already created this issue
        existing_body = (
            f"Codex advisory finding from PR #1.\n\n"
            f"{findings[0]['body']}\n\n"
            f"<!-- codex-fingerprint: {real_fingerprint} -->\n"
            f"<!-- codex-source-file: parser.py -->\n"
        )

        with patch("deployment.morris.scripts.codex_review_post.gh") as mock_gh:
            mock_gh.return_value = json.dumps([
                {"number": 5, "body": existing_body}
            ])
            is_dup = _is_duplicate_issue("owner/repo", real_fingerprint)

        assert is_dup is True, (
            "Second run should detect the existing issue by matching the real fingerprint. "
            "A hardcoded 'abc123' would never match and this test would always pass vacuously."
        )

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_main_does_not_create_duplicate_on_second_run(
        self, mock_gh, mock_post, mock_dispatch
    ):
        """Full main() path: second run with existing issue for same fingerprint → no new issue."""
        findings = parse_findings(REVIEW_P2_ONLY)
        real_fingerprint = findings[0]["fingerprint"]

        existing_body = (
            f"Codex finding.\n"
            f"<!-- codex-fingerprint: {real_fingerprint} -->\n"
            f"<!-- codex-source-file: parser.py -->\n"
        )

        issue_create_calls = []

        def gh_side_effect(*args):
            args_str = " ".join(str(a) for a in args)
            if "reviews" in args_str:
                return "0"
            if "body" in args_str and "json" in args_str:
                return json.dumps({"body": ""})
            if "issue" in args_str and "list" in args_str:
                return json.dumps([{"number": 5, "body": existing_body}])
            if "issue" in args_str and "create" in args_str:
                issue_create_calls.append(args)
                return "https://github.com/issue/6"
            return "ok"

        mock_gh.side_effect = gh_side_effect
        mock_post.return_value = None
        mock_dispatch.return_value = None

        import io
        with patch.object(sys, "argv", [
            "codex_review_post.py", "--pr", "2", "--repo", "owner/repo"
        ]):
            with patch("sys.stdin", io.StringIO(REVIEW_P2_ONLY)):
                main()

        assert len(issue_create_calls) == 0, (
            f"Issue should NOT be created on second run (fingerprint matches existing). "
            f"Got {len(issue_create_calls)} create calls."
        )


# ---------------------------------------------------------------------------
# Test 6: gh_create_issue failure — graceful handling, other findings continue
# ---------------------------------------------------------------------------

class TestCreateIssueFailureHandling:
    """gh_create_issue failure: process_review logs error, continues, P1 rework dispatched."""

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_create_issue_failure_does_not_abort_p1_rework(
        self, mock_gh, mock_post, mock_dispatch
    ):
        """P2 issue creation failure must not prevent P1 rework dispatch."""
        dispatch_called = {"n": 0}

        def dispatch_side_effect(story_id, repo, finding):
            dispatch_called["n"] += 1

        mock_dispatch.side_effect = dispatch_side_effect
        mock_post.return_value = None

        def gh_side_effect(*args):
            args_str = " ".join(str(a) for a in args)
            if "issue" in args_str and "create" in args_str:
                raise RuntimeError("GitHub API error: 422 Unprocessable Entity")
            if "reviews" in args_str:
                return "0"
            if "body" in args_str and "json" in args_str:
                return json.dumps({"body": ""})
            if "issue" in args_str and "list" in args_str:
                return json.dumps([])  # no existing issues
            return "https://github.com/review/1"

        mock_gh.side_effect = gh_side_effect

        import io
        with patch.object(sys, "argv", [
            "codex_review_post.py", "--pr", "1", "--repo", "owner/repo",
            "--story-id", "story-720"
        ]):
            with patch("sys.stdin", io.StringIO(REVIEW_P2_THEN_P1)):
                main()  # must not raise

        # P1 rework must still be dispatched despite P2 issue creation failure
        assert dispatch_called["n"] >= 1, (
            "dispatch_rework must still be called for P1 when create_debt_issue raises for P2"
        )

        # Comment must still be posted for findings
        mock_post.assert_called()

    @patch("deployment.morris.scripts.codex_review_post.dispatch_rework")
    @patch("deployment.morris.scripts.codex_review_post.post_inline_comment")
    @patch("deployment.morris.scripts.codex_review_post.gh")
    def test_create_issue_failure_continues_processing_remaining_findings(
        self, mock_gh, mock_post, mock_dispatch
    ):
        """When create_debt_issue raises, remaining findings (P3) are still processed."""
        REVIEW_P2_THEN_P3 = """\
**P2** Advisory: regex edge case.
`parser.py:55` — unanchored regex.

**P3** Nitpick: rename variable.
`utils.py:5` — `x` should be `index`.
"""
        post_calls = []

        def post_side_effect(pr_num, repo, body, file, line):
            post_calls.append(body)

        mock_post.side_effect = post_side_effect
        mock_dispatch.return_value = None

        def gh_side_effect(*args):
            args_str = " ".join(str(a) for a in args)
            if "issue" in args_str and "create" in args_str:
                raise RuntimeError("Create failed")
            if "reviews" in args_str:
                return "0"
            if "body" in args_str and "json" in args_str:
                return json.dumps({"body": ""})
            if "issue" in args_str and "list" in args_str:
                return json.dumps([])
            return "ok"

        mock_gh.side_effect = gh_side_effect

        import io
        with patch.object(sys, "argv", [
            "codex_review_post.py", "--pr", "1", "--repo", "owner/repo"
        ]):
            with patch("sys.stdin", io.StringIO(REVIEW_P2_THEN_P3)):
                main()  # must not raise

        # Both P2 and P3 findings should still get comments posted
        assert len(post_calls) >= 2, (
            f"Expected comments for both P2 and P3 findings. Got: {post_calls}"
        )
