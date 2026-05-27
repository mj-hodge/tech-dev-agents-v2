"""STORY-724: Morris Queue Orchestrator — main entry point.

Runs every 10 minutes via cron on the Morris VM.
Uses flock guard to prevent concurrent invocations.

Review-findings fix (2026-04-26):
  M-2: OPS console outage detection — if both fetch_queue and fetch_history
       fail in the same cycle, all interventions are suppressed and a CRIT
       log line is emitted.  _FetchResult carries an .ok flag so the main
       loop can distinguish "empty queue" from "fetch failed".
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Import-path bootstrap (Bug D fix)
#
# This file is reachable two ways:
#   1) From the repo (tests, dev): as `deployment.morris.scripts.orchestrator_loop`
#      with cwd at the repo root. Sibling modules (detectors, interventions,
#      improvement/) are importable via the full package path.
#   2) On the Morris VM: files are deployed flat into /opt/morris/ (no
#      `deployment/morris/scripts/` prefix exists), and cron invokes
#      `/opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py`. In
#      that layout `from deployment.morris.scripts.X import Y` raises
#      ModuleNotFoundError.
#
# Inserting this file's directory onto sys.path lets sibling modules resolve
# as bare names (`from detectors import ...`, `from interventions import ...`)
# in BOTH layouts. The insertion is idempotent and harmless under tests.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import argparse
import fcntl
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Module-level logger (configured by setup_logging)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetch result sentinel (M-2 — OPS console outage detection)
# ---------------------------------------------------------------------------


class _FetchResult(list):
    """A list subclass that carries an .ok flag.

    ok=True  → fetch succeeded (data may be empty — that is normal)
    ok=False → fetch failed (connection error, timeout, JSON parse error)

    Downstream code that doesn't know about _FetchResult simply sees a plain
    list (backwards compatible). The main loop checks both flags to detect a
    full OPS console outage and suppress interventions accordingly.
    """

    def __init__(self, data: list, ok: bool) -> None:  # noqa: D107
        super().__init__(data)
        self.ok = ok


# ---------------------------------------------------------------------------
# Config loading and validation
# ---------------------------------------------------------------------------

REQUIRED_KEYS = [
    "ops_console",
    "teams",
    "agents",
    "thresholds",
    "interventions",
    "workdir",
    "log_path",
    "lock_path",
]


def load_config(path: str) -> dict:
    """Load and validate orchestrator_config.yaml.

    Raises RuntimeError with a descriptive message if required keys are missing.
    Expands ${ENV_VAR} references from environment.
    """
    with open(path) as f:
        raw = f.read()

    # Expand environment variables of the form ${VAR}
    import re
    def _expand(m: "re.Match") -> str:
        var = m.group(1)
        return os.environ.get(var, m.group(0))
    raw = re.sub(r"\$\{([^}]+)\}", _expand, raw)

    config = yaml.safe_load(raw)

    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        raise RuntimeError(
            f"orchestrator_config.yaml is missing required keys: {missing}. "
            "Fix the config before running."
        )
    return config


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(config: dict, log_level: str = "INFO") -> None:
    """Configure root logger to write to the audit log path and stdout."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    log_path = config.get("log_path", "/var/log/morris/orchestrator.log")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    except OSError:
        pass  # Running in test/dev — stdout only is fine

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        handlers=handlers,
        force=True,
    )


# ---------------------------------------------------------------------------
# HTTP session builder
# ---------------------------------------------------------------------------


def build_session(config: dict) -> Any:
    """Build a requests.Session with ops console auth headers."""
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests library not available")

    session = requests.Session()
    api_key = config["ops_console"].get("api_key", "")
    session.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})
    return session


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def fetch_queue(session: Any, config: dict) -> "_FetchResult":
    """GET /api/dispatch/v2/queue?include_claimed=true.

    Returns a _FetchResult (list subclass) with .ok=True on success, .ok=False
    on failure.  Callers that just iterate over the result see a plain list.
    The main loop inspects .ok on both fetch_queue and fetch_history to detect
    a full OPS console outage (M-2).

    Resilience: if ops console is unreachable, return _FetchResult([], ok=False)
    and log a warning.  Returning empty-but-ok=False lets downstream detectors
    fire zero findings (safe — no spurious release/DM activity) while still
    letting the main loop detect the outage.
    """
    base = config["ops_console"]["url"].rstrip("/")
    try:
        resp = session.get(
            f"{base}/api/dispatch/v2/queue",
            params={"include_claimed": "true"},
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FETCH-QUEUE-FAILED] ops_console=%s error=%s", base, exc)
        return _FetchResult([], ok=False)
    if isinstance(data, list):
        return _FetchResult(data, ok=True)
    # Some endpoints return {"items": [...]} or {"queue": [...]}
    return _FetchResult(
        data.get("items", data.get("queue", data.get("claimed", []))),
        ok=True,
    )


def fetch_history(session: Any, config: dict) -> "_FetchResult":
    """GET /api/dispatch/history?status=failed&limit=50 — filter client-side to last 24h.

    Returns a _FetchResult with .ok=True on success, .ok=False on failure (M-2).
    Resilience: same contract as fetch_queue — return _FetchResult([], ok=False)
    on any failure so a transient ops-console outage doesn't abort the cycle.
    """
    base = config["ops_console"]["url"].rstrip("/")
    try:
        resp = session.get(
            f"{base}/api/dispatch/history",
            params={"status": "failed", "limit": "50"},
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FETCH-HISTORY-FAILED] ops_console=%s error=%s", base, exc)
        return _FetchResult([], ok=False)
    items: list[dict]
    if isinstance(data, list):
        items = data
    else:
        items = data.get("items", data.get("history", []))

    # Client-side 24h filter (no since= param on the API)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    from detectors import parse_dt
    filtered = []
    for row in items:
        ts = row.get("completed_at")
        if not ts:
            continue
        try:
            if parse_dt(ts) >= cutoff:
                filtered.append(row)
        except (ValueError, TypeError):
            # Malformed timestamp — skip rather than crash the cycle
            logger.warning("[FETCH-HISTORY] skipping row with bad completed_at=%r", ts)
    return _FetchResult(filtered, ok=True)


def fetch_prs(config: dict) -> list[dict]:
    """Run `gh pr list` for each configured repo and merge results."""
    repos = config["agents"].get("pr_repos", [])
    all_prs: list[dict] = []
    for repo in repos:
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "list",
                    "--repo", repo,
                    "--state", "open",
                    "--json", "number,title,headRefName,baseRefName,author,mergeable,repository",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                prs = json.loads(result.stdout or "[]")
                for pr in prs:
                    pr["repo"] = repo
                all_prs.extend(prs)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning("[COLLECT] gh pr list failed for %s: %s", repo, e)
    return all_prs


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_all(
    queue: list[dict],
    history: list[dict],
    prs: list[dict],
    config: dict,
    now: datetime,
) -> dict[str, list]:
    """Run all 6 detectors and return findings dict. Pure — no side effects."""
    from detectors import (
        detect_stale_never_started,
        detect_stale_heartbeat,
        detect_stale_phase,
        detect_repeated_failures,
        detect_pr_conflicts,
        detect_needs_info_decay,
    )
    return {
        "stale_never_started": detect_stale_never_started(queue, config, now),
        "stale_heartbeat":     detect_stale_heartbeat(queue, config, now),
        "stale_phase":         detect_stale_phase(queue, config, now),
        "repeated_failures":   detect_repeated_failures(history, config, now),
        "pr_conflicts":        detect_pr_conflicts(prs, config),
        "needs_info_decay":    detect_needs_info_decay(queue, config, now),
    }


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


def execute_interventions(
    findings: dict[str, list],
    queue: list[dict],
    session: Any,
    config: dict,
    dry_run: bool,
    mode: str = "full",
) -> int:
    """Execute interventions in priority order. Returns intervention count."""
    from interventions import (
        release_claim,
        post_approval_needed,
        invoke_rebase_subagent,
        post_dm,
        post_needs_info_surface,
        post_load_imbalance_dm,
    )

    count = 0

    def _safe(label: str, fn, *args, **kwargs) -> bool:
        """Run an intervention and isolate it from the cycle.

        A single failing intervention (network blip, malformed record) must
        never block the others. We log and continue.
        """
        try:
            fn(*args, **kwargs)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[INTERVENTION-FAILED] %s: %s", label, exc)
            return False

    # Priority 1: Release stale claims
    stale_all = (
        findings.get("stale_never_started", [])
        + findings.get("stale_heartbeat", [])
        + findings.get("stale_phase", [])
    )
    for r in stale_all:
        if _safe(
            f"release_claim STORY-{r.story_id}",
            release_claim, r.story_id, r.reason, session, config, dry_run,
        ):
            count += 1

    # Priority 2: Escalate repeated failures
    for r in findings.get("repeated_failures", []):
        if _safe(
            f"post_approval_needed STORY-{r.story_id}",
            post_approval_needed, r.story_id, r.reason, r.recent_failures,
            session, config, dry_run,
        ):
            count += 1

    # Priority 3: Handle PR conflicts
    for r in findings.get("pr_conflicts", []):
        if r.agent_owned and config["interventions"]["rebase"].get("enabled", False):
            if mode == "light":
                logger.info("[MODE light] subagent suppressed: rebase PR#%s", r.pr_number)
            else:
                _safe(
                    f"invoke_rebase_subagent PR#{r.pr_number}",
                    invoke_rebase_subagent, r, config, dry_run, session=session,
                )
        else:
            if not dry_run:
                action = "rebase disabled — manual review needed" if r.agent_owned else "human-authored PR — no auto-action"
                _safe(
                    f"post_dm conflict PR#{r.pr_number}",
                    post_dm,
                    "[INFO]",
                    f"PR #{r.pr_number} has merge conflicts",
                    [f"Repo: {r.repo}", f"Author: {r.author}", f"Title: {r.title}", action],
                    session,
                    config,
                )
            else:
                logger.info("[DRY-RUN] would: post [INFO] DM for PR #%s conflict", r.pr_number)
        count += 1

    # Priority 4: Needs info decay
    if findings.get("needs_info_decay"):
        if _safe(
            "post_needs_info_surface",
            post_needs_info_surface, findings["needs_info_decay"], session, config, dry_run,
        ):
            count += 1

    # Priority 5: Load imbalance
    _safe(
        "post_load_imbalance_dm",
        post_load_imbalance_dm, queue, session, config, dry_run,
    )

    return count


# ---------------------------------------------------------------------------
# Briefing mode
# ---------------------------------------------------------------------------


def run_briefing(
    session: Any,
    config: dict,
    dry_run: bool,
    now: Optional[datetime] = None,
) -> None:
    """Post a morning briefing DM with queue status summary."""
    from interventions import post_dm

    if now is None:
        now = datetime.now(timezone.utc)

    queue = fetch_queue(session, config)
    history = fetch_history(session, config)

    # Count queue statuses
    status_counts: dict[str, int] = {}
    for row in queue:
        s = row.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    pending_n = status_counts.get("pending", 0)
    claimed_n = status_counts.get("claimed", 0)
    in_progress_n = status_counts.get("in_progress", 0)
    needs_info_n = status_counts.get("needs_info", 0)

    # Count failed in last 24h from history (already filtered)
    failed_24h = len(history)

    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    bullets = [
        f"Queue status ({ts_str}):",
        f"  Pending:      {pending_n}",
        f"  Claimed:      {claimed_n}",
        f"  In progress:  {in_progress_n}",
        f"  Needs info:   {needs_info_n}",
        f"  Failed (24h): {failed_24h}",
    ]

    # Failed stories detail
    if history:
        bullets.append("")
        bullets.append("Failed stories (last 24h):")
        for row in history:
            sid = row.get("story_id", "?")
            title = row.get("title", "no title")
            reason = row.get("failure_reason", "unknown")
            bullets.append(f"  - STORY-{sid}: {title} — {reason}")

    # Claimed stories detail
    claimed_rows = [r for r in queue if r.get("status") in ("claimed", "in_progress")]
    if claimed_rows:
        bullets.append("")
        bullets.append("In-progress stories:")
        for row in claimed_rows:
            sid = row.get("story_id", "?")
            title = row.get("title", "no title")
            agent = row.get("claimed_by", "unknown")
            claimed_at = row.get("claimed_at", "?")
            try:
                from detectors import parse_dt
                elapsed = (now - parse_dt(claimed_at)).total_seconds() / 3600
                elapsed_str = f"{elapsed:.1f}h"
            except Exception:
                elapsed_str = "?"
            bullets.append(f"  - STORY-{sid}: {title} — claimed by {agent} — {elapsed_str} elapsed")

    if dry_run:
        logger.info("[DRY-RUN] would: post [BRIEFING] DM for %s", date_str)
        return

    # Reuse post_dm — keeps DM-formatting (severity prefix, bullet rendering,
    # Graph URL construction) in a single place. Avoids duplicating the
    # _graph_url helper and the requests payload shape across modules.
    post_dm(
        "[BRIEFING]",
        f"Morris Orchestrator — {date_str} Morning Queue Summary",
        bullets,
        session,
        config,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Morris Queue Orchestrator — classifies queue state and executes interventions."
    )
    parser.add_argument("--dry-run", action="store_true", help="Classify only; no API mutations.")
    parser.add_argument(
        "--check",
        metavar="DETECTOR",
        help="Run only the named detector (stale_never_started, stale_heartbeat, "
             "stale_phase, repeated_failures, pr_conflicts, needs_info_decay, load_imbalance).",
    )
    parser.add_argument("--briefing-only", action="store_true", help="Post morning briefing DM only.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "orchestrator_config.yaml"),
        help="Path to orchestrator_config.yaml.",
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG|INFO|WARNING|ERROR")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    config = load_config(args.config)
    setup_logging(config, args.log_level)

    lock_path = config["lock_path"]
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info("skip — previous invocation still running")
        sys.exit(0)

    try:
        logger.info("=== orchestrator START ===")
        try:
            session = build_session(config)

            # STORY-734: Check quota and auto-transition operating mode
            from mode_controller import check_and_auto_transition, get_mode, mode_allows
            current_mode = check_and_auto_transition(config, session)
            logger.info("[MODE] current_mode=%s", current_mode)

            # Minimal mode: orchestrator poll is suspended
            if not mode_allows(current_mode, "orchestrator_poll"):
                logger.info("[MODE] %s — orchestrator poll suspended, exiting cleanly", current_mode)
                return

            # Briefing mode gate
            if args.briefing_only:
                if not mode_allows(current_mode, "briefing"):
                    logger.info("[MODE] %s — briefing suspended, exiting cleanly", current_mode)
                    return
                run_briefing(session, config, args.dry_run)
                return

            queue   = fetch_queue(session, config)
            history = fetch_history(session, config)
            prs     = fetch_prs(config)
            now     = datetime.now(timezone.utc)

            logger.info(
                "[COLLECT] queue=%s history=%s prs=%s",
                len(queue), len(history), len(prs),
            )

            # M-2: OPS console outage gate — if both primary fetches failed,
            # the API is unreachable. Suppress ALL interventions this cycle
            # to avoid acting on stale/empty data, and log CRIT for alerting.
            if not getattr(queue, "ok", True) and not getattr(history, "ok", True):
                logger.critical(
                    "[OUTAGE] OPS console unreachable (queue_ok=False, history_ok=False)"
                    " — suppressing all interventions this cycle"
                )
                return

            findings = classify_all(queue, history, prs, config, now)

            logger.info(
                "[CLASSIFY] stale_never_started=%s stale_heartbeat=%s repeated_failures=%s pr_conflicts=%s",
                len(findings["stale_never_started"]),
                len(findings["stale_heartbeat"]),
                len(findings["repeated_failures"]),
                len(findings["pr_conflicts"]),
            )

            if args.check:
                findings = {k: v for k, v in findings.items() if k == args.check}

            count = execute_interventions(findings, queue, session, config, args.dry_run, mode=current_mode)
            logger.info("=== orchestrator END interventions=%s ===", count)
        except Exception:  # noqa: BLE001 — last-resort guard so cron always sees a clean exit log
            logger.exception("=== orchestrator FAILED with unhandled exception ===")
            # Re-raise so cron's exit-code surfaces the failure (e.g. via mail-on-failure
            # or the dead-man monitoring described in the resiliency review).
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
