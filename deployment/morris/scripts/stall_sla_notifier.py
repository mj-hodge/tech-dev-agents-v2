#!/usr/bin/env python3
"""Stall SLA notifier — polls /api/dispatch/v2/stalls and DMs Mark on threshold breach.

Phase A — Goal #1 (less agent babysitting). Without this, `awaiting_human`
needs_info questions can sit unanswered for hours/days because nobody runs
`/whats-next` while heads-down on something else. This proactively pings
when the queue says a story has crossed an SLA.

Designed to run as a cron tick (recommended: every 15 min). Idempotent via
a JSON dedupe state file: the same `(story_id, stall_reason, day)` is only
notified once per day. Never raises on transient failures — the next tick
will retry.

Usage:
    python3 stall_sla_notifier.py [--dry-run]

Environment:
    OPS_CONSOLE_API_KEY  — required, used as X-API-Key
    OPS_CONSOLE_URL      — default https://tech-dev-agents.gorillacommerce.ai
    STALL_NOTIFIER_STATE — default ~/state/morris/stall-notifier-state.json

Crontab snippet (every 15 min):
    */15 * * * * /usr/bin/python3 /home/hermes/scripts/stall_sla_notifier.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("stall_sla_notifier")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


_DEFAULT_STATE_PATH = "~/state/morris/stall-notifier-state.json"
_DEFAULT_OPS_URL = "https://tech-dev-agents.gorillacommerce.ai"


def _state_path() -> Path:
    raw = os.environ.get("STALL_NOTIFIER_STATE", _DEFAULT_STATE_PATH)
    return Path(os.path.expanduser(raw))


def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {"notified": {}}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "notified" not in data:
            return {"notified": {}}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("state load failed (%s) — starting fresh", exc)
        return {"notified": {}}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    tmp.replace(p)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fetch_stalls() -> list[dict]:
    url = os.environ.get("OPS_CONSOLE_URL", _DEFAULT_OPS_URL).rstrip("/")
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    if not api_key:
        logger.error("OPS_CONSOLE_API_KEY unset — cannot poll /stalls")
        return []
    req = urllib.request.Request(
        f"{url}/api/dispatch/v2/stalls",
        headers={"X-API-Key": api_key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        logger.warning("stalls fetch HTTP %s: %s", exc.code, exc.reason)
        return []
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("stalls fetch failed: %s", exc)
        return []
    items = body.get("items") or []
    if not isinstance(items, list):
        return []
    return items


# Severity tags drive Teams DM prefix. Critical reasons go above the fold.
_SEVERITY = {
    "silent_stall":            "[ALERT] silent stall",
    "awaiting_human":          "[WARN] question waiting",
    "awaiting_human_critical": "[ESCALATION] question >12h",
    "stale_dispatch":          "[INFO] stale dispatch",
    "review_stuck":            "[WARN] review stuck",
    "never_started":           "[WARN] dispatch never started",
}


def _dedupe_key(item: dict) -> str:
    return "|".join([
        item.get("story_id", "?"),
        item.get("stall_reason", "?"),
        _today_utc(),
    ])


def _format_dm(item: dict) -> tuple[str, list[str]]:
    """Return (severity_label, bullets) for a stall item."""
    reason = item.get("stall_reason", "unknown")
    label = _SEVERITY.get(reason, "[INFO]")
    headline = (
        f"{item.get('story_id', '?')} ({item.get('repo', '?')}) — {reason}"
    )
    bullets = []
    if item.get("last_action"):
        bullets.append(f"last_action: {item['last_action']}")
    if item.get("stalled_since"):
        bullets.append(f"stalled_since: {item['stalled_since']}")
    if item.get("leased_by"):
        bullets.append(f"leased_by: {item['leased_by']}")
    bullets.append(
        "next: `/pm STORY-N` to investigate, "
        "`/answer-needs-info STORY-N` to unblock if needs_info"
    )
    return f"{label} {headline}", bullets


def _print_dm(label_headline: str, bullets: list[str]) -> None:
    """Stub DM output — prints to stdout for cron logs.

    Real Graph API integration mirrors deployment/morris/scripts/interventions.py
    `post_dm`. Wiring it up requires the manage-agent session already used by
    Morris (graph_token_provider). For v1 we surface notifications via stdout
    so the cron log captures them, then escalate to live Graph DM in a
    follow-up. Keeps this notifier deployable without the Graph token plumbing.
    """
    print(label_headline)
    for b in bullets:
        print(f"  - {b}")


def run(*, dry_run: bool) -> int:
    state = _load_state()
    notified = state.get("notified", {})
    items = _fetch_stalls()
    logger.info("fetched %d stalled jobs", len(items))

    new_notifications = 0
    for item in items:
        key = _dedupe_key(item)
        if key in notified:
            continue
        label, bullets = _format_dm(item)
        if dry_run:
            print(f"[DRY-RUN] would notify: {label}")
            for b in bullets:
                print(f"  - {b}")
        else:
            _print_dm(label, bullets)
            notified[key] = datetime.now(timezone.utc).isoformat()
        new_notifications += 1

    # Garbage-collect: drop notification keys older than 7 days so the file
    # does not grow unbounded.
    today = _today_utc()
    keep: dict[str, str] = {}
    for k, ts in notified.items():
        try:
            day = k.rsplit("|", 1)[-1]
            if (datetime.fromisoformat(today) - datetime.fromisoformat(day)).days <= 7:
                keep[k] = ts
        except ValueError:
            keep[k] = ts
    state["notified"] = keep

    if not dry_run:
        _save_state(state)

    logger.info("done — %d new notifications, %d total tracked",
                new_notifications, len(keep))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SLA notifier for stalled dispatch jobs")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print would-notify lines without sending or persisting")
    args = ap.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — never crash the cron host
        logger.error("notifier crashed: %s", exc, exc_info=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
