"""Codex debt issue auto-close on PR merge.

STORY-720: When a PR merges, scans codex-debt issues in the repo. If the
flagged file was deleted or the fingerprinted code snippet was removed,
auto-closes the issue. Content-based (fingerprint), not line-number-based.

Fingerprint is a sha-256 of the original flagged code snippet, stored in the
issue body as: <!-- codex-fingerprint: <sha256-hex> -->
"""

from __future__ import annotations

import hashlib
import re

# Pattern to extract fingerprint from issue body
_FINGERPRINT_RE = re.compile(r"<!--\s*codex-fingerprint:\s*([a-f0-9]{64})\s*-->")

# Pattern to extract file path from issue body: "Flagged in `path`"
_FLAGGED_FILE_RE = re.compile(r"Flagged in `([^`]+)`")


# ---------------------------------------------------------------------------
# GitHub API functions — injected/mocked in tests
# ---------------------------------------------------------------------------

def gh_list_repo_issues(repo, labels):
    """List open issues in a repo with given labels."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_close_issue_with_comment(issue_number, comment, repo=None):
    """Close a GitHub issue with a comment explaining why."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_get_pr_diff(repo, pr_number):
    """Get the diff of a merged PR. Returns dict with 'files' list."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_get_file_content(repo, file_path, ref="HEAD"):
    """Get the current content of a file in the repo."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def extract_fingerprint_from_issue(body: str) -> str | None:
    """Extract codex-fingerprint from issue body HTML comment.

    Returns the sha256 hex string, or None if not found.
    """
    match = _FINGERPRINT_RE.search(body)
    if match:
        return match.group(1)
    return None


def _extract_flagged_file(body: str) -> str | None:
    """Extract the flagged file path from issue body."""
    match = _FLAGGED_FILE_RE.search(body)
    if match:
        return match.group(1)
    return None


def check_fingerprint_in_file(file_content: str, fingerprint: str) -> bool:
    """Check if the fingerprinted code snippet is still in the file.

    We can't reverse a sha256, so we check all possible contiguous
    sub-sequences of 5-10 lines. Instead, we use a simpler approach:
    compute fingerprints of every possible window of lines and compare.

    For efficiency, since we stored the fingerprint at creation time,
    we just hash every window of N lines (5 through 10) and check for match.
    """
    lines = file_content.split("\n")
    for window_size in range(5, min(len(lines) + 1, 11)):
        for start in range(len(lines) - window_size + 1):
            window = "\n".join(lines[start:start + window_size])
            # Try with and without trailing newline
            for suffix in ("", "\n"):
                candidate = window + suffix
                if hashlib.sha256(candidate.encode()).hexdigest() == fingerprint:
                    return True
    return False


def process_merged_pr(pr_number: int, repo: str) -> dict:
    """Process a merged PR: find codex-debt issues, auto-close resolved ones.

    For each open codex-debt issue:
    1. If the flagged file was deleted in this PR → close
    2. If the flagged file was modified and the fingerprint is gone → close
    3. Otherwise → leave open

    Returns summary dict with closed_count and checked_count.
    """
    # Get open codex-debt issues
    issues = gh_list_repo_issues(repo, labels=["codex-debt"])
    if not issues:
        return {"closed_count": 0, "checked_count": 0}

    # Get PR diff
    diff = gh_get_pr_diff(repo, pr_number)
    changed_files = {f["filename"]: f["status"] for f in diff.get("files", [])}

    if not changed_files:
        return {"closed_count": 0, "checked_count": len(issues)}

    closed_count = 0

    for issue in issues:
        body = issue.get("body", "")
        issue_number = issue["number"]

        # Extract fingerprint — skip if missing
        fingerprint = extract_fingerprint_from_issue(body)
        if not fingerprint:
            continue

        # Extract flagged file
        flagged_file = _extract_flagged_file(body)
        if not flagged_file:
            continue

        # Check if this file was changed in the PR
        if flagged_file not in changed_files:
            continue

        file_status = changed_files[flagged_file]

        if file_status == "removed":
            # File deleted → close issue
            gh_close_issue_with_comment(
                issue_number,
                f"Auto-closed: file deleted in PR #{pr_number}.",
            )
            closed_count += 1
            continue

        # File was modified — check if fingerprinted code is still present
        file_content = gh_get_file_content(repo, flagged_file)
        if not check_fingerprint_in_file(file_content, fingerprint):
            # Fingerprinted code removed → close
            gh_close_issue_with_comment(
                issue_number,
                f"Auto-closed: flagged code removed in PR #{pr_number}.",
            )
            closed_count += 1

    return {"closed_count": closed_count, "checked_count": len(issues)}
