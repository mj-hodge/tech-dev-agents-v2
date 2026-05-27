"""Dispatch queue polling loop — v2 protocol (atomic claim + lease tokens).

Epic-Queue-v2, Story Q2 — v2 poller module.

This module is the v2 replacement for dispatch_poller.py.  It is ONLY activated
when DISPATCH_PROTOCOL=v2 is set in the environment.  Both code paths coexist
during the cutover window; see deployment/hermes/run_dispatch_poller.py for the
selector that picks v1 vs v2 based on the env var.

Key differences from v1:
  - Single atomic /claim-next endpoint (no separate /next + /claim round-trip).
  - Lease token required on every mutation (heartbeat, release, transition).
  - X-Worker-Version header required; 426 response → sys.exit(2) so systemd
    restarts with the correct image.
  - Heartbeat carries {git_head_sha, current_phase, phase_started_at, last_test_status}
    for the Q8 self-healing watcher.
  - No TypeError fallbacks (strict typing — no exception shims).

Environment variables:
  OPS_CONSOLE_URL         — Base URL of ops console API (required)
  OPS_CONSOLE_API_KEY     — API key for auth (required)
  AGENT_NAME              — Agent identifier (required)
  AGENT_ROLE              — Agent role, default 'developer'
  WORKER_VERSION          — Semantic version sent in X-Worker-Version, default '2.0'
  DISPATCH_PROTOCOL       — Must be 'v2' for this poller to activate (checked by selector)
  DISPATCH_POLL_INTERVAL  — Seconds between polls when idle, default 60
  DISPATCH_LEASE_RENEW_INTERVAL — Seconds between heartbeats, default 300 (5 min)
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import requests

logger = logging.getLogger("dispatch_poller_v2")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SDK_TOOL_PATH = "/opt/agent/claude_sdk_tool.py"
PYTHON_PATH = sys.executable or "python3"
DISPATCH_PROTOCOL = os.environ.get("DISPATCH_PROTOCOL", "v1")
V2_BASE = "/api/dispatch/v2"

# STORY-857: lease-token state file. Written after every successful claim,
# deleted after release / transition. drain_leases.py (ExecStop) reads it to
# release any in-flight lease before SIGKILL. /var/run requires root to
# create — fall back to /tmp on agents where hermes can't write to /var/run.
ACTIVE_LEASE_PRIMARY_PATH = "/var/run/dispatch-poller/active_lease.json"
ACTIVE_LEASE_FALLBACK_PATH = "/tmp/dispatch-poller-active-lease.json"

# Sidecar file the persona writes its current "last_action" to. Picked up by
# the heartbeat thread on each tick. Deliberately a single file (not per-story)
# because each agent VM only ever holds one lease at a time. Personas write
# short strings like "committed abc123 (Phase 8)" or "drafting test cases".
LAST_ACTION_PATH_DEFAULT = "/tmp/dispatch-last-action.txt"


def _active_lease_path() -> str:
    """Return the path to use for the active-lease state file.

    Prefer /var/run/dispatch-poller/active_lease.json; if the parent dir does
    not exist or is not writable as the running user, fall back to /tmp.
    Tests can override via the ACTIVE_LEASE_PATH env var.
    """
    override = os.environ.get("ACTIVE_LEASE_PATH")
    if override:
        return override
    primary_dir = os.path.dirname(ACTIVE_LEASE_PRIMARY_PATH)
    try:
        if os.path.isdir(primary_dir) and os.access(primary_dir, os.W_OK):
            return ACTIVE_LEASE_PRIMARY_PATH
    except OSError:
        pass
    return ACTIVE_LEASE_FALLBACK_PATH


def _write_active_lease(claim: "_ActiveClaim") -> None:
    """Persist the active claim to disk so drain_leases.py can release it.

    Best-effort: any I/O failure is logged but does not abort the poller.
    """
    path = _active_lease_path()
    payload = {
        "job_id": claim.job_id,
        "lease_token": claim.lease_token,
        "story_id": claim.story_id,
        "repo": claim.repo,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError as exc:  # pragma: no cover - best-effort logging
        logger.warning(
            "dispatch_poller_v2: failed to write active-lease state %s: %s",
            path, exc,
        )


def _clear_active_lease() -> None:
    """Remove the active-lease state file. Idempotent."""
    path = _active_lease_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:  # pragma: no cover - best-effort logging
        logger.warning(
            "dispatch_poller_v2: failed to remove active-lease state %s: %s",
            path, exc,
        )


# STORY-857: SIGTERM coordination. systemd sends SIGTERM to the main pid on
# `systemctl restart`. Without a handler, the SDK subprocess keeps running
# and the lease orphans. We track the active subprocess + claim so the
# signal handler can terminate the SDK and release the lease before exit.
_active_sdk_proc: "subprocess.Popen | None" = None  # type: ignore[type-arg]
_active_claim_for_signal: "_ActiveClaim | None" = None
_active_session_for_signal: "requests.Session | None" = None
_active_headers_for_signal: "dict[str, str] | None" = None
_sigterm_received = False


def _handle_sigterm(signum: int, frame) -> None:  # noqa: ARG001
    """SIGTERM handler — release the active lease, kill SDK child, exit cleanly.

    systemd's TimeoutStopSec gives us the wall clock to do this; the ExecStop
    drain script is a second line of defence.
    """
    global _sigterm_received
    _sigterm_received = True
    logger.warning(
        "dispatch_poller_v2: SIGTERM received — releasing lease and shutting down"
    )

    proc = _active_sdk_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("dispatch_poller_v2: SDK terminate failed: %s", exc)

    claim = _active_claim_for_signal
    session = _active_session_for_signal
    headers = _active_headers_for_signal
    if claim is not None and session is not None and headers is not None:
        try:
            release_claim(
                claim,
                session=session,
                headers=headers,
                reason="sigterm_shutdown",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("dispatch_poller_v2: SIGTERM release failed: %s", exc)
    _clear_active_lease()
    sys.exit(0)


def _install_sigterm_handler() -> None:
    """Install the SIGTERM handler. Idempotent — safe to call multiple times."""
    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError) as exc:  # pragma: no cover - non-main-thread
        logger.warning("dispatch_poller_v2: cannot install SIGTERM handler: %s", exc)


# ---------------------------------------------------------------------------
# Worker version gate
# ---------------------------------------------------------------------------


def handle_426_response(*, min_required: str, got: str | None) -> None:
    """Handle a 426 Upgrade Required response from the ops console.

    Logs a structured error and exits with code 2 so systemd triggers a
    controlled restart.  The restart is expected to pick up a newer image
    that satisfies the MIN_WORKER_VERSION contract.

    This function NEVER returns — it always raises SystemExit(2).
    """
    logger.critical(
        "dispatch_poller_v2: worker version rejected by server "
        "[action=exit_for_upgrade min_required=%s got=%s exit_code=2]",
        min_required,
        got,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _build_headers(worker_version: str, agent_name: str) -> dict[str, str]:
    """Build the standard request headers for every v2 API call."""
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    return {
        "X-API-Key": api_key,
        "X-Worker-Version": worker_version,
        "X-Agent-Name": agent_name,
        "X-Agent-Role": os.environ.get("AGENT_ROLE", "developer"),
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return os.environ.get("OPS_CONSOLE_URL", "http://localhost:8005").rstrip("/")


def _post(path: str, payload: dict, *, session: requests.Session, headers: dict) -> requests.Response:
    """POST to ops-console v2 endpoint."""
    url = f"{_base_url()}{V2_BASE}{path}"
    return session.post(url, json=payload, headers=headers, timeout=30)


def _get(path: str, *, session: requests.Session, headers: dict, params: dict | None = None) -> requests.Response:
    """GET from ops-console v2 endpoint."""
    url = f"{_base_url()}{V2_BASE}{path}"
    return session.get(url, headers=headers, params=params, timeout=30)


# ---------------------------------------------------------------------------
# Claim state
# ---------------------------------------------------------------------------


class _ActiveClaim:
    """Holds the state of a currently-held lease."""

    def __init__(
        self,
        job_id: str,
        lease_token: str,
        expires_at: str,
        repo: str,
        story_id: str,
        prompt: str,
        scope: str,
        rework_of: str | None = None,
        # STORY-860: extended fields for orchestration
        branch: str | None = None,
        target_pr: str | None = None,
        correlation_key: str | None = None,
        parent_job_id: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.lease_token = lease_token
        self.expires_at = expires_at
        self.repo = repo
        self.story_id = story_id
        self.prompt = prompt
        self.scope = scope
        self.rework_of = rework_of
        # STORY-860: orchestration metadata
        self.branch = branch
        self.target_pr = target_pr
        self.correlation_key = correlation_key
        self.parent_job_id = parent_job_id
        self.claimed_at = time.monotonic()
        self.current_phase: str | None = None
        self.phase_started_at: str | None = None
        self.last_test_status: str | None = None
        self.last_action: str | None = None


# ---------------------------------------------------------------------------
# Core poll / claim / heartbeat
# ---------------------------------------------------------------------------


def claim_next(
    *,
    session: requests.Session,
    headers: dict,
    preferred_scope: str = "small",
) -> _ActiveClaim | None:
    """Call /claim-next and return an _ActiveClaim or None (204 = nothing to do).

    On 426: calls handle_426_response (which sys.exit(2)s).
    On other errors: logs and returns None.
    """
    resp = _post("/claim-next", {"capabilities": [], "preferred_scope": preferred_scope},
                 session=session, headers=headers)

    if resp.status_code == 426:
        body = resp.json()
        handle_426_response(
            min_required=body.get("min_required", "unknown"),
            got=body.get("got"),
        )

    if resp.status_code == 204:
        return None  # Nothing eligible

    if resp.status_code == 409:
        logger.warning("claim_next: 409 from server — already holds a lease: %s", resp.text)
        return None

    if resp.status_code != 200:
        logger.error("claim_next: unexpected status %s: %s", resp.status_code, resp.text)
        return None

    data = resp.json()
    # STORY-860: parse metadata fields for orchestration
    metadata = data.get("metadata") or {}
    return _ActiveClaim(
        job_id=data["job_id"],
        lease_token=data["lease_token"],
        expires_at=data["expires_at"],
        repo=data["repo"],
        story_id=data["story_id"],
        prompt=data["prompt"],
        scope=data["scope"],
        rework_of=data.get("rework_of"),
        branch=data.get("branch") or metadata.get("branch"),
        target_pr=data.get("target_pr") or metadata.get("target_pr"),
        correlation_key=data.get("correlation_key"),
        parent_job_id=data.get("parent_job_id"),
    )


def _last_action_path() -> str:
    """Return the sidecar path the persona writes last_action to.

    Override via DISPATCH_LAST_ACTION_PATH for tests / non-default agents.
    """
    return os.environ.get("DISPATCH_LAST_ACTION_PATH", LAST_ACTION_PATH_DEFAULT)


def _read_last_action() -> str | None:
    """Read the persona-written last_action sidecar, return short trimmed text.

    Returns None if the file is missing or empty. Truncates to 500 chars so
    a runaway persona write can't bloat the heartbeat payload. Never raises:
    sidecar I/O must not break heartbeating.
    """
    path = _last_action_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if not text:
        return None
    return text[:500]


def send_heartbeat(
    claim: _ActiveClaim,
    *,
    session: requests.Session,
    headers: dict,
    git_head_sha: str | None = None,
) -> bool:
    """Send a heartbeat for the active claim.

    Returns True if the lease was renewed, False if stale (409).
    """
    payload: dict = {
        "job_id": claim.job_id,
        "lease_token": claim.lease_token,
    }
    # Extended heartbeat fields (Q8 self-healing watcher)
    if git_head_sha:
        payload["git_head_sha"] = git_head_sha
    if claim.current_phase:
        payload["current_phase"] = claim.current_phase
    if claim.phase_started_at:
        payload["phase_started_at"] = claim.phase_started_at
    if claim.last_test_status:
        payload["last_test_status"] = claim.last_test_status

    # Sidecar last_action takes precedence over claim attribute. The persona
    # updates this between every commit / phase transition; reading on every
    # tick keeps the queue current without churn.
    sidecar_action = _read_last_action()
    if sidecar_action:
        claim.last_action = sidecar_action
    if claim.last_action:
        payload["last_action"] = claim.last_action

    resp = _post("/heartbeat", payload, session=session, headers=headers)

    if resp.status_code == 426:
        body = resp.json()
        handle_426_response(
            min_required=body.get("min_required", "unknown"),
            got=body.get("got"),
        )

    if resp.status_code == 409:
        logger.warning(
            "heartbeat: stale lease for job_id=%s [action=drop_claim]",
            claim.job_id,
        )
        return False

    if resp.status_code == 200:
        data = resp.json()
        claim.expires_at = data["expires_at"]
        return True

    logger.error("heartbeat: unexpected status %s for job_id=%s", resp.status_code, claim.job_id)
    return False


def release_claim(
    claim: _ActiveClaim,
    *,
    session: requests.Session,
    headers: dict,
    reason: str = "released",
) -> None:
    """Release the active claim back to work_queue."""
    resp = _post(
        "/release",
        {"job_id": claim.job_id, "lease_token": claim.lease_token, "reason": reason},
        session=session,
        headers=headers,
    )
    if resp.status_code == 426:
        body = resp.json()
        handle_426_response(min_required=body.get("min_required", "unknown"), got=body.get("got"))
    if resp.status_code not in (200, 409):
        logger.error("release: unexpected status %s for job_id=%s", resp.status_code, claim.job_id)


def transition_claim(
    claim: _ActiveClaim,
    *,
    event_type: str,
    event_data: dict,
    session: requests.Session,
    headers: dict,
) -> bool:
    """Emit a transition event for the active claim.

    Returns True on success, False on stale lease (409) or error.
    """
    resp = _post(
        "/transition",
        {
            "job_id": claim.job_id,
            "lease_token": claim.lease_token,
            "event_type": event_type,
            "event_data": event_data,
        },
        session=session,
        headers=headers,
    )
    if resp.status_code == 426:
        body = resp.json()
        handle_426_response(min_required=body.get("min_required", "unknown"), got=body.get("got"))
    if resp.status_code == 200:
        return True
    logger.error(
        "transition: status=%s event_type=%s job_id=%s: %s",
        resp.status_code, event_type, claim.job_id, resp.text,
    )
    return False


# ---------------------------------------------------------------------------
# SDK execution
# ---------------------------------------------------------------------------


def _get_current_git_sha(workspace_dir: str | None = None) -> str | None:
    """Return the current git HEAD SHA in the workspace, or None on error."""
    cwd = workspace_dir or os.path.expanduser("~/workspace")
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _resolve_workspace(repo: str) -> str:
    """Resolve the on-disk path to the cloned repo.

    Mirrors v1 fallback order (dispatch_poller.py:861-867). v2 originally
    only tried ~/workspace/<repo> which didn't exist on prod agents (they
    clone to ~/dev/hpi-gorillacommerce/<repo>). Restoring the v1 list.
    """
    candidates = [
        os.path.expanduser(f"~/workspace/{repo}"),
        os.path.expanduser(f"~/dev/hpi-gorillacommerce/{repo}"),
    ]
    for p in candidates:
        if os.path.isdir(p):
            return p
    # Fallback: home dir; SDK will clone if missing
    return os.path.expanduser("~")


# ---------------------------------------------------------------------------
# STORY-859: Rebase workspace prep
#
# RCA: v2 poller has no orchestration step before SDK launch. Rebase / rework
# / fix-PR prompts hit dirty workspaces, SDK fails, and the failure is
# misclassified as `phase_runner_crash` (retryable, max_attempts=3) — burning
# 3 retries before reaching attention queue.
#
# Fix: detect rebase-shaped prompts and run fetch + checkout + pull --rebase
# BEFORE launching the SDK. On rebase failure, emit a typed
# `git_rebase_failed` event (non-retryable, attention queue) and skip SDK
# launch entirely. Diagnostic JSON is written workspace-local so an operator
# can inspect the conflict.
#
# Default-branch resolution is dynamic (git symbolic-ref) — never hardcoded
# "main". Mirrors deployment/hermes/sdlc_phase_runner.py:_resolve_default_branch
# (STORY-759/STORY-760). Inlined here to avoid importing sdlc_phase_runner
# (heavy side effects).
#
# Codex-review fixes (STORY-859 remediation):
#   CRITICAL: branch_unresolved fail-fast — if _extract_story_branch returns
#     None for a rebase-mode job, fail immediately with prep_failure_kind=
#     "branch_unresolved". Never run git pull --rebase on an unresolved branch.
#   HIGH: rebase abort on pull failure — after git pull --rebase exits non-zero,
#     run git rebase --abort to clear lingering .git/rebase-* state. If abort
#     itself fails, mark workspace_tainted=True in the diagnostic.
#   MEDIUM: prep_failure_kind field in every failure diagnostic so the poll_loop
#     caller can map workspace_missing → failure_class="workspace_missing"
#     (non-retryable, attention_queue) instead of conflating it with
#     git_rebase_failed.
# ---------------------------------------------------------------------------


_REBASE_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brebase\s+only\b", re.IGNORECASE),
    re.compile(r"\brebase\s+pr\s*#\d+", re.IGNORECASE),
    re.compile(r"\brework\s+of\s+story-\d+\b", re.IGNORECASE),
    re.compile(r"\bfix\s+pr\s*#\d+", re.IGNORECASE),
    # Auto-redispatch prompts may not include "Rebase PR #..." phrasing.
    re.compile(r"\bgit_rebase_failed\b", re.IGNORECASE),
)

# Branch name validator: prevents shell-metacharacter injection from prompt-derived branch.
_VALID_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")

# Branch extraction from rebase / rework prompts. Examples:
#   "Rebase PR #123 (story-100/work) onto main..."  → story-100/work
#   "Rework of STORY-169 (PR #312): fix..."         → story-169/work (synthesized)
_BRANCH_EXTRACT_RE = re.compile(r"\((story-\d+/[A-Za-z0-9_./-]+)\)", re.IGNORECASE)
_STORY_NUM_RE = re.compile(r"story-(\d+)", re.IGNORECASE)
_PR_NUMBER_RE = re.compile(r"\bPR\s*#?\s*(\d+)\b", re.IGNORECASE)


def _is_rebase_prompt(prompt: str) -> bool:
    """Return True if `prompt` matches one of the four rebase trigger patterns.

    Patterns (case-insensitive):
      - "Rebase only"
      - "Rebase PR #<N>"
      - "Rework of STORY-<N>"
      - "Fix PR #<N>"   (must include the `#`)
    """
    if not prompt:
        return False
    return any(p.search(prompt) for p in _REBASE_PROMPT_PATTERNS)


def _resolve_default_branch_for_workspace(workspace: str) -> str | None:
    """Resolve the default branch via `git symbolic-ref refs/remotes/origin/HEAD`.

    Falls back through `ls-remote main` then `ls-remote master`. Returns None
    if all probes fail (caller treats as `default_branch_unresolvable`). Mirrors
    sdlc_phase_runner._resolve_default_branch (STORY-759).
    """
    sym_ref = subprocess.run(
        ["git", "-C", workspace, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if sym_ref.returncode == 0 and sym_ref.stdout.strip():
        ref = sym_ref.stdout.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return ref[len(prefix):]

    ls_main = subprocess.run(
        ["git", "-C", workspace, "ls-remote", "--heads", "origin", "main"],
        capture_output=True, text=True, timeout=15,
    )
    if ls_main.returncode == 0 and ls_main.stdout.strip():
        return "main"

    ls_master = subprocess.run(
        ["git", "-C", workspace, "ls-remote", "--heads", "origin", "master"],
        capture_output=True, text=True, timeout=15,
    )
    if ls_master.returncode == 0 and ls_master.stdout.strip():
        return "master"

    return None


def _extract_story_branch(prompt: str, story_id: str) -> str | None:
    """Pull the branch name from the prompt; fall back to story-<N>/work.

    Returns None if no story number can be extracted at all. For rebase-mode
    jobs this should never happen because they always reference a story — but
    if it does the caller must fail-fast (branch_unresolved) rather than
    running pull --rebase on whatever branch is currently checked out.
    """
    # 1. Direct parenthesized reference: "(story-100/work)"
    m = _BRANCH_EXTRACT_RE.search(prompt)
    if m:
        candidate = m.group(1).lower()
        if _VALID_BRANCH_RE.match(candidate):
            return candidate

    # 2. Synthesize from story_id or prompt's STORY-N reference.
    snum = None
    sm = _STORY_NUM_RE.search(story_id or "")
    if sm:
        snum = sm.group(1)
    else:
        sm = _STORY_NUM_RE.search(prompt)
        if sm:
            snum = sm.group(1)

    if snum:
        candidate = f"story-{snum}/work"
        if _VALID_BRANCH_RE.match(candidate):
            return candidate

    return None


def _extract_pr_number_from_output(output: str) -> int | None:
    """Extract PR number from SDK output text (e.g. 'PR #341', 'PR 341')."""
    if not output:
        return None
    m = _PR_NUMBER_RE.search(output)
    if not m:
        return None
    try:
        n = int(m.group(1))
        return n if n >= 1 else None
    except Exception:
        return None


_GH_ORG = "hpi-gorillacommerce"
_GH_API_BASE = "https://api.github.com"


def _lookup_pr_by_branch(
    *,
    story_id: str,
    repo: str,
    branch: str | None = None,
) -> int | None:
    """Query the GitHub REST API for a PR matching this story's branch.

    STORY-903: Fallback PR detection for when the regex fast path misses
    (e.g. agent output contains a bare GitHub URL rather than 'PR #N' text).

    Resolution order for the branch name:
      1. Use `branch` arg directly if provided (from _ActiveClaim.branch,
         populated by STORY-860 orchestration metadata).
      2. Derive `story-{N}/work` from story_id via _STORY_NUM_RE. If the
         story_id contains no STORY-N pattern, skip the HTTP call and return
         None immediately (no safe branch to query).

    Queries:
      GET https://api.github.com/repos/{org}/{repo}/pulls
          ?head={org}:{branch}&state=all&per_page=5

    Returns the most recent PR's number (by created_at), or None if:
      - GITHUB_TOKEN is absent
      - No STORY-N number can be derived from story_id (and no branch given)
      - HTTP 4xx / 5xx
      - Network error
      - Empty result list
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.warning(
            "dispatch_poller_v2: gh REST fallback skipped — GITHUB_TOKEN not set"
            " [story_id=%s]",
            story_id,
        )
        return None

    # Resolve the branch to query
    if not branch:
        m = _STORY_NUM_RE.search(story_id or "")
        if not m:
            logger.warning(
                "dispatch_poller_v2: gh REST fallback skipped — cannot derive branch"
                " from story_id=%r (no STORY-N pattern)",
                story_id,
            )
            return None
        branch = f"story-{m.group(1)}/work"

    head_param = f"{_GH_ORG}:{branch}"
    qs = urllib.parse.urlencode(
        {"head": head_param, "state": "all", "per_page": "5"}
    )
    url = f"{_GH_API_BASE}/repos/{_GH_ORG}/{repo}/pulls?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.warning(
            "dispatch_poller_v2: gh REST fallback HTTP %s [story_id=%s branch=%s url=%s]",
            exc.code, story_id, branch, url,
        )
        return None
    except Exception as exc:
        logger.warning(
            "dispatch_poller_v2: gh REST fallback error [story_id=%s branch=%s]: %s",
            story_id, branch, exc,
        )
        return None

    if not isinstance(body, list) or not body:
        logger.debug(
            "dispatch_poller_v2: gh REST fallback empty result [story_id=%s branch=%s]",
            story_id, branch,
        )
        return None

    # Pick the most recent PR by created_at (lexicographic ISO-8601 sort is correct)
    try:
        best = max(body, key=lambda p: p.get("created_at", ""))
        n = int(best["number"])
        if n >= 1:
            return n
        logger.warning(
            "dispatch_poller_v2: gh REST fallback returned invalid pr_number=%s"
            " [story_id=%s]",
            n, story_id,
        )
        return None
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "dispatch_poller_v2: gh REST fallback parse error [story_id=%s]: %s",
            story_id, exc,
        )
        return None


def _success_transition_payload(
    output: str,
    *,
    story_id: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
) -> tuple[str, dict]:
    """Build transition payload for a successful SDK run.

    STORY-903: Three-level PR number resolution:
      1. Regex fast path  — scan SDK output for 'PR #N' / 'PR N' text
      2. GitHub REST fallback — query pulls by branch (requires story_id + repo)
      3. Null → needs_info  — degrades gracefully (STORY-902 backfill territory)

    Both successful paths (regex / REST) return identical event shape:
      ("submitted", {"pr_number": int, "output_summary": str})

    Integrity guard: avoid invalid in_review states by requiring PR linkage
    before emitting `submitted`.

    Logs which path matched via logger.info so operators can track adoption.
    """
    # --- Fast path: regex ---
    pr_number = _extract_pr_number_from_output(output)
    if pr_number is not None:
        logger.info(
            "dispatch_poller_v2: PR detected via regex"
            " [pr=%s story_id=%s path=regex]",
            pr_number, story_id,
        )
        return "submitted", {"pr_number": pr_number, "output_summary": output[:500]}

    # --- Fallback: GitHub REST query (only when caller supplies story_id + repo) ---
    if story_id and repo:
        pr_number = _lookup_pr_by_branch(
            story_id=story_id,
            repo=repo,
            branch=branch,
        )
        if pr_number is not None:
            logger.info(
                "dispatch_poller_v2: PR detected via gh REST fallback"
                " [pr=%s story_id=%s branch=%s path=gh_rest]",
                pr_number, story_id, branch,
            )
            return "submitted", {"pr_number": pr_number, "output_summary": output[:500]}
        logger.warning(
            "dispatch_poller_v2: gh REST fallback returned no PR"
            " [story_id=%s repo=%s branch=%s] — falling through to needs_info",
            story_id, repo, branch,
        )

    # --- Both paths missed ---
    return "needs_info", {
        "kind": "question",
        "question": "Missing PR linkage: unable to detect PR number from successful run output.",
        "reason": "missing_pr_linkage",
        "output_summary": output[:500],
    }


def _write_rebase_diagnostic(workspace: str, payload: dict) -> None:
    """Write the rebase diagnostic JSON workspace-local. Best-effort."""
    path = os.path.join(workspace, "phase_runner_diagnostics.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
    except OSError as exc:  # pragma: no cover - best-effort
        logger.warning(
            "dispatch_poller_v2: failed to write rebase diagnostic %s: %s",
            path, exc,
        )


def _capture_git_status(workspace: str) -> str:
    """Capture `git status --porcelain` for the diagnostic. Best-effort."""
    try:
        result = subprocess.run(
            ["git", "-C", workspace, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except Exception as exc:  # pragma: no cover
        return f"<git status failed: {exc!r}>"


def _build_diagnostic(
    *, command: list[str], result: subprocess.CompletedProcess, workspace: str,
    prep_failure_kind: str,
) -> dict:
    """Construct the diagnostic payload (capped at 4KB per stream).

    The prep_failure_kind field is mandatory — it lets the poll_loop caller
    map workspace_missing to failure_class="workspace_missing" (non-retryable)
    instead of conflating it with git_rebase_failed.

    Valid values: workspace_missing | branch_unresolved |
                  default_branch_unresolvable | git_rebase_conflict |
                  git_fetch_failed | git_checkout_failed
    """
    stdout = (result.stdout or "")[-4096:]
    stderr = (result.stderr or "")[-4096:]
    return {
        "command": command,
        "exit_code": int(result.returncode),
        "stdout": stdout,
        "stderr": stderr,
        "git_status": _capture_git_status(workspace)[-4096:],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prep_failure_kind": prep_failure_kind,
    }


def _prepare_rebase_workspace(claim: "_ActiveClaim") -> tuple[bool, dict | None]:
    """Clean up the workspace before SDK launch for rebase / rework prompts.

    Sequence:
      1. Resolve workspace via _resolve_workspace(claim.repo).
      2. Resolve default branch via `git symbolic-ref refs/remotes/origin/HEAD`
         with fallback through ls-remote main → master.
      3. Extract the story branch name from the prompt; fall back to
         story-<N>/work derived from claim.story_id.
         [CRITICAL] If _extract_story_branch returns None, fail-fast with
         prep_failure_kind="branch_unresolved" — do NOT run git pull --rebase
         on an unknown branch (workspace reuse risk).
      4. Run `git fetch origin`, `git checkout <branch>`, `git pull --rebase
         origin <default-branch>`. Each subprocess: cwd=workspace, 60s timeout,
         capture stdout/stderr.
      5. [HIGH] On pull --rebase failure, run `git rebase --abort` to clear
         lingering .git/rebase-* state before returning the failure diagnostic.

    Returns:
        (True, None) on full success — caller proceeds to _run_sdk.
        (False, diag_payload) on any failure — caller emits the appropriate
        typed event (workspace_missing or git_rebase_failed) and skips SDK
        launch. Diagnostic includes prep_failure_kind for routing.
    """
    workspace = _resolve_workspace(claim.repo)
    if not os.path.isdir(workspace):
        logger.warning(
            "dispatch_poller_v2: rebase prep skipped — workspace missing: %s",
            workspace,
        )
        # workspace_missing is an infra problem, NOT a git conflict.
        # Poll_loop maps prep_failure_kind="workspace_missing" to
        # failure_class="workspace_missing" (non-retryable, attention_queue).
        diag = {
            "command": ["<rebase-prep>", "workspace-missing"],
            "exit_code": -1,
            "stdout": "",
            "stderr": f"workspace not found: {workspace}",
            "git_status": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prep_failure_kind": "workspace_missing",
        }
        return False, diag

    default_branch = _resolve_default_branch_for_workspace(workspace)
    if not default_branch:
        diag = {
            "command": ["git", "-C", workspace, "symbolic-ref", "refs/remotes/origin/HEAD"],
            "exit_code": -1,
            "stdout": "",
            "stderr": "default_branch_unresolvable: symbolic-ref + ls-remote main + ls-remote master all failed",
            "git_status": _capture_git_status(workspace),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prep_failure_kind": "default_branch_unresolvable",
        }
        _write_rebase_diagnostic(workspace, diag)
        return False, diag

    # [CRITICAL] Branch resolution is mandatory for rebase-mode jobs.
    # If _extract_story_branch returns None, the prompt does not contain enough
    # information to identify a branch. Running pull --rebase without an explicit
    # checkout could rewrite history on whatever branch happens to be checked out
    # (main, stale branch from prior job, etc.). Fail-fast instead.
    story_branch = _extract_story_branch(claim.prompt, claim.story_id)
    if story_branch is None:
        diag = {
            "command": ["<rebase-prep>", "branch-resolution"],
            "exit_code": -1,
            "stdout": "",
            "stderr": (
                f"branch_unresolved: could not extract story branch from prompt "
                f"(story_id={claim.story_id!r}). Refusing to run git pull --rebase "
                f"without verified branch checkout."
            ),
            "git_status": _capture_git_status(workspace),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prep_failure_kind": "branch_unresolved",
        }
        _write_rebase_diagnostic(workspace, diag)
        logger.warning(
            "dispatch_poller_v2: rebase prep fail-fast — branch_unresolved "
            "[job_id=%s story_id=%s]",
            claim.job_id, claim.story_id,
        )
        return False, diag

    # Step 1: fetch origin
    fetch_cmd = ["git", "fetch", "origin"]
    fetch = subprocess.run(
        fetch_cmd, cwd=workspace, capture_output=True, text=True, timeout=60,
    )
    if fetch.returncode != 0:
        diag = _build_diagnostic(
            command=fetch_cmd, result=fetch, workspace=workspace,
            prep_failure_kind="git_fetch_failed",
        )
        _write_rebase_diagnostic(workspace, diag)
        return False, diag

    # Step 2: checkout the story branch
    if not _VALID_BRANCH_RE.match(story_branch):
        # Defensive — should never trigger because _extract_story_branch validates
        diag = {
            "command": ["git", "checkout", story_branch],
            "exit_code": -1,
            "stdout": "",
            "stderr": f"refused to checkout branch with invalid characters: {story_branch!r}",
            "git_status": _capture_git_status(workspace),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prep_failure_kind": "git_checkout_failed",
        }
        _write_rebase_diagnostic(workspace, diag)
        return False, diag

    checkout_cmd = ["git", "checkout", story_branch]
    checkout = subprocess.run(
        checkout_cmd, cwd=workspace, capture_output=True, text=True, timeout=60,
    )
    if checkout.returncode != 0:
        diag = _build_diagnostic(
            command=checkout_cmd, result=checkout, workspace=workspace,
            prep_failure_kind="git_checkout_failed",
        )
        _write_rebase_diagnostic(workspace, diag)
        return False, diag

    # Step 3: pull --rebase origin <default_branch>
    pull_cmd = ["git", "pull", "--rebase", "origin", default_branch]
    pull = subprocess.run(
        pull_cmd, cwd=workspace, capture_output=True, text=True, timeout=60,
    )
    if pull.returncode != 0:
        diag = _build_diagnostic(
            command=pull_cmd, result=pull, workspace=workspace,
            prep_failure_kind="git_rebase_conflict",
        )
        _write_rebase_diagnostic(workspace, diag)
        # [HIGH] Clear lingering .git/rebase-* state so subsequent jobs don't
        # find a workspace stuck in mid-rebase. Best-effort: log but don't
        # let abort failure mask the original pull error.
        logger.warning(
            "dispatch_poller_v2: pull --rebase failed [job_id=%s] — running abort",
            claim.job_id,
        )
        try:
            abort_result = subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=workspace, capture_output=True, text=True, timeout=30,
            )
            if abort_result.returncode != 0:
                logger.warning(
                    "dispatch_poller_v2: git rebase --abort also failed "
                    "[job_id=%s rc=%d stderr=%s] — workspace may be tainted",
                    claim.job_id, abort_result.returncode,
                    (abort_result.stderr or "")[-500:],
                )
                diag["workspace_tainted"] = True
                diag["abort_stderr"] = (abort_result.stderr or "")[-1000:]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "dispatch_poller_v2: exception running git rebase --abort "
                "[job_id=%s]: %s — workspace may be tainted",
                claim.job_id, exc,
            )
            diag["workspace_tainted"] = True
            diag["abort_stderr"] = str(exc)
        return False, diag

    logger.info(
        "dispatch_poller_v2: rebase prep complete [job_id=%s repo=%s branch=%s default=%s]",
        claim.job_id, claim.repo, story_branch, default_branch,
    )
    return True, None


def _heartbeat_loop(
    claim: _ActiveClaim,
    proc: subprocess.Popen,
    *,
    session: requests.Session,
    headers: dict,
    interval_sec: int,
    stop_event: threading.Event,
    lease_lost_event: threading.Event | None = None,
) -> None:
    """Background loop: heartbeat every ``interval_sec`` while ``proc`` is alive.

    Runs in a daemon thread spawned by ``_run_sdk``. Without this, the lease
    (15-min TTL) expires during long Phase 8 runs while the SDK is busy and
    the StuckAgentWatcher reclaims the job mid-stride. With it, the lease
    keeps renewing for as long as the SDK process exists and `last_action`
    flows through to the queue's stall view.

    Stops on: SDK exit, stop_event set, or 409 stale-lease (someone else
    already reclaimed). Never raises — heartbeat failures must not impact
    the SDK run.
    """
    while not stop_event.wait(interval_sec):
        if proc.poll() is not None:
            return  # SDK exited
        try:
            ok = send_heartbeat(claim, session=session, headers=headers)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "dispatch_poller_v2: heartbeat-thread error [job_id=%s]: %s",
                claim.job_id, exc,
            )
            continue
        if not ok:
            # 409 stale lease. The poller can't recover; stop heartbeating
            # and signal the progress watchdog so it can SIGTERM the SDK
            # instead of letting it burn tokens for hours on a job that's
            # already been reclaimed. (Pre-watchdog behaviour was to let
            # the SDK finish — that wasted tokens fleet-wide on 2026-05-25.)
            logger.warning(
                "dispatch_poller_v2: heartbeat-thread saw stale lease "
                "[job_id=%s] — stopping ticks", claim.job_id,
            )
            if lease_lost_event is not None:
                lease_lost_event.set()
            return


def _run_sdk(
    claim: _ActiveClaim,
    *,
    session: requests.Session | None = None,
    headers: dict | None = None,
    heartbeat_interval_sec: int = 300,
) -> tuple[bool, str]:
    """Launch the SDK tool for this claim.

    Returns (success: bool, result_summary: str).

    When ``session`` and ``headers`` are provided, a background thread keeps
    the lease alive by heartbeating every ``heartbeat_interval_sec`` seconds.
    Pass None for tests that don't need real heartbeating.
    """
    workspace = _resolve_workspace(claim.repo)
    # v1-compatible SDK invocation. claude_sdk_tool.py only accepts
    # -p PROMPT and -w WORKDIR (plus --max-turns/--resume/--model).
    # Story-id, job-id, lease-token are tracked by this poller process,
    # not passed into the SDK. Lease lifecycle is enforced by the v2
    # lease TTL + StuckAgentWatcher in ops-console.
    cmd = [
        PYTHON_PATH,
        SDK_TOOL_PATH,
        "-p", claim.prompt,
        "-w", workspace,
    ]

    logger.info(
        "dispatch_poller_v2: launching SDK [job_id=%s story_id=%s repo=%s]",
        claim.job_id, claim.story_id, claim.repo,
    )
    global _active_sdk_proc, _active_claim_for_signal
    hb_thread: threading.Thread | None = None
    hb_stop = threading.Event()
    # STORY-WATCHDOG-2026-05-25: lease_lost signals the progress watchdog that
    # the heartbeat thread saw a 409 — watchdog will SIGTERM the SDK rather
    # than let it run silently until proc.communicate's 2h timeout.
    lease_lost_event = threading.Event()
    watchdog = None
    # Single-element list updated by the (future) stdout tailer; the
    # watchdog reads [0] each tick. Initialised to 0 — meaning "no
    # observation yet" — so the silence trip is a no-op until a real
    # tailer wires updates. With SDK_WATCHDOG_SILENCE_MAX_S defaulting
    # to 0 the silence path is fully off; both belt and braces guard
    # against the false-trip path Codex flagged on 2026-05-26.
    last_output_ts: list[float] = [0.0]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # STORY-WATCHDOG-2026-05-25: own process group so killpg() in the
            # watchdog reaches all SDK descendants (claude CLI, node, etc.).
            start_new_session=True,
        )
        # STORY-857: register the proc so the SIGTERM handler can terminate
        # it cleanly on `systemctl restart`. _active_claim_for_signal is set
        # by poll_loop before this call (along with session/headers) so the
        # handler can also call /release. Cleared in the finally block.
        _active_sdk_proc = proc
        # Spawn the lease-renewal heartbeat thread for the bare-SDK path.
        #
        # Two execution paths exist in poll_loop():
        #   1. Orchestrated (DISPATCH_V2_ORCHESTRATION=1) — calls
        #      v2_orchestrator.run_orchestrated() which has its own heartbeat
        #      thread; this _run_sdk() is never reached.
        #   2. Bare SDK (default) — calls _run_sdk() and blocks on
        #      proc.communicate() for up to 2h. Nothing else heartbeats.
        #
        # claude_sdk_tool.py logs "heartbeat" lines for visibility but does NOT
        # call /api/dispatch/v2/heartbeat. With a 15-min lease TTL, any Phase 8
        # run longer than 15 min loses its lease mid-stride and the
        # StuckAgentWatcher reclaims the job. This thread closes that gap by
        # POSTing /heartbeat every DISPATCH_LEASE_RENEW_INTERVAL seconds while
        # the SDK process is alive.
        if session is not None and headers is not None:
            hb_thread = threading.Thread(
                target=_heartbeat_loop,
                args=(claim, proc),
                kwargs={
                    "session": session,
                    "headers": headers,
                    "interval_sec": heartbeat_interval_sec,
                    "stop_event": hb_stop,
                    "lease_lost_event": lease_lost_event,
                },
                name=f"v2-heartbeat-{claim.story_id}",
                daemon=True,
            )
            hb_thread.start()

            # STORY-WATCHDOG-2026-05-25: progress watchdog. Trips on
            # silence / RSS / wall-clock / lease-lost, SIGTERMs the SDK
            # group, releases the lease as watchdog_<trip>. Thresholds
            # come from env so we can tune without redeploy.
            try:
                from deployment.hermes.sdk_progress_watchdog import ProgressWatchdog
            except ImportError:  # pragma: no cover - VM runs from /opt/agent
                from sdk_progress_watchdog import ProgressWatchdog  # type: ignore

            thresholds = {
                "turn_max_seconds": int(
                    os.environ.get("SDK_WATCHDOG_TURN_MAX_S", "2400")
                ),
                # Silence trip default: 0 (disabled) until a stdout tailer
                # updates last_output_ts. Operators can enable post-deploy
                # by setting SDK_WATCHDOG_SILENCE_MAX_S=600 (or similar).
                "silence_max_seconds": int(
                    os.environ.get("SDK_WATCHDOG_SILENCE_MAX_S", "0")
                ),
                "rss_max_mb": int(
                    os.environ.get("SDK_WATCHDOG_RSS_MAX_MB", "3500")
                ),
                "tick_seconds": int(
                    os.environ.get("SDK_WATCHDOG_TICK_S", "15")
                ),
                "grace_seconds": int(
                    os.environ.get("SDK_WATCHDOG_GRACE_S", "10")
                ),
            }
            watchdog = ProgressWatchdog(
                proc,
                claim,
                last_output_ts,
                lease_lost_event,
                session=session,
                headers=headers,
                release_fn=release_claim,
                clear_lease_fn=_clear_active_lease,
                thresholds=thresholds,
            )
            watchdog.start()
        stdout, _ = proc.communicate(timeout=7200)  # 2h max
        success = proc.returncode == 0
        # STORY-WATCHDOG-2026-05-25: if the watchdog fired, the SDK was
        # killed and the lease already released — surface the trip class
        # in the output tail so _failure_event_data.classify() picks the
        # right non-retryable failure_class.
        if watchdog is not None and watchdog.fired:
            success = False
            trip = watchdog.fired_reason or "watchdog_progress_stall"
            tail = stdout[-3900:] if stdout else ""
            return success, f"[{trip}] {tail}"
        # STORY-857a: bumped 2000 → 4000 chars so the failure classifier sees
        # enough log context to identify lease_lost / sigterm / quota patterns
        # that previously fell through to the catch-all phase_runner_crash.
        return success, stdout[-4000:] if stdout else ""
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "SDK timed out after 2h"
    except Exception as exc:
        return False, str(exc)
    finally:
        # STORY-857: clear the proc ref so an idle-window SIGTERM doesn't see
        # a stale subprocess. _active_claim_for_signal is cleared by poll_loop
        # after the transition completes.
        _active_sdk_proc = None
        # Stop the heartbeat thread before returning. The thread checks
        # proc.poll() too, but signaling explicitly avoids one extra tick.
        hb_stop.set()
        if hb_thread is not None:
            hb_thread.join(timeout=2)
        # STORY-WATCHDOG-2026-05-25: stop the progress watchdog. .stop()
        # only sets the internal Event — we don't block on the daemon
        # thread because it may be inside a SIGTERM grace sleep.
        if watchdog is not None:
            watchdog.stop()


def _failure_event_data(output: str, exit_code: int | None) -> dict:
    """Build structured failure event payload with robust classification.

    STORY-857a:
      - reason captures up to 4000 chars (was 500) so the classifier sees
        enough log tail to match expanded patterns.
      - tail_summary keeps a compact head+tail snippet for UI display.
      - default failure_class on classifier-import failure is 'unknown'
        (was 'phase_runner_crash'). 'unknown' is non-retryable; this avoids
        the 3-attempt retry burn that hit the fleet on 2026-05-03.
    """
    full_output = output or "SDK exited non-zero"
    reason = full_output[:4000]
    head = full_output[:300]
    tail = full_output[-300:] if len(full_output) > 600 else ""
    tail_summary = f"{head}\n...\n{tail}" if tail else head
    failure_class = "unknown"
    try:
        from tech_dev_agents.ops_console.services.dispatch_failure_policy import classify
        failure_class = classify(
            reason,
            exit_code=exit_code,
            error_message=reason,
        )
    except Exception:
        # Keep poller resilient if classifier import fails in agent runtime.
        # 'unknown' default ensures non-retryable routing rather than the old
        # 'phase_runner_crash' which burned 3 retries before escalating.
        pass
    return {
        "failure_class": failure_class,
        "failure_reason": reason,
        "error_message": reason,
        "exit_code": exit_code,
        "tail_summary": tail_summary,
    }


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------


def poll_loop(
    *,
    poll_interval: int | None = None,
    heartbeat_interval: int | None = None,
) -> None:
    """Main v2 polling loop.

    Activated only when DISPATCH_PROTOCOL=v2.
    Claims jobs, runs the SDK, heartbeats the lease, and emits transitions.
    """
    if DISPATCH_PROTOCOL != "v2":
        logger.info(
            "dispatch_poller_v2: DISPATCH_PROTOCOL=%s — v2 poller not active",
            DISPATCH_PROTOCOL,
        )
        return

    agent_name = os.environ.get("AGENT_NAME", "unknown-agent")
    worker_version = os.environ.get("WORKER_VERSION", "2.0")
    _poll_interval = poll_interval or int(os.environ.get("DISPATCH_POLL_INTERVAL", "60"))
    _heartbeat_interval = heartbeat_interval or int(
        os.environ.get("DISPATCH_LEASE_RENEW_INTERVAL", "300")
    )

    logger.info(
        "dispatch_poller_v2: starting [agent=%s version=%s poll=%ds heartbeat=%ds]",
        agent_name, worker_version, _poll_interval, _heartbeat_interval,
    )

    session = requests.Session()
    headers = _build_headers(worker_version, agent_name)

    # STORY-857: install SIGTERM handler so a `systemctl restart` from
    # push-code.sh releases the active lease before exiting (rather than
    # orphaning it for the broken expired-lease sweeper to catch later).
    _install_sigterm_handler()

    global _active_claim_for_signal, _active_session_for_signal, _active_headers_for_signal

    while True:
        try:
            claim = claim_next(session=session, headers=headers, preferred_scope="small")
        except SystemExit:
            raise  # propagate handle_426_response's sys.exit(2)
        except Exception as exc:
            logger.error("dispatch_poller_v2: claim_next failed: %s", exc)
            time.sleep(_poll_interval)
            continue

        if claim is None:
            logger.debug("dispatch_poller_v2: nothing eligible — sleeping %ds", _poll_interval)
            time.sleep(_poll_interval)
            continue

        logger.info(
            "dispatch_poller_v2: claimed job_id=%s story_id=%s",
            claim.job_id, claim.story_id,
        )

        # STORY-857: persist the lease + register signal-handler refs so a
        # `systemctl restart` mid-run releases the lease cleanly. Without
        # these two lines, an in-flight lease orphans on every restart and
        # the SDK keeps running detached from the poller.
        _write_active_lease(claim)
        _active_claim_for_signal = claim
        _active_session_for_signal = session
        _active_headers_for_signal = headers

        # STORY-860: Orchestrated execution path. When enabled, the orchestrator
        # handles branch lifecycle, phase events, rework threading, resume, and
        # heartbeat — superseding STORY-859's rebase prep step. Feature-flagged
        # via DISPATCH_V2_ORCHESTRATION env var for safe rollback.
        _orchestration_enabled = os.environ.get("DISPATCH_V2_ORCHESTRATION", "0") == "1"
        if _orchestration_enabled:
            try:
                from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
                run_orchestrated(claim, session, headers)
            except Exception as exc:
                logger.error(
                    "dispatch_poller_v2: orchestration failed for job_id=%s: %s",
                    claim.job_id, exc,
                )
                # Fallback: emit a terminal failed event
                transition_claim(
                    claim,
                    event_type="failed",
                    event_data=_failure_event_data(str(exc), None),
                    session=session,
                    headers=headers,
                )
            _clear_active_lease()
            _active_claim_for_signal = None
            _active_session_for_signal = None
            _active_headers_for_signal = None
            time.sleep(1)
            continue

        # STORY-859: Rebase / rework / fix-PR prompts get a workspace prep step
        # before SDK launch. Failure here emits a typed event (workspace_missing
        # or git_rebase_failed — non-retryable, attention queue) and skips SDK
        # launch entirely. This prevents the prior behaviour of misclassifying
        # all prep failures as the retryable phase_runner_crash.
        if _is_rebase_prompt(claim.prompt):
            prep_ok, prep_diag = _prepare_rebase_workspace(claim)
            if not prep_ok:
                diag = prep_diag or {}
                prep_kind = diag.get("prep_failure_kind", "")
                # [MEDIUM] Map workspace_missing to its own non-retryable class
                # (infra failure) rather than conflating it with git_rebase_failed
                # (merge conflict). All other prep failures → git_rebase_failed.
                if prep_kind == "workspace_missing":
                    failure_class = "workspace_missing"
                else:
                    failure_class = "git_rebase_failed"
                stderr_tail = (diag.get("stderr") or "")[-2000:]
                stdout_tail = (diag.get("stdout") or "")[-2000:]
                failure_reason = stderr_tail or stdout_tail or f"rebase prep failed ({prep_kind})"
                head = failure_reason[:300]
                tail = failure_reason[-300:] if len(failure_reason) > 600 else ""
                tail_summary = f"{head}\n...\n{tail}" if tail else head
                event_data: dict = {
                    "failure_class": failure_class,
                    "failure_reason": failure_reason[:2000],
                    "exit_code": diag.get("exit_code"),
                    "git_command": diag.get("command"),
                    "tail_summary": tail_summary,
                    "prep_failure_kind": prep_kind,
                }
                if diag.get("workspace_tainted"):
                    event_data["workspace_tainted"] = True
                ok = transition_claim(
                    claim,
                    event_type="failed",
                    event_data=event_data,
                    session=session,
                    headers=headers,
                )
                if not ok:
                    logger.warning(
                        "dispatch_poller_v2: transition %s failed for job_id=%s",
                        failure_class, claim.job_id,
                    )
                # Clear lease state before continuing to next iteration.
                _clear_active_lease()
                _active_claim_for_signal = None
                _active_session_for_signal = None
                _active_headers_for_signal = None
                time.sleep(1)
                continue

        # Run the SDK in this thread (simple single-job model matching v1 poller)
        success, output = _run_sdk(
            claim,
            session=session,
            headers=headers,
            heartbeat_interval_sec=_heartbeat_interval,
        )

        if success:
            # STORY-903: pass claim context so the gh REST fallback can query
            # by branch when the regex fast path misses.
            event_type, event_data = _success_transition_payload(
                output,
                story_id=claim.story_id,
                repo=claim.repo,
                branch=claim.branch,
            )
            ok = transition_claim(
                claim,
                event_type=event_type,
                event_data=event_data,
                session=session,
                headers=headers,
            )
            if not ok:
                logger.warning(
                    "dispatch_poller_v2: transition %s failed for job_id=%s",
                    event_type, claim.job_id,
                )
        else:
            # STORY-857a: route through _failure_event_data so the SDK output
            # is run through the expanded classifier (lease_lost, sigterm,
            # auth_expired, etc.) instead of hard-coded phase_runner_crash.
            ok = transition_claim(
                claim,
                event_type="failed",
                event_data=_failure_event_data(output, None),
                session=session,
                headers=headers,
            )
            if not ok:
                logger.warning(
                    "dispatch_poller_v2: transition failed for job_id=%s",
                    claim.job_id,
                )

        # STORY-857: lease is no longer ours. Clear the state file + signal
        # refs so a SIGTERM during the idle window doesn't try to re-release
        # an already-transitioned lease.
        _clear_active_lease()
        _active_claim_for_signal = None
        _active_session_for_signal = None
        _active_headers_for_signal = None

        # Brief pause before next claim to avoid tight-loop on rapid job exhaustion
        time.sleep(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for dispatch_poller_v2 when run as a script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if DISPATCH_PROTOCOL != "v2":
        logger.warning(
            "dispatch_poller_v2 invoked but DISPATCH_PROTOCOL=%s. "
            "Set DISPATCH_PROTOCOL=v2 to activate v2 mode.",
            DISPATCH_PROTOCOL,
        )
        sys.exit(0)
    poll_loop()


if __name__ == "__main__":
    main()
