"""
STORY-626: Verify test-results/ is excluded from git tracking.

These tests confirm that:
1. `.gitignore` contains a `test-results/` entry
2. No test-results/ files are tracked in the git index
3. The gitignore pattern is a directory pattern (trailing slash)

RED state: All tests fail because `.gitignore` does not yet contain `test-results/`.
Phase 8 will add the entry and make these GREEN.
"""

import subprocess
from pathlib import Path

import pytest

# Repo root is two levels up from tests/deployment/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


class TestGitignoreTestResults:
    """Verify test-results/ artifacts are excluded from version control."""

    def test_gitignore_contains_test_results_entry(self):
        """AC3: .gitignore must contain a test-results/ line to prevent
        future Playwright artifacts from being committed."""
        # Arrange
        assert GITIGNORE_PATH.exists(), f".gitignore not found at {GITIGNORE_PATH}"
        content = GITIGNORE_PATH.read_text()

        # Act — parse non-comment, non-blank lines
        entries = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        # Assert
        assert any(
            entry in ("test-results/", "test-results")
            for entry in entries
        ), (
            ".gitignore does not contain a 'test-results/' entry. "
            "Playwright artifacts will be committed again."
        )

    def test_no_test_results_files_tracked_in_git(self):
        """AC1+AC2: No test-results/ files should appear in the git index.
        After `git rm -r test-results/`, `git ls-files` must return nothing."""
        # Act
        result = subprocess.run(
            ["git", "ls-files", "test-results/"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=15,
        )

        # Assert — output should be empty (no tracked files)
        tracked_files = result.stdout.strip()
        assert tracked_files == "", (
            f"test-results/ files are still tracked in git:\n{tracked_files}"
        )

    def test_gitignore_entry_is_directory_pattern(self):
        """AC3: The gitignore entry should be 'test-results/' (with trailing
        slash) to match the directory, not just files named 'test-results'."""
        # Arrange
        assert GITIGNORE_PATH.exists()
        content = GITIGNORE_PATH.read_text()

        # Act
        entries = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        # Assert — must have trailing slash for directory pattern
        assert "test-results/" in entries, (
            ".gitignore should contain 'test-results/' (with trailing slash) "
            "to match the directory pattern. Found entries: "
            + ", ".join(entries)
        )
