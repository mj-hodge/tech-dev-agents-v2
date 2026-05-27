#!/usr/bin/env python3
"""Fleet status script — queries Loki for dispatch poller activity.

STORY-031: Dispatch Queue Monitoring & Logging Fix

Queries Grafana Loki for recent [DISPATCH] log lines and displays
a summary of poller activity: claimed, completed, failed, errors,
and queue-empty cycles per agent.

Usage:
    python3 scripts/fleet_status.py [--hours N] [--agent NAME]

Environment:
    LOKI_URL  — Loki query endpoint (default: https://grafana.gorillacommerce.ai)
    LOKI_USER — Loki basic auth user (optional)
    LOKI_PASS — Loki basic auth password (optional)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LOKI_URL = os.environ.get(
    "LOKI_URL", "https://grafana.gorillacommerce.ai"
)
LOKI_USER = os.environ.get("LOKI_USER", "")
LOKI_PASS = os.environ.get("LOKI_PASS", "")


# ---------------------------------------------------------------------------
# Loki query helper
# ---------------------------------------------------------------------------


def query_loki(
    query: str,
    start_ns: int,
    end_ns: int,
    limit: int = 1000,
) -> list[dict]:
    """Execute a Loki query_range and return parsed log entries."""
    url = f"{LOKI_URL}/loki/api/v1/query_range"
    params = {
        "query": query,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(limit),
        "direction": "backward",
    }
    auth = (LOKI_USER, LOKI_PASS) if LOKI_USER and LOKI_PASS else None

    try:
        resp = requests.get(url, params=params, auth=auth, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Error querying Loki: {exc}", file=sys.stderr)
        return []

    data = resp.json()
    entries = []
    for stream in data.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            entries.append({
                "timestamp": ts,
                "line": line,
                "agent": labels.get("agent", "unknown"),
            })
    return entries


# ---------------------------------------------------------------------------
# Classify [DISPATCH] lines
# ---------------------------------------------------------------------------


def is_genuine_dispatch_line(line: str) -> bool:
    """Check if a line is a genuine [DISPATCH] log line (not conversation noise).

    Genuine lines start with [DISPATCH] (possibly after a timestamp).
    Lines from [Claude Code] or other non-dispatch sources are excluded.
    """
    stripped = line.strip()
    # Genuine: starts with [DISPATCH]
    if stripped.startswith("[DISPATCH]"):
        return True
    # Journal format: "Apr 13 00:04:02 vm-xxx bash[NNN]: [DISPATCH] ..."
    if "[DISPATCH]" in stripped and "[Claude Code]" not in stripped:
        # Check it's a syslog/journal line, not conversation text
        import re
        if re.match(r"^[A-Z][a-z]{2} \d+ \d+:\d+:\d+ ", stripped):
            return True
    return False


def classify_dispatch_line(line: str) -> str:
    """Classify a [DISPATCH] log line into a category."""
    lower = line.lower()
    if "[dispatch] completed" in lower:
        return "completed"
    if "[dispatch] failed" in lower:
        return "failed"
    if "[dispatch] claimed" in lower:
        return "claimed"
    if "[dispatch] queue empty" in lower:
        return "empty"
    if "[dispatch] busy" in lower:
        return "busy"
    if "[dispatch] error" in lower or "[dispatch] unexpected" in lower:
        return "error"
    if "[dispatch] starting sdk" in lower:
        return "started"
    if "[dispatch] found" in lower:
        return "found"
    if "[dispatch] starting polling" in lower:
        return "startup"
    if "[dispatch] poller thread" in lower:
        return "startup"
    if "[dispatch] cleared" in lower:
        return "cleared"
    if "[dispatch] complete" in lower or "/complete" in lower:
        return "completed"
    if "[dispatch] sdk exited" in lower:
        return "completed"
    if "[dispatch] warning" in lower:
        return "warning"
    if "[dispatch] missing" in lower:
        return "startup"
    return "other"


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def print_dispatch_summary(
    entries: list[dict],
    agent_filter: str | None = None,
) -> None:
    """Print a summary table of dispatch activity."""
    # Group by agent
    by_agent: dict[str, dict[str, int]] = {}
    for entry in entries:
        agent = entry["agent"]
        if agent_filter and agent != agent_filter:
            continue
        category = classify_dispatch_line(entry["line"])
        if agent not in by_agent:
            by_agent[agent] = {}
        by_agent[agent][category] = by_agent[agent].get(category, 0) + 1

    if not by_agent:
        print("  No [DISPATCH] activity found in the specified time range.")
        return

    # Print per-agent summary
    for agent in sorted(by_agent):
        counts = by_agent[agent]
        total = sum(counts.values())
        print(f"\n  Agent: {agent} ({total} total events)")
        print(f"  {'Category':<15} {'Count':>6}")
        print(f"  {'-'*15} {'-'*6}")
        for cat in ["claimed", "completed", "failed", "started", "found",
                     "empty", "busy", "error", "warning", "cleared",
                     "startup", "other"]:
            if cat in counts:
                print(f"  {cat:<15} {counts[cat]:>6}")

    # Print recent lines
    print(f"\n  Recent [DISPATCH] lines (last 20):")
    print(f"  {'-'*70}")
    recent = sorted(entries, key=lambda e: e["timestamp"], reverse=True)[:20]
    for entry in recent:
        # Convert nanosecond timestamp to readable
        try:
            ts_sec = int(entry["timestamp"]) / 1e9
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, OSError):
            ts_str = entry["timestamp"]

        agent = entry["agent"]
        line = entry["line"].strip()
        # Truncate long lines
        if len(line) > 100:
            line = line[:97] + "..."
        print(f"  [{ts_str}] {agent}: {line}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query Loki for dispatch poller activity"
    )
    parser.add_argument(
        "--hours", type=float, default=2,
        help="Look back N hours (default: 2)"
    )
    parser.add_argument(
        "--agent", type=str, default=None,
        help="Filter to a specific agent name"
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Print raw log lines instead of summary"
    )
    args = parser.parse_args()

    end_ns = int(time.time() * 1e9)
    start_ns = int((time.time() - args.hours * 3600) * 1e9)

    print(f"\n  Dispatch Poller Status (last {args.hours}h)")
    print(f"  {'='*50}")

    # Query 1: [DISPATCH] lines from hermes-gateway job
    # Filter to lines starting with [DISPATCH] and exclude [Claude Code] noise
    query = '{job="hermes-gateway"} |~ "\\\\[DISPATCH\\\\]" !~ "\\\\[Claude Code\\\\]"'
    print(f"\n  Querying Loki: {query}")
    entries = query_loki(query, start_ns, end_ns)

    # Query 2: Also check dispatch-poller dedicated job
    query2 = '{job="dispatch-poller"}'
    entries2 = query_loki(query2, start_ns, end_ns)

    # Query 3: Journal logs
    query3 = '{job="dispatch-poller-journal"}'
    entries3 = query_loki(query3, start_ns, end_ns)

    # Merge, deduplicate, and filter to genuine [DISPATCH] lines only
    seen = set()
    all_entries = []
    for entry in entries + entries2 + entries3:
        key = (entry["timestamp"], entry["agent"])
        if key not in seen and is_genuine_dispatch_line(entry["line"]):
            seen.add(key)
            all_entries.append(entry)

    print(f"  Found {len(all_entries)} [DISPATCH] log entries")

    if args.raw:
        for entry in sorted(all_entries, key=lambda e: e["timestamp"]):
            print(f"  {entry['agent']}: {entry['line'].strip()}")
    else:
        print_dispatch_summary(all_entries, agent_filter=args.agent)

    print()

    # Token quota check via SSH + quota_check.py
    print("  Token Quota (5-hour billing window)")
    print("  " + "=" * 50)
    import subprocess as _sp
    import json as _json
    agents_ssh = {
        "dan": "20.228.224.243",
        "derrick": "20.121.210.186",
        "daisy": "20.98.231.234",
        "devon": "20.186.26.130",
        "morris": "20.246.36.143",
    }
    for name, ip in agents_ssh.items():
        try:
            result = _sp.run(
                ["ssh", "-p", "443", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=8", f"azureagent@{ip}",
                 "sudo -u hermes HOME=/home/hermes python3 /opt/agent/quota_check.py"],
                capture_output=True, text=True, timeout=30,
            )
            if result.stdout.strip():
                data = _json.loads(result.stdout)
                if data.get("error"):
                    print(f"  {name:10} {data['error']}")
                    continue
                block = data.get("active_block")
                p90 = data.get("p90_limit")
                if block:
                    tokens = block["tokens"]
                    reset = block["reset_in_minutes"]
                    pct = block.get("percent_used")
                    remaining = block.get("remaining_tokens")
                    pct_str = f"{pct:.0f}%" if pct else "?"
                    remaining_str = f"{remaining:,}" if remaining else "?"
                    status = "OK" if (pct or 0) < 50 else ("WARN" if (pct or 0) < 80 else "HIGH")
                    print(f"  {name:10} {tokens:>12,} tokens  {pct_str:>5} used  resets in {reset}m  remaining: {remaining_str}  [{status}]")
                else:
                    p90_str = f"(P90 limit: {p90:,})" if p90 else ""
                    print(f"  {name:10} no active block {p90_str}")
            else:
                print(f"  {name:10} quota_check unavailable")
        except Exception as e:
            print(f"  {name:10} unreachable ({e})")
    print()


if __name__ == "__main__":
    main()
