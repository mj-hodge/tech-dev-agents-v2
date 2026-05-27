"""Tests for codex_debt_autoclose.py — STORY-720 Phase 7 (RED state).

Tests for auto-closing codex-debt GitHub issues when flagged code is removed.
All GitHub API calls (gh CLI) are mocked.
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test (will fail in RED state — expected)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deployment.morris.scripts.codex_debt_autoclose import (
    fingerprint_present_in_file,
    get_pr_deleted_files,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_ISSUE_BODY = """\
Codex advisory finding from PR #10.

Unanchored regex may match partial strings in `parser.py`.

<!-- codex-fingerprint: {fingerprint} -->
<!-- codex-source-file: src/parser.py -->
"""

SAMPLE_CODE = """\
import re

def parse(text):
    pattern = re.compile(r'\\d+')
    return pattern.findall(text)

# More code here
x = 1
y = 2
"""


def make_fingerprint(content: str) -> str:
    """Make a sha256 fingerprint of a 500-char window of content."""
    return hashlib.sha256(content[:500].encode()).hexdigest()


# ---------------------------------------------------------------------------
# TC-08: Auto-close when file is deleted
# ---------------------------------------------------------------------------

class TestAutoCloseFileDeleted:
    """TC-08: Open codex-debt issue references file X; PR deletes file X → issue closed."""

    @patch("subprocess.run")
    def test_deleted_file_closes_issue(self, mock_run, tmp_path):
        """When the referenced file is deleted, the issue should be auto-closed."""
        # The file does NOT exist on disk (simulating deletion)
        source_file = str(tmp_path / "src" / "parser.py")
        # Note: file intentionally not created — it's "deleted"

        fingerprint = make_fingerprint(SAMPLE_CODE)
        issue_body = SAMPLE_ISSUE_BODY.format(fingerprint=fingerprint).replace(
            "src/parser.py", source_file
        )

        issues = [{"number": 42, "title": "[codex-debt] parser issue", "body": issue_body}]

        call_log = []

        def run_side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            cmd_str = " ".join(str(c) for c in cmd)
            if "issue" in cmd_str and "list" in cmd_str:
                result.stdout = json.dumps(issues) + "\n"
            elif "pr" in cmd_str and "diff" in cmd_str:
                result.stdout = source_file + "\n"
            elif "issue" in cmd_str and "comment" in cmd_str:
                call_log.append(("comment", cmd))
                result.stdout = "ok\n"
            elif "issue" in cmd_str and "close" in cmd_str:
                call_log.append(("close", cmd))
                result.stdout = "ok\n"
            else:
                result.stdout = "ok\n"
            return result

        mock_run.side_effect = run_side_effect

        from deployment.morris.scripts.codex_debt_autoclose import (
            FINGERPRINT_RE, SOURCE_FILE_RE, fingerprint_present_in_file, gh
        )

        # Simulate the main loop logic
        deleted_files = {source_file}

        for issue in issues:
            body = issue.get("body", "")
            fp_match = FINGERPRINT_RE.search(body)
            file_match = SOURCE_FILE_RE.search(body)

            assert fp_match is not None
            fp = fp_match.group(1)
            src = file_match.group(1).strip() if file_match else None

            close_reason = None
            if src and src in deleted_files:
                close_reason = f"Auto-closed: file `{src}` deleted in PR #42."
            elif src and not fingerprint_present_in_file(src, fp):
                close_reason = f"Auto-closed: flagged code removed in PR #42."

            assert close_reason is not None, "Should have detected deleted file"
            assert "deleted" in close_reason

    @patch("subprocess.run")
    def test_get_pr_deleted_files_returns_nonexistent(self, mock_run, tmp_path):
        """get_pr_deleted_files should return files listed in diff that don't exist."""
        existing = tmp_path / "alive.py"
        existing.write_text("# alive")
        deleted_path = str(tmp_path / "deleted.py")  # not created

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"{existing}\n{deleted_path}\n",
            stderr=""
        )

        deleted = get_pr_deleted_files(99, "owner/repo")
        assert deleted_path in deleted
        assert str(existing) not in deleted


# ---------------------------------------------------------------------------
# TC-09: Auto-close when fingerprinted code is removed
# ---------------------------------------------------------------------------

class TestAutoCloseCodeRemoved:
    """TC-09: Fingerprinted code gone from file → issue auto-closes."""

    def test_fingerprint_not_present_in_modified_file(self, tmp_path):
        """After code is removed/replaced, fingerprint should not match."""
        original_code = SAMPLE_CODE
        fp = make_fingerprint(original_code)

        # Write a modified file without the original code
        modified = tmp_path / "parser.py"
        modified.write_text("# Completely rewritten\ndef new_parse(text):\n    return []\n")

        result = fingerprint_present_in_file(str(modified), fp)
        assert result is False

    def test_auto_close_when_code_removed(self, tmp_path):
        """Issue should close when fingerprint no longer present in file."""
        original_code = SAMPLE_CODE
        fp = make_fingerprint(original_code)

        # File exists but code is rewritten
        source_file = tmp_path / "parser.py"
        source_file.write_text("# Completely rewritten\ndef new_parse(text):\n    return []\n")

        issue_body = f"""Codex finding.

<!-- codex-fingerprint: {fp} -->
<!-- codex-source-file: {source_file} -->
"""
        from deployment.morris.scripts.codex_debt_autoclose import FINGERPRINT_RE, SOURCE_FILE_RE

        fp_match = FINGERPRINT_RE.search(issue_body)
        file_match = SOURCE_FILE_RE.search(issue_body)

        assert fp_match is not None
        assert file_match is not None

        extracted_fp = fp_match.group(1)
        extracted_file = file_match.group(1).strip()

        deleted_files = set()  # file not deleted, just modified
        close_reason = None

        if extracted_file in deleted_files:
            close_reason = "deleted"
        elif not fingerprint_present_in_file(extracted_file, extracted_fp):
            close_reason = f"Auto-closed: flagged code removed."

        assert close_reason is not None
        assert "removed" in close_reason

    @patch("subprocess.run")
    def test_main_closes_issue_on_code_removal(self, mock_run, tmp_path):
        """Full main() path: code removed → gh issue comment + gh issue close called."""
        original_code = SAMPLE_CODE
        fp = make_fingerprint(original_code)

        # Modified file (code removed)
        source_file = tmp_path / "parser.py"
        source_file.write_text("# Rewritten\n")

        issue_body = f"""Codex finding.

<!-- codex-fingerprint: {fp} -->
<!-- codex-source-file: {source_file} -->
"""
        issues_json = json.dumps([{"number": 7, "title": "[codex-debt] regex", "body": issue_body}])

        close_calls = []

        def run_side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            cmd_str = " ".join(str(c) for c in cmd)
            if "issue" in cmd_str and "list" in cmd_str:
                result.stdout = issues_json + "\n"
            elif "pr" in cmd_str and "diff" in cmd_str:
                result.stdout = ""  # no deleted files in diff
            elif "issue" in cmd_str and "comment" in cmd_str:
                close_calls.append("comment")
                result.stdout = "ok\n"
            elif "issue" in cmd_str and "close" in cmd_str:
                close_calls.append("close")
                result.stdout = "ok\n"
            else:
                result.stdout = "ok\n"
            return result

        mock_run.side_effect = run_side_effect

        import sys
        with patch.object(sys, "argv", ["codex_debt_autoclose.py", "--pr", "5", "--repo", "owner/repo"]):
            main()

        assert "comment" in close_calls
        assert "close" in close_calls


# ---------------------------------------------------------------------------
# TC-10: Auto-close NEGATIVE — same code, different line → stays open
# ---------------------------------------------------------------------------

class TestAutoCloseNegative:
    """TC-10: Fingerprinted code present (line shifted) → issue stays open."""

    def test_fingerprint_present_in_file_same_content(self, tmp_path):
        """Fingerprint matches even when code is at a different line."""
        original_code = SAMPLE_CODE
        fp = make_fingerprint(original_code)

        # Write the same code (possibly with extra leading lines)
        source_file = tmp_path / "parser.py"
        preamble = "# Copyright header\n# More header\n\n"
        source_file.write_text(preamble + original_code)

        # Fingerprint should still be found (different line, same content window)
        result = fingerprint_present_in_file(str(source_file), fp)
        assert result is True

    def test_issue_not_closed_when_fingerprint_present(self, tmp_path):
        """When fingerprint found in file, close_reason should be None."""
        original_code = SAMPLE_CODE
        fp = make_fingerprint(original_code)

        # Same code at a different line (code was moved but not removed)
        source_file = tmp_path / "parser.py"
        source_file.write_text("# Added header\n\n" + original_code)

        issue_body = f"""Codex finding.

<!-- codex-fingerprint: {fp} -->
<!-- codex-source-file: {source_file} -->
"""
        from deployment.morris.scripts.codex_debt_autoclose import FINGERPRINT_RE, SOURCE_FILE_RE

        fp_match = FINGERPRINT_RE.search(issue_body)
        file_match = SOURCE_FILE_RE.search(issue_body)

        extracted_fp = fp_match.group(1)
        extracted_file = file_match.group(1).strip()

        deleted_files = set()  # file not deleted
        close_reason = None

        if extracted_file in deleted_files:
            close_reason = "deleted"
        elif not fingerprint_present_in_file(extracted_file, extracted_fp):
            close_reason = "removed"

        assert close_reason is None, "Issue should remain open when code is unchanged"

    @patch("subprocess.run")
    def test_main_does_not_close_when_code_unchanged(self, mock_run, tmp_path):
        """Full main() path: code present at different line → no close calls."""
        original_code = SAMPLE_CODE
        fp = make_fingerprint(original_code)

        # Same code, just with a preamble (line shifted)
        source_file = tmp_path / "parser.py"
        source_file.write_text("# Header\n\n" + original_code)

        issue_body = f"""Codex finding.

<!-- codex-fingerprint: {fp} -->
<!-- codex-source-file: {source_file} -->
"""
        issues_json = json.dumps([{"number": 99, "title": "[codex-debt] test", "body": issue_body}])

        close_calls = []

        def run_side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            cmd_str = " ".join(str(c) for c in cmd)
            if "issue" in cmd_str and "list" in cmd_str:
                result.stdout = issues_json + "\n"
            elif "pr" in cmd_str and "diff" in cmd_str:
                result.stdout = ""
            elif "issue" in cmd_str and "comment" in cmd_str:
                close_calls.append("comment")
                result.stdout = "ok\n"
            elif "issue" in cmd_str and "close" in cmd_str:
                close_calls.append("close")
                result.stdout = "ok\n"
            else:
                result.stdout = "ok\n"
            return result

        mock_run.side_effect = run_side_effect

        import sys
        with patch.object(sys, "argv", ["codex_debt_autoclose.py", "--pr", "5", "--repo", "owner/repo"]):
            main()

        assert close_calls == [], f"Issue should not be closed, got: {close_calls}"


# ---------------------------------------------------------------------------
# Additional helper unit tests
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    """Unit tests for low-level helper functions."""

    def test_fingerprint_present_file_not_exists(self, tmp_path):
        fp = "a" * 64
        result = fingerprint_present_in_file(str(tmp_path / "nonexistent.py"), fp)
        assert result is False

    def test_fingerprint_present_exact_match(self, tmp_path):
        content = "x = 1\ny = 2\nz = 3\n" * 10
        fp = make_fingerprint(content)
        f = tmp_path / "code.py"
        f.write_text(content)
        assert fingerprint_present_in_file(str(f), fp) is True

    def test_fingerprint_not_present_different_content(self, tmp_path):
        original = "original content " * 30
        modified = "completely different content " * 30
        fp = make_fingerprint(original)
        f = tmp_path / "code.py"
        f.write_text(modified)
        assert fingerprint_present_in_file(str(f), fp) is False

    @patch("subprocess.run")
    def test_gh_helper_in_autoclose(self, mock_run):
        """gh helper raises on non-zero return code."""
        from deployment.morris.scripts.codex_debt_autoclose import gh
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with pytest.raises(RuntimeError, match="gh failed"):
            gh("issue", "list")
