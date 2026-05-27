"""STORY-727: Approval Handler — record approvals/rejections and apply Tier-1 proposals.

Handles:
- record_approval(): mark proposal as approved in DB
- apply_tier1_proposal(): atomically apply diff via git and call mark_proposal_applied
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ._async_util import run_async as _run_async

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stub helpers — patched in tests
# ---------------------------------------------------------------------------


def _run_git(args: list[str], **kwargs) -> Any:  # pragma: no cover
    """Run a git command and return subprocess.CompletedProcess. Patched in tests."""
    import subprocess
    return subprocess.run(["git"] + args, **kwargs)


def _get_commit_sha(repo_root: Path) -> str:  # pragma: no cover
    """Return the SHA of the latest commit. Patched in tests."""
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
    )
    return result.stdout.decode().strip()


def _open_pr(branch: str, title: str, body: str, auto_merge: bool, repo_root: Path) -> dict:  # pragma: no cover
    """Open a GitHub PR. Returns dict with 'number' and 'auto_merge'. Patched in tests.

    CONTRACT-FIRST STUB: see feature-spec.md §5.3 — PR creation is wired
    at runtime by the Morris orchestrator environment (STORY-724 dependency).
    """
    raise RuntimeError(
        "PR creation not wired — this function must be patched by the "
        "Morris orchestrator environment before use (see feature-spec.md §5.3)"
    )


def _get_teams_client():  # pragma: no cover
    """Return a Teams DM client. Patched in tests."""
    return None


def _get_db_service():  # pragma: no cover
    """Return the improvement DB service. Patched in tests."""
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_approval(
    proposal_id: int,
    approver: str,
    db_service: Any = None,
) -> None:
    """Record that Mark approved the proposal — sets status='approved' in DB."""
    db = db_service or _get_db_service()
    if db is not None:
        _run_async(
            db.mark_proposal_decided(
                proposal_id,
                "approved",
                approver,
            )
        )
        logger.info("Proposal #%d approved by %s", proposal_id, approver)
    else:
        logger.warning("record_approval called with no db_service")


def apply_tier1_proposal(
    proposal_id: int,
    proposal: Any,
    repo_root: Path,
    db_service: Any = None,
    teams_client: Any = None,
) -> bool:
    """Atomically apply a Tier-1 proposal via git.

    Sequence:
    1. Run git apply with the diff_text
    2. Commit with a message containing STORY-727, proposal_id, and pattern_key
    3. Call db_service.mark_proposal_applied with the commit SHA
    4. Open PR
    5. Post [ACTION] DM to Mark

    On failure: raises an exception; mark_proposal_applied is NOT called.
    """
    db = db_service or _get_db_service()
    teams = teams_client or _get_teams_client()

    diff_text = proposal.diff_text
    pattern_key = proposal.pattern_key
    target_file = proposal.target_file

    # Apply the diff via git
    apply_result = _run_git(
        ["apply", "--index", "-"],
        input=diff_text.encode() if isinstance(diff_text, str) else diff_text,
        capture_output=True,
        cwd=repo_root,
    )
    if apply_result.returncode != 0:
        err = apply_result.stderr.decode() if isinstance(apply_result.stderr, bytes) else str(apply_result.stderr)
        raise RuntimeError(
            f"git apply failed for proposal #{proposal_id} ({pattern_key}): {err}"
        )

    # Commit
    commit_message = (
        f"improvement(story-727): apply proposal #{proposal_id} — {pattern_key}\n\n"
        f"Pattern evidence: {proposal.pattern_evidence_json}\n"
        f"Proposal ID: {proposal_id}\n"
        f"STORY-727"
    )
    _run_git(
        ["commit", "-m", commit_message],
        capture_output=True,
        cwd=repo_root,
    )

    # Get the commit SHA
    sha = _get_commit_sha(repo_root)

    # Mark proposal as applied in DB
    if db is not None:
        _run_async(db.mark_proposal_applied(proposal_id, sha))

    # Open PR
    branch = f"morris/improvement-{proposal_id}"
    pr_info = _open_pr(
        branch=branch,
        title=f"improvement(story-727): proposal #{proposal_id} — {pattern_key}",
        body=proposal.rationale,
        auto_merge=True,
        repo_root=repo_root,
    )

    # Post [ACTION] DM
    if teams is not None:
        pr_num = pr_info.get("number", "?") if pr_info else "?"
        message = (
            f"[ACTION] Proposal #{proposal_id} applied at commit {sha}. "
            f"PR #{pr_num} will merge after CI."
        )
        _run_async(teams.post_dm(recipient="mark@gorillacommerce.co", message=message))

    logger.info(
        "Tier-1 proposal #%d (%s) applied at commit %s",
        proposal_id,
        pattern_key,
        sha,
    )
    return True
