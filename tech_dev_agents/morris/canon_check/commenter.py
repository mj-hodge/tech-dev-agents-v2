"""Idempotent comment + commit-status helpers for Morris canon-check.

Uses an injectable `Runner` protocol that wraps `subprocess.run` so unit
tests can run without the `gh` binary present.

Contracts (DO NOT CHANGE without coordinating with STORY-1007):
  - COMMENT_MARKER  = "<!-- morris-canon-check:v1 -->"
  - STATUS_CHECK_NAME = "morris/canon-check"  (imported from checker)
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
from typing import Any, Optional, Protocol

from tech_dev_agents.morris.canon_check.checker import (
    STATUS_CHECK_NAME,
    CanonCheckResult,
)

# Idempotency token — first line of every comment Morris posts via this
# skill. STORY-1007 may grep for this exact marker.
COMMENT_MARKER = "<!-- morris-canon-check:v1 -->"


class Runner(Protocol):
    """Protocol for an executor of argv → (rc, stdout, stderr)."""

    def run(self, argv: list[str]) -> tuple[int, str, str]: ...


class _SubprocessRunner:
    """Default production runner — wraps subprocess.run."""

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return proc.returncode, proc.stdout, proc.stderr


class RunnerError(RuntimeError):
    """Raised when an external command exits non-zero unexpectedly."""


# ---------------------------------------------------------------------------
# Comment rendering
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_comment(result: CanonCheckResult, *, now_iso: Optional[str] = None) -> str:
    """Render the Morris canon-check PR comment body.

    First line is ALWAYS the marker — idempotency depends on it.
    """
    ts = now_iso or _now_iso()
    if result.status == "FAILURE":
        return _render_drift_body(result, ts)
    if result.status == "SUCCESS":
        return _render_clean_body(result, ts)
    # PENDING / NA — caller shouldn't render these; safety fallback.
    return f"{COMMENT_MARKER}\n## Morris canon-check — status: {result.status}\n"


def _render_drift_body(result: CanonCheckResult, ts: str) -> str:
    lines: list[str] = [
        COMMENT_MARKER,
        "## Morris canon-check — DRIFT DETECTED",
        "",
        f"Canonical files in this PR drift from "
        f"`gc-data-v2/pipeline-template/{result.scaffold_ref}`:",
        "",
    ]
    for d in result.drifted:
        lines.append(f"### `{d.path}` — {d.drift_type}")
        if d.diff_snippet:
            lines.append("```diff")
            lines.append(d.diff_snippet)
            lines.append("```")
        lines.append("**Remediation:**")
        lines.append("```")
        lines.append(d.remediation)
        lines.append("```")
        lines.append("")
    lines += [
        "---",
        "*Posted by Morris (Engineering Manager). Re-runs on every poll cycle.*",
        f"*Last checked: {ts}. Status check `{STATUS_CHECK_NAME}` will remain "
        "FAILURE until all drifts are resolved.*",
    ]
    return "\n".join(lines)


def _render_clean_body(result: CanonCheckResult, ts: str) -> str:
    return (
        f"{COMMENT_MARKER}\n"
        f"## Morris canon-check — CLEAN\n\n"
        f"All canonical files match "
        f"`gc-data-v2/pipeline-template/{result.scaffold_ref}` as of {ts}.\n\n"
        f"*Status check `{STATUS_CHECK_NAME}` set to SUCCESS.*\n"
    )


# ---------------------------------------------------------------------------
# Existing-comment discovery
# ---------------------------------------------------------------------------


def find_existing_comment(
    repo: str, pr_number: int, runner: Optional[Runner] = None
) -> Optional[int]:
    """Return the id of an existing Morris canon-check comment, if any."""
    r = runner or _SubprocessRunner()
    rc, stdout, _ = r.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "comments",
        ]
    )
    if rc != 0:
        return None
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    for c in payload.get("comments") or []:
        if not isinstance(c, dict):
            continue
        author = (c.get("author") or {}).get("login", "")
        body = c.get("body", "") or ""
        if author == "morris-bot" and COMMENT_MARKER in body:
            cid = c.get("id")
            if isinstance(cid, int):
                return cid
            if isinstance(cid, str) and cid.isdigit():
                return int(cid)
    return None


# ---------------------------------------------------------------------------
# Posting (idempotent)
# ---------------------------------------------------------------------------


def post_drift_comment(
    result: CanonCheckResult,
    *,
    runner: Optional[Runner] = None,
) -> dict[str, Any]:
    """Post or update the Morris canon-check comment on a PR.

    Behaviour by status:
      - NA       → no-op (returns action='skipped')
      - PENDING  → no-op (status check is posted separately)
      - SUCCESS  → if an existing comment exists, update it to the CLEAN
                   body so the audit trail closes; otherwise no comment.
      - FAILURE  → render drift body; PATCH existing comment if present,
                   else POST a new one.
    """
    if result.status == "NA":
        return {"action": "skipped", "reason": result.skipped_reason or "non_v2_repo"}
    if result.status == "PENDING":
        return {"action": "skipped", "reason": "pending"}

    r = runner or _SubprocessRunner()
    body = render_comment(result)
    existing = find_existing_comment(result.repo, result.pr_number, runner=r)

    # SUCCESS path: only touch the PR if there was a prior drift comment.
    if result.status == "SUCCESS" and existing is None:
        return {"action": "noop", "reason": "clean_and_no_prior_comment"}

    if existing is not None:
        # PATCH the existing comment.
        endpoint = f"repos/{result.repo}/issues/comments/{existing}"
        rc, stdout, stderr = r.run(
            [
                "gh",
                "api",
                "-X",
                "PATCH",
                endpoint,
                "-f",
                f"body={body}",
            ]
        )
        if rc != 0:
            raise RunnerError(f"gh api PATCH failed: {stderr.strip()}")
        return {"action": "updated", "comment_id": existing, "stdout": stdout}

    # POST a new comment via `gh pr comment`.
    rc, stdout, stderr = r.run(
        [
            "gh",
            "pr",
            "comment",
            str(result.pr_number),
            "--repo",
            result.repo,
            "--body",
            body,
        ]
    )
    if rc != 0:
        raise RunnerError(f"gh pr comment failed: {stderr.strip()}")
    return {"action": "posted", "stdout": stdout}


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------


_VALID_STATES = {"success", "failure", "pending", "error"}


def update_status_check(
    repo: str,
    sha: str,
    state: str,
    description: str,
    *,
    runner: Optional[Runner] = None,
    target_url: Optional[str] = None,
) -> dict[str, Any]:
    """Set the `morris/canon-check` commit-status on `sha`."""
    if state not in _VALID_STATES:
        raise ValueError(f"invalid status state: {state!r}; expected one of {_VALID_STATES}")
    r = runner or _SubprocessRunner()
    # Truncate description to GitHub's 140-char limit
    desc = (description or "")[:140]
    endpoint = f"repos/{repo}/statuses/{sha}"
    argv = [
        "gh",
        "api",
        "-X",
        "POST",
        endpoint,
        "-f",
        f"state={state}",
        "-f",
        f"context={STATUS_CHECK_NAME}",
        "-f",
        f"description={desc}",
    ]
    if target_url:
        argv += ["-f", f"target_url={target_url}"]
    rc, stdout, stderr = r.run(argv)
    return {
        "rc": rc,
        "stdout": stdout,
        "stderr": stderr,
        "context": STATUS_CHECK_NAME,
        "state": state,
        "sha": sha,
    }
