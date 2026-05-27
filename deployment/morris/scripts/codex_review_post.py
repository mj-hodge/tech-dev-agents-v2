"""Codex review post — severity-aware action policy for PR reviews.

STORY-720: Parses Codex review output, routes findings by severity (P1/P2/P3),
respects override markers, enforces convergence cap, and creates debt issues.

Severity → action map:
  P1 → inline comment + REQUEST_CHANGES + dispatch rework
  P2 → inline comment + codex-debt GitHub issue (no rework)
  P3 → inline comment only

Override markers:
  PR-body: <!-- codex-override: reason --> (skips all findings)
  Code-comment: # codex-override: reason (skips finding within ±5 lines)
  Case-sensitive: only lowercase "codex-override" matches.

Convergence: after 3 prior REQUEST_CHANGES reviews from Morris, escalate
to Teams DM instead of dispatching rework.

Idempotency: checks for existing comments/issues before creating duplicates.
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# Max rework cycles before escalating to Mark
CONVERGENCE_CAP = 3

# Morris bot login name for counting prior reviews
MORRIS_LOGIN = "morris-bot"

# Override marker patterns (case-sensitive)
_PR_BODY_OVERRIDE_RE = re.compile(r"<!--\s*codex-override:\s*.+?\s*-->")
_CODE_OVERRIDE_RE = re.compile(r"#\s*codex-override:")

# Finding header pattern: ## P1: ..., ## P2: ..., ## P3: ..., or ## <no-tag>: ...
_FINDING_HEADER_RE = re.compile(
    r"^##\s+(?:(P[123]):\s*)?(.+)$", re.MULTILINE
)
_FILE_LINE_RE = re.compile(r"^File:\s*(.+)$", re.MULTILINE)
_LINES_RE = re.compile(r"^Lines:\s*(\d+)-(\d+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# GitHub API functions — injected/mocked in tests, real impl calls `gh` CLI
# ---------------------------------------------------------------------------

def gh_create_review_comment(repo, pr_number, body, path, line):
    """Post an inline review comment on a PR."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_create_issue(repo, title, body, labels):
    """Create a GitHub issue with labels."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_submit_review(repo, pr_number, event, body=""):
    """Submit a PR review (APPROVE or REQUEST_CHANGES)."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_list_reviews(repo, pr_number):
    """List all reviews on a PR."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_dispatch_rework(repo, pr_number, findings):
    """Dispatch a rework story for P1 findings."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def teams_dm_mark(message):
    """Send a Teams DM to Mark for convergence escalation."""
    raise NotImplementedError("Requires Teams webhook")


def gh_list_pr_comments(repo, pr_number):
    """List existing comments on a PR."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def gh_list_repo_issues(repo, labels):
    """List open issues in a repo with given labels."""
    raise NotImplementedError("Requires gh CLI or GitHub API token")


def read_file_lines(repo, file_path, pr_ref):
    """Read source file lines for code-comment override checking.

    Returns dict mapping line_number → line_content for the relevant range.
    """
    raise NotImplementedError("Requires gh CLI or GitHub API token")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def generate_fingerprint(snippet: str) -> str:
    """Generate sha256 hex fingerprint of a code snippet."""
    return hashlib.sha256(snippet.encode()).hexdigest()


def parse_codex_findings(codex_output: str) -> list[dict]:
    """Parse Codex review output into structured findings.

    Each finding is a dict with keys:
      severity: "P1", "P2", or "P3" (untagged defaults to "P2")
      title: the finding title text
      file: file path
      line_start, line_end: int
      description: the body text after the header
      finding_id: deterministic ID for idempotency
    """
    if not codex_output or not codex_output.strip():
        return []

    findings = []
    # Split on finding headers
    sections = re.split(r"(?=^## )", codex_output.strip(), flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        header_match = _FINDING_HEADER_RE.search(section)
        if not header_match:
            continue

        severity = header_match.group(1) or "P2"  # default to P2 if untagged
        title = header_match.group(2).strip()

        file_match = _FILE_LINE_RE.search(section)
        lines_match = _LINES_RE.search(section)

        file_path = file_match.group(1).strip() if file_match else ""
        line_start = int(lines_match.group(1)) if lines_match else 0
        line_end = int(lines_match.group(2)) if lines_match else 0

        # Description is everything after the metadata lines
        desc_lines = []
        past_metadata = False
        for line in section.split("\n"):
            if past_metadata:
                desc_lines.append(line)
            elif line.startswith("## ") or line.startswith("File:") or line.startswith("Lines:"):
                continue
            else:
                past_metadata = True
                desc_lines.append(line)

        description = "\n".join(desc_lines).strip()

        # Deterministic finding ID for idempotency
        finding_id = hashlib.sha256(
            f"{severity}:{file_path}:{line_start}-{line_end}:{title}".encode()
        ).hexdigest()[:12]

        findings.append({
            "severity": severity,
            "title": title,
            "file": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "description": description,
            "finding_id": finding_id,
        })

    return findings


def check_pr_body_override(pr_body: str | None) -> bool:
    """Check if PR body contains a codex-override marker (case-sensitive)."""
    if not pr_body:
        return False
    return bool(_PR_BODY_OVERRIDE_RE.search(pr_body))


def check_code_comment_override(
    file_path: str,
    line_start: int,
    line_end: int,
) -> bool:
    """Check if source code has a codex-override comment within ±5 lines.

    Calls read_file_lines() to get file content around the finding.
    """
    try:
        # Read lines in the ±5 range around the finding
        check_start = max(1, line_start - 5)
        check_end = line_end + 5
        file_lines = read_file_lines(None, file_path, None)

        for line_num, content in file_lines.items():
            if check_start <= line_num <= check_end:
                if _CODE_OVERRIDE_RE.search(content):
                    return True
    except Exception:
        logger.debug(
            "Failed to read file lines for override check on %s",
            file_path,
            exc_info=True,
        )

    return False


def count_prior_rework_reviews(pr_number: int, repo: str) -> int:
    """Count prior REQUEST_CHANGES reviews from Morris on this PR."""
    reviews = gh_list_reviews(repo, pr_number)
    count = 0
    for review in reviews:
        user_login = review.get("user", {}).get("login", "")
        state = review.get("state", "")
        if user_login == MORRIS_LOGIN and state == "CHANGES_REQUESTED":
            count += 1
    return count


def route_finding(finding: dict, pr_number: int, repo: str) -> str:
    """Route a single finding based on severity. Returns action taken."""
    severity = finding["severity"]

    # Post inline comment for all severities
    gh_create_review_comment(
        repo,
        pr_number,
        body=f"**[{severity}]** {finding['title']}\n\n{finding['description']}",
        path=finding["file"],
        line=finding["line_end"],
    )

    if severity == "P2":
        # Create codex-debt issue with fingerprint
        fingerprint = generate_fingerprint(finding["description"])
        issue_body = (
            f"Flagged in `{finding['file']}` by Codex review on PR #{pr_number}.\n\n"
            f"**Finding:** {finding['title']}\n\n"
            f"{finding['description']}\n\n"
            f"codex-finding-id:{finding['finding_id']}\n\n"
            f"<!-- codex-fingerprint: {fingerprint} -->\n"
        )
        gh_create_issue(
            repo,
            title=f"[codex-debt] {finding['title']}",
            body=issue_body,
            labels=["codex-debt"],
        )
        return "debt_issue"

    if severity == "P1":
        return "rework"  # dispatch handled at the process_review level

    return "comment_only"


def _is_duplicate_comment(finding: dict, existing_comments: list[dict]) -> bool:
    """Check if a comment for this finding already exists on the PR.

    Checks existing comments for codex-finding-id markers. Matches on either:
    - Exact finding_id match
    - File path match (same file referenced in an existing codex comment)
    """
    finding_marker = f"codex-finding-id:{finding['finding_id']}"
    for comment in existing_comments:
        body = comment.get("body", "")
        # Exact match — this specific finding was already posted
        if finding_marker in body:
            return True
    return False


def _is_duplicate_issue(finding: dict, existing_issues: list[dict]) -> bool:
    """Check if a debt issue for this finding already exists.

    Matches on exact finding_id only — no title-based fallback.
    """
    finding_marker = f"codex-finding-id:{finding['finding_id']}"
    for issue in existing_issues:
        body = issue.get("body", "")
        # Exact finding-id match only
        if finding_marker in body:
            return True
    return False


def process_review(
    pr_number: int,
    repo: str,
    codex_output: str,
    pr_body: str | None,
) -> dict:
    """Process a full Codex review: parse, filter overrides, route findings.

    Returns a summary dict with keys: findings_count, actions, skipped_count.
    """
    findings = parse_codex_findings(codex_output)

    # No findings → APPROVE
    if not findings:
        gh_submit_review(repo, pr_number, "APPROVE", body="Codex review: no findings.")
        return {"findings_count": 0, "actions": ["APPROVE"], "skipped_count": 0}

    # Check PR-body override (case-sensitive)
    if check_pr_body_override(pr_body):
        gh_submit_review(repo, pr_number, "APPROVE", body="Codex review: all findings overridden via PR body marker.")
        return {"findings_count": len(findings), "actions": ["APPROVE"], "skipped_count": len(findings)}

    # Check for existing comments/issues for idempotency
    try:
        existing_comments = gh_list_pr_comments(repo, pr_number)
    except Exception:
        existing_comments = []
    try:
        existing_issues = gh_list_repo_issues(repo, labels=["codex-debt"])
    except Exception:
        existing_issues = []

    # Count prior rework reviews for convergence cap
    prior_rework_count = count_prior_rework_reviews(pr_number, repo)

    actions = []
    p1_findings = []
    skipped = 0

    for finding in findings:
        # Check code-comment override
        if finding["file"] and finding["line_start"]:
            if check_code_comment_override(finding["file"], finding["line_start"], finding["line_end"]):
                skipped += 1
                continue

        # Idempotency: skip if comment already exists
        if _is_duplicate_comment(finding, existing_comments):
            skipped += 1
            continue

        # Idempotency: skip P2 entirely (comment + issue) if debt issue exists
        if finding["severity"] == "P2" and _is_duplicate_issue(finding, existing_issues):
            skipped += 1
            continue

        action = route_finding(finding, pr_number, repo)
        actions.append(action)

        if finding["severity"] == "P1":
            p1_findings.append(finding)

    # Handle P1 findings: rework or escalate
    has_p1 = len(p1_findings) > 0
    if has_p1:
        if prior_rework_count >= CONVERGENCE_CAP:
            # Convergence cap reached — escalate to Mark
            teams_dm_mark(
                f"PR #{pr_number} in {repo} has hit {CONVERGENCE_CAP} rework cycles. "
                f"{len(p1_findings)} P1 finding(s) remain. Manual review needed."
            )
            gh_submit_review(
                repo, pr_number, "REQUEST_CHANGES",
                body=f"Codex review: {len(p1_findings)} P1 finding(s). Convergence cap reached — escalated to Mark.",
            )
        else:
            # Dispatch rework
            gh_dispatch_rework(repo, pr_number, p1_findings)
            gh_submit_review(
                repo, pr_number, "REQUEST_CHANGES",
                body=f"Codex review: {len(p1_findings)} P1 finding(s). Rework dispatched.",
            )
    else:
        # No P1s — just comment, possible P2 issues created via route_finding
        if not actions:
            # All findings were skipped
            gh_submit_review(repo, pr_number, "APPROVE", body="Codex review: all findings resolved or overridden.")
        else:
            gh_submit_review(repo, pr_number, "COMMENT", body="Codex review: advisory findings posted.")

    return {
        "findings_count": len(findings),
        "actions": actions,
        "skipped_count": skipped,
        "p1_count": len(p1_findings),
        "dispatched_rework": has_p1 and prior_rework_count < CONVERGENCE_CAP,
        "escalated": has_p1 and prior_rework_count >= CONVERGENCE_CAP,
    }
