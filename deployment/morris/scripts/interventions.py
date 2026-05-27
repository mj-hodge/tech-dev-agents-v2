"""STORY-724: Intervention functions for Morris Queue Orchestrator.

Each function accepts `dry_run=False`. When dry_run=True:
  - Zero HTTP calls
  - Zero subprocess invocations
  - Logs [DRY-RUN] would: {action}

Review-findings fixes (2026-04-26):
  H-2: all variable content (story IDs, PR titles, branch names, error text)
       is HTML-escaped via html.escape() before injection into Teams DM bodies.
  M-3: session.post() response status is checked in post_dm; 4xx/5xx responses
       are logged as warnings rather than silently succeeding.
"""

from __future__ import annotations

import html as _html
import logging
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _graph_url(config: dict) -> str:
    """Build the Graph API messages URL for Mark's chat."""
    chat_id = config["teams"]["mark_chat_id"]
    return f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"


def _ops_base(config: dict) -> str:
    return config["ops_console"]["url"].rstrip("/")


def _e(value: Any) -> str:
    """HTML-escape a value coerced to str (H-2).

    Applied to all variable content before injection into Teams DM bodies to
    prevent HTML injection via attacker-controlled data (PR titles, branch
    names, failure reasons, error text from APIs or exceptions).
    """
    return _html.escape(str(value))


# ---------------------------------------------------------------------------
# 1. post_dm — send a Teams DM via Graph API
# ---------------------------------------------------------------------------


def post_dm(
    severity: str,
    headline: str,
    bullets: list[str],
    session: Any,
    config: dict,
) -> None:
    """POST a Teams DM via Graph API.

    severity: e.g. '[ACTION]', '[INFO]', '[BRIEFING]', '[DRY-RUN]'
    bullets: list of bullet-point strings (plain text; HTML-escaped before send)

    H-2: headline and every bullet are HTML-escaped before building the body.
    M-3: session.post() response status is checked; 4xx/5xx logged as warning.

    Resilience: any exception from the Graph API call (network error, 5xx,
    auth failure) is caught and logged. We never want a Teams outage to
    abort the orchestrator cycle — the next tick will retry, and the
    fleet-check cron is a fallback alerting path.
    """
    # H-2: escape all variable content before building the DM body
    safe_headline = _e(headline)
    lines = [f"{severity} {safe_headline}", ""]
    for b in bullets:
        lines.append(f"- {_e(b)}")
    body_text = "\n".join(lines)

    url = _graph_url(config)
    payload = {
        "body": {
            "contentType": "text",
            "content": body_text,
        }
    }
    try:
        resp = session.post(url, json=payload)
        # M-3: check HTTP response status and warn on 4xx/5xx
        status = getattr(resp, "status_code", None)
        if status is not None and status >= 400:
            logger.warning(
                "[DM-FAILED] %s %s — Graph API returned HTTP %s",
                severity, headline, status,
            )
        else:
            logger.info("%s %s", severity, headline)
    except Exception as exc:  # noqa: BLE001 — defensive: any failure must not abort the cycle
        logger.warning(
            "[DM-FAILED] %s %s — Graph API error: %s", severity, headline, exc
        )


# ---------------------------------------------------------------------------
# 2. release_claim — release a stale claim on the ops console
# ---------------------------------------------------------------------------


def release_claim(
    story_id: int,
    reason: str,
    session: Any,
    config: dict,
    dry_run: bool = False,
) -> None:
    """Release a stale claim. Supports STORY-702 shim."""
    if dry_run:
        logger.info("[DRY-RUN] would: release_claim story_id=%s reason=%s", story_id, reason)
        return

    base = _ops_base(config)

    # v2 cutover (2026-05-03): use /api/dispatch/v2/operator/resume which emits
    # a `requeued` event. Works for both 'leased' (force-release) and
    # 'needs_info' (resume after answer) prior states. The v1 mirror trigger
    # syncs the change back to dispatch_items.status for legacy readers.
    # Resilience: a transient ops-console outage must not kill the cycle.
    try:
        # Resolve repo for this story so the endpoint can find the right job
        # when the same story_id exists across repos.
        repo = config["interventions"]["release"].get("repo")
        body = {"reason": reason}
        if repo:
            body["repo"] = repo
            body["story_id"] = f"STORY-{story_id}" if not str(story_id).startswith("STORY-") else story_id
        else:
            # Fall back to v1-style positional path for backwards compat —
            # this path will route through the v1 endpoint which the
            # reverse-mirror picks up.
            url = f"{base}/api/dispatch/release/{story_id}"
            resp = session.post(
                url,
                json={"reason": reason, "released_by": "morris-orchestrator"},
            )
            if hasattr(resp, "json"):
                try:
                    data = resp.json()
                    if data.get("already_released"):
                        logger.info("[SKIP] STORY-%s already released", story_id)
                        return
                except Exception:
                    pass
            return  # done via v1 path

        url = f"{base}/api/dispatch/v2/operator/resume"
        resp = session.post(url, json=body)
        if getattr(resp, "status_code", None) == 404:
            post_dm(
                "[INFO]",
                f"Cannot release STORY-{story_id} — endpoint unavailable",
                [f"Reason: {_e(reason)}", "v2 operator/resume returned 404"],
                session,
                config,
            )
            return
        if getattr(resp, "status_code", None) == 409:
            # Job not in non-terminal state (already completed/cancelled/failed)
            logger.info("[SKIP] STORY-%s not eligible for release (409)", story_id)
            return
    except Exception as exc:  # noqa: BLE001 — defensive: do not abort cycle on transient API errors
        logger.warning(
            "[RELEASE-FAILED] STORY-%s reason=%s error=%s — will retry next cycle",
            story_id, reason, exc,
        )
        return

    post_dm(
        "[ACTION]",
        f"Released stale claim: STORY-{story_id}",
        [f"Reason: {_e(reason)}", "Released by: morris-orchestrator"],
        session,
        config,
    )


# ---------------------------------------------------------------------------
# 3. invoke_rebase_subagent — run auto-rebase via subprocess
# ---------------------------------------------------------------------------


def invoke_rebase_subagent(
    pr: Any,
    config: dict,
    dry_run: bool = False,
    session: Optional[Any] = None,
) -> None:
    """Invoke the auto-rebase subagent for a conflicting PR.

    Requires config['interventions']['rebase']['enabled'] == True.
    Catches subprocess.TimeoutExpired and posts [INFO] DM.

    H-2: PR title, branch names, and error text are HTML-escaped before
    injection into DM bullet strings passed to post_dm.
    """
    if not config["interventions"]["rebase"].get("enabled", False):
        if session is not None:
            post_dm(
                "[INFO]",
                f"Rebase disabled — PR #{_e(pr.pr_number)} ({_e(pr.head)}) has conflicts",
                [f"Repo: {_e(pr.repo)}", "Set interventions.rebase.enabled=true once STORY-722 merged"],
                session,
                config,
            )
        return

    if dry_run:
        logger.info(
            "[DRY-RUN] would: invoke_rebase_subagent PR #%s (%s) onto %s",
            pr.pr_number,
            pr.head,
            pr.base,
        )
        return

    timeout = config["interventions"]["rebase"].get("timeout_seconds", 300)
    workdir = config.get("workdir", "/home/hermes/dev")
    # Raw values used in subprocess args — subprocess shell does not interpret HTML
    prompt = (
        f"Rebase PR #{pr.pr_number} ({pr.head}) onto {pr.base} in repo {pr.repo}. "
        f"Resolve any conflicts and force-push."
    )

    try:
        result = subprocess.run(
            [
                "/opt/agent/venv/bin/python",
                "/opt/agent/claude_sdk_tool.py",
                "-p", prompt,
                "-w", workdir,
                "--max-turns", "15",
                "--permission-mode", "bypassPermissions",
            ],
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        logger.info("[ACTION] rebase PR #%s exit=%s", pr.pr_number, result.returncode)
        if session is not None:
            post_dm(
                "[ACTION]",
                f"Rebase subagent completed for PR #{_e(pr.pr_number)}",
                [f"Repo: {_e(pr.repo)}", f"Exit code: {_e(result.returncode)}"],
                session,
                config,
            )
    except subprocess.TimeoutExpired:
        logger.warning("[INFO] Rebase subagent timed out for PR #%s", pr.pr_number)
        if session is not None:
            post_dm(
                "[INFO]",
                f"Rebase subagent timed out for PR #{_e(pr.pr_number)}",
                [
                    f"Repo: {_e(pr.repo)}",
                    f"Timeout: {_e(timeout)}s",
                    "Manual rebase required.",
                ],
                session,
                config,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        # Catch broader subprocess failures (binary missing, fork failure,
        # permission denied) so a misconfigured VM can't kill the cycle.
        logger.warning(
            "[INFO] Rebase subagent failed for PR #%s: %s", pr.pr_number, exc
        )
        if session is not None:
            post_dm(
                "[INFO]",
                f"Rebase subagent error for PR #{_e(pr.pr_number)}",
                [
                    f"Repo: {_e(pr.repo)}",
                    f"Error: {_e(exc.__class__.__name__)}: {_e(exc)}",
                    "Manual rebase required.",
                ],
                session,
                config,
            )


# ---------------------------------------------------------------------------
# 4. post_approval_needed — escalation DM for repeated failures
# ---------------------------------------------------------------------------


def post_approval_needed(
    story_id: int,
    reason: str,
    failures: list[dict],
    session: Any,
    config: dict,
    dry_run: bool = False,
) -> None:
    """Post an [APPROVAL-NEEDED] DM for a story with repeated failures.

    Does NOT re-enqueue or call any release endpoint.

    H-2: failure_reason and timestamp strings from the ops console API are
    HTML-escaped before injection into the DM body.
    """
    if dry_run:
        logger.info(
            "[DRY-RUN] would: post_approval_needed story_id=%s failures=%s",
            story_id,
            len(failures),
        )
        return

    n = len(failures)
    body_lines = [f"[APPROVAL-NEEDED] Story {story_id} has failed {n} times in 24h", ""]
    body_lines.append("Failure history:")
    for f in failures:
        # H-2: escape API-sourced strings (timestamps, exit codes, failure reasons)
        ts = _e(f.get("completed_at", "?"))
        code = _e(f.get("exit_code", "?"))
        reason_str = _e(f.get("failure_reason", "?"))
        body_lines.append(f"- {ts}: exit {code} — {reason_str}")
    body_lines.append("")
    body_lines.append(f"Recommended: respond CANCEL/RETRY/SKIP-{story_id} via ops console.")

    body_text = "\n".join(body_lines)
    url = _graph_url(config)
    payload = {"body": {"contentType": "text", "content": body_text}}
    try:
        resp = session.post(url, json=payload)
        # M-3: log on 4xx/5xx from direct post (post_dm is not used here)
        status = getattr(resp, "status_code", None)
        if status is not None and status >= 400:
            logger.warning(
                "[APPROVAL-NEEDED-FAILED] STORY-%s HTTP %s", story_id, status
            )
        else:
            logger.info(
                "[APPROVAL-NEEDED] DM posted for STORY-%s failure_count=%s", story_id, n
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[APPROVAL-NEEDED-FAILED] STORY-%s failure_count=%s error=%s",
            story_id, n, exc,
        )


# ---------------------------------------------------------------------------
# 5. post_needs_info_surface — surface idle needs_info stories
# ---------------------------------------------------------------------------


def post_needs_info_surface(
    records: list[Any],
    session: Any,
    config: dict,
    dry_run: bool = False,
) -> None:
    """Post an [INFO] DM listing stories that have been waiting for operator response.

    H-2: updated_at strings from the ops console API are HTML-escaped.
    """
    if dry_run:
        logger.info("[DRY-RUN] would: post_needs_info_surface count=%s", len(records))
        return

    if not records:
        return

    decay_hours = config["thresholds"]["needs_info_decay_hours"]
    n = len(records)
    lines = [f"[INFO] {n} stories awaiting operator response (>{decay_hours}h)", ""]
    for r in records:
        # H-2: escape updated_at from API (story_id and age_hours are safe numeric values)
        lines.append(
            f"- STORY-{r.story_id}: waiting {r.age_hours}h"
            f" (last updated {_e(r.updated_at)})"
        )

    url = _graph_url(config)
    payload = {"body": {"contentType": "text", "content": "\n".join(lines)}}
    try:
        resp = session.post(url, json=payload)
        # M-3: log on 4xx/5xx
        status = getattr(resp, "status_code", None)
        if status is not None and status >= 400:
            logger.warning("[NEEDS-INFO-FAILED] HTTP %s count=%s", status, n)
        else:
            logger.info("[INFO] needs_info_surface DM posted count=%s", n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NEEDS-INFO-FAILED] count=%s error=%s", n, exc)


# ---------------------------------------------------------------------------
# 6. post_load_imbalance_dm — alert on queue imbalance
# ---------------------------------------------------------------------------


def post_load_imbalance_dm(
    queue: list[dict],
    session: Any,
    config: dict,
    dry_run: bool = False,
) -> None:
    """Post an [INFO] DM if one agent has >= overload_pending_count pending while others have 0.

    Does NOT call any release endpoint.

    H-2: agent names from config/queue are HTML-escaped before injection into body.
    """
    overload_threshold = config["thresholds"]["overload_pending_count"]

    # Count pending items per agent
    from collections import Counter
    pending_counts: Counter[str] = Counter()
    for row in queue:
        if row.get("status") == "pending":
            agent = row.get("assigned_agent", "unassigned")
            pending_counts[agent] += 1

    if not pending_counts:
        if dry_run:
            logger.info("[DRY-RUN] would: post_load_imbalance_dm (no pending items)")
        return

    overloaded = {a: c for a, c in pending_counts.items() if c >= overload_threshold}
    # Find agents with 0 pending from known agents not in the queue
    known_agents = list(config["agents"]["github_logins"].values())
    all_idle = [a for a in known_agents if pending_counts.get(a, 0) == 0]

    if not overloaded:
        if dry_run:
            logger.info("[DRY-RUN] would: post_load_imbalance_dm (no imbalance)")
        return

    if len(all_idle) < 2 and len(overloaded) == 0:
        return

    if dry_run:
        logger.info("[DRY-RUN] would: post_load_imbalance_dm overloaded=%s", list(overloaded.keys()))
        return

    lines = ["[INFO] Queue imbalance detected", ""]
    for agent, count in overloaded.items():
        # H-2: escape agent names (sourced from config/queue)
        lines.append(f"Overloaded: {_e(agent)} — {count} pending")
    if all_idle:
        lines.append(f"Idle: {', '.join(_e(a) for a in all_idle)}")
    lines.append("No automatic re-routing (v1: no assigned_to field).")

    url = _graph_url(config)
    payload = {"body": {"contentType": "text", "content": "\n".join(lines)}}
    try:
        resp = session.post(url, json=payload)
        # M-3: log on 4xx/5xx
        status = getattr(resp, "status_code", None)
        if status is not None and status >= 400:
            logger.warning(
                "[LOAD-IMBALANCE-FAILED] HTTP %s overloaded=%s",
                status, list(overloaded.keys()),
            )
        else:
            logger.info(
                "[INFO] load_imbalance DM posted overloaded=%s", list(overloaded.keys())
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LOAD-IMBALANCE-FAILED] overloaded=%s error=%s",
            list(overloaded.keys()), exc,
        )
