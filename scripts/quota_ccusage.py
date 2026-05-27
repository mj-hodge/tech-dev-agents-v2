#!/usr/bin/env python3
"""Claude Code quota probe — ccusage-backed with JSONL fallback.

Deployed to /opt/agent/quota_ccusage.py on each dev-agent VM.

Usage:
    sudo -u hermes python3 /opt/agent/quota_ccusage.py          # current 5-hour block
    sudo -u hermes python3 /opt/agent/quota_ccusage.py --weekly  # 7-day trend

Contract:
  - Always exits 0 (never crashes the SSH caller).
  - Prints exactly ONE line of valid JSON to stdout at the end.
  - JSON always contains a "source" field: "ccusage" | "jsonl" | "unavailable".
  - When source="unavailable", all numeric fields are null.
  - When --weekly, JSON contains "days" list (exactly 7 entries, padded if short).
  - All intermediate status / debug lines go to stderr (discarded by SSH caller).

Data source precedence:
  1. ccusage blocks --json          (source = "ccusage")
  2. quota_check.py JSONL parsing   (source = "jsonl")
  3. unavailable sentinel            (source = "unavailable")
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def _parse_last_json_line(text: str) -> dict | None:
    """Scan lines from the end and return the first one that parses as a JSON object.

    Handles ccusage stdout that contains a spinner prefix (e.g. "⠋ Loading...")
    before the actual JSON payload.  Returns None if no parseable JSON found.
    """
    if not text or not text.strip():
        return None

    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            result = json.loads(line)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue

    return None


# ---------------------------------------------------------------------------
# Unavailable sentinel
# ---------------------------------------------------------------------------


def _emit_unavailable(weekly: bool = False, reason: str | None = None) -> dict:
    """Return the unavailable sentinel payload (all numerics null)."""
    payload: dict = {"source": "unavailable", "error": reason}
    if not weekly:
        payload.update({
            "current_block_tokens": None,
            "current_block_cost_usd": None,
            "time_remaining_minutes": None,
            "block_start": None,
            "block_end": None,
            "p90_limit": None,
            "sessions_in_block": None,
            "percent_used": None,
            "remaining_tokens": None,
        })
    else:
        payload.update({
            "total_tokens": None,
            "total_cost_usd": None,
            "days": [],
        })
    return payload


# ---------------------------------------------------------------------------
# JSONL fallback (wraps quota_check.py)
# ---------------------------------------------------------------------------


def _jsonl_fallback_quota() -> dict | None:
    """Get current-block quota from quota_check.py's JSONL parser.

    Returns a normalised dict on success, None on any failure.
    """
    try:
        # quota_check.py lives alongside this script on the agent VM
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        from quota_check import get_5h_blocks, load_sessions  # type: ignore[import]

        sessions = load_sessions()
        if not sessions:
            return None

        blocks = get_5h_blocks(sessions)
        now = datetime.now(timezone.utc)

        # Find active block
        active = None
        for b in blocks:
            if b["start"] <= now < b["end"]:
                active = b
                break

        # P90 limit from recent history
        recent = [b for b in blocks if b["start"] > now - timedelta(days=8)]
        p90_limit: int | None = None
        if len(recent) >= 3:
            sorted_totals = sorted(b["total_tokens"] for b in recent)
            idx = int(len(sorted_totals) * 0.9)
            p90_limit = sorted_totals[min(idx, len(sorted_totals) - 1)]

        if active is None:
            return {
                "source": "jsonl",
                "current_block_tokens": None,
                "current_block_cost_usd": None,
                "time_remaining_minutes": None,
                "block_start": None,
                "block_end": None,
                "p90_limit": p90_limit,
                "sessions_in_block": None,
                "percent_used": None,
                "remaining_tokens": None,
                "error": "no active block",
            }

        remaining_min = int((active["end"] - now).total_seconds() / 60)
        tokens: int = active["total_tokens"]
        percent = round(tokens / p90_limit * 100, 1) if p90_limit else None
        remaining_tokens = (p90_limit - tokens) if p90_limit else None

        return {
            "source": "jsonl",
            "current_block_tokens": tokens,
            "current_block_cost_usd": None,  # JSONL doesn't track cost
            "time_remaining_minutes": remaining_min,
            "block_start": active["start"].strftime("%H:%M UTC"),
            "block_end": active["end"].strftime("%H:%M UTC"),
            "p90_limit": p90_limit,
            "sessions_in_block": active.get("sessions", 0),
            "percent_used": percent,
            "remaining_tokens": remaining_tokens,
            "error": None,
        }
    except Exception:
        return None


def _jsonl_fallback_weekly() -> dict | None:
    """Get 7-day weekly usage from quota_check.py's JSONL parser.

    Returns a normalised dict on success, None on any failure.
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        from quota_check import load_sessions  # type: ignore[import]

        sessions = load_sessions()
        if not sessions:
            return None

        now = datetime.now(timezone.utc)
        today = now.date()

        # Build 7-day window (oldest → newest) ending today
        days_map: dict[str, dict] = {}
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            days_map[d.isoformat()] = {"tokens": 0, "cost_usd": 0.0, "blocks_used": 0}

        for s in sessions:
            date_key = s["timestamp"].date().isoformat()
            if date_key in days_map:
                days_map[date_key]["tokens"] += s["total"]

        days = [
            {
                "date": date_str,
                "tokens": days_map[date_str]["tokens"],
                "cost_usd": 0.0,
                "blocks_used": 0,
            }
            for date_str in sorted(days_map.keys())
        ]
        total_tokens = sum(d["tokens"] for d in days)

        return {
            "source": "jsonl",
            "total_tokens": total_tokens,
            "total_cost_usd": 0.0,
            "days": days,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ccusage parsers
# ---------------------------------------------------------------------------


def _parse_ccusage_blocks(stdout: str) -> dict | None:
    """Parse `ccusage blocks --json` stdout into our normalised schema.

    Returns None if stdout cannot be parsed (triggers JSONL fallback).
    """
    payload = _parse_last_json_line(stdout)
    if payload is None:
        return None

    active = payload.get("activeBlock")
    p90_limit = payload.get("p90") or payload.get("p90Limit")

    now = datetime.now(timezone.utc)

    if active is None:
        # ccusage ran successfully but no active block (agent idle)
        return {
            "source": "ccusage",
            "current_block_tokens": None,
            "current_block_cost_usd": None,
            "time_remaining_minutes": None,
            "block_start": None,
            "block_end": None,
            "p90_limit": p90_limit,
            "sessions_in_block": None,
            "percent_used": None,
            "remaining_tokens": None,
            "error": "no active block",
        }

    tokens = active.get("totalTokens") or active.get("tokens")
    cost = active.get("totalCost") or active.get("cost")
    sessions = active.get("sessions") or active.get("sessionCount") or 0

    # Parse block timestamps
    time_remaining: int | None = None
    block_start_str: str | None = None
    block_end_str: str | None = None

    start_time_str = active.get("startTime") or active.get("start")
    end_time_str = active.get("endTime") or active.get("end")

    if start_time_str:
        try:
            start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            block_start_str = start_dt.strftime("%H:%M UTC")
        except Exception:
            pass

    if end_time_str:
        try:
            end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
            block_end_str = end_dt.strftime("%H:%M UTC")
            remaining_sec = (end_dt - now).total_seconds()
            time_remaining = max(0, int(remaining_sec / 60))
        except Exception:
            pass

    percent: float | None = None
    remaining_tokens: int | None = None
    if tokens is not None and p90_limit:
        percent = round(tokens / p90_limit * 100, 1)
        remaining_tokens = p90_limit - tokens

    return {
        "source": "ccusage",
        "current_block_tokens": tokens,
        "current_block_cost_usd": cost,
        "time_remaining_minutes": time_remaining,
        "block_start": block_start_str,
        "block_end": block_end_str,
        "p90_limit": p90_limit,
        "sessions_in_block": sessions,
        "percent_used": percent,
        "remaining_tokens": remaining_tokens,
        "error": None,
    }


def _parse_ccusage_weekly(stdout: str) -> dict | None:
    """Parse `ccusage --period weekly --format json` stdout into our schema.

    Pads to exactly 7 days ending today (UTC). Returns None on parse failure.
    """
    payload = _parse_last_json_line(stdout)
    if payload is None:
        return None

    raw_days = payload.get("daily") or payload.get("days") or []

    # Build a keyed map of days from the raw ccusage output
    days_map: dict[str, dict] = {}
    for d in raw_days:
        date = d.get("date") or d.get("day")
        if not date:
            continue
        tokens = int(d.get("totalTokens") or d.get("tokens") or 0)
        cost = float(d.get("totalCost") or d.get("cost_usd") or d.get("cost") or 0.0)
        blocks = int(
            d.get("blocks") or d.get("blocksUsed") or d.get("blocks_used") or 0
        )
        days_map[str(date)] = {"tokens": tokens, "cost_usd": cost, "blocks_used": blocks}

    # Pad to exactly 7 days ending today UTC (oldest → newest)
    today = datetime.now(timezone.utc).date()
    result_days = []
    for i in range(6, -1, -1):
        date_str = (today - timedelta(days=i)).isoformat()
        if date_str in days_map:
            result_days.append({
                "date": date_str,
                "tokens": days_map[date_str]["tokens"],
                "cost_usd": days_map[date_str]["cost_usd"],
                "blocks_used": days_map[date_str]["blocks_used"],
            })
        else:
            result_days.append({
                "date": date_str,
                "tokens": 0,
                "cost_usd": 0.0,
                "blocks_used": 0,
            })

    # Use ccusage-provided totals when available; otherwise sum padded days
    total_tokens = int(
        payload.get("totalTokens") or payload.get("total_tokens")
        or sum(d["tokens"] for d in result_days)
    )
    total_cost = float(
        payload.get("totalCost") or payload.get("total_cost_usd")
        or sum(d["cost_usd"] for d in result_days)
    )

    return {
        "source": "ccusage",
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "days": result_days,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(weekly: bool = False) -> None:
    """Probe quota and print one JSON line to stdout. Never raises."""
    result: dict | None = None

    try:
        # 1. Check if ccusage binary is available
        try:
            ccusage_path = shutil.which("ccusage")
        except Exception:
            ccusage_path = None

        if ccusage_path:
            # 2. Try ccusage
            try:
                if not weekly:
                    proc = subprocess.run(
                        ["ccusage", "blocks", "--json"],
                        capture_output=True,
                        text=True,
                        timeout=8,
                    )
                    if proc.returncode == 0 and proc.stdout:
                        result = _parse_ccusage_blocks(proc.stdout)
                else:
                    proc = subprocess.run(
                        ["ccusage", "--period", "weekly", "--format", "json"],
                        capture_output=True,
                        text=True,
                        timeout=8,
                    )
                    if proc.returncode == 0 and proc.stdout:
                        result = _parse_ccusage_weekly(proc.stdout)
            except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
                result = None

        # 3. JSONL fallback
        if result is None:
            if not weekly:
                result = _jsonl_fallback_quota()
            else:
                result = _jsonl_fallback_weekly()

        # 4. Unavailable sentinel (last resort)
        if result is None:
            result = _emit_unavailable(weekly=weekly, reason="all sources failed")

    except Exception as exc:  # pragma: no cover — belt-and-suspenders
        result = _emit_unavailable(weekly=weekly, reason=str(exc))

    print(json.dumps(result))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Claude Code quota probe (ccusage-backed)"
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Return 7-day weekly trend instead of current block",
    )
    args = parser.parse_args()
    main(weekly=args.weekly)
