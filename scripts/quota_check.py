#!/usr/bin/env python3
"""Check Claude Code token quota for an agent.

Reads ~/.claude/projects/*/*.jsonl session logs (same data source as
claude-monitor) and calculates:
- Current 5-hour block token usage
- Estimated P90 limit from historical blocks
- Remaining tokens and time to reset

Usage:
    python3 quota_check.py                    # run on the agent itself
    ssh -p 443 azureagent@<IP> "sudo -u hermes python3 /opt/agent/quota_check.py"
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_sessions(claude_dir: str = None) -> list[dict]:
    """Load all session JSONL files and extract token usage per session."""
    if claude_dir is None:
        # Ensure HOME is set correctly (sudo -u hermes doesn't always set it)
        home = os.environ.get("HOME", f"/home/{os.environ.get('USER', 'hermes')}")
        claude_dir = os.path.join(home, ".claude", "projects")

    sessions = []
    projects = Path(claude_dir)
    if not projects.exists():
        return sessions

    for jsonl_file in projects.rglob("*.jsonl"):
        try:
            total_input = 0
            total_output = 0
            total_cache_read = 0
            total_cache_create = 0
            timestamp = None

            with open(jsonl_file) as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        # Usage can be at entry.usage or entry.message.usage
                        usage = entry.get("usage") or {}
                        msg = entry.get("message", {})
                        if isinstance(msg, dict):
                            usage = usage or msg.get("usage", {})
                        if usage.get("input_tokens") or usage.get("output_tokens"):
                            total_input += usage.get("input_tokens", 0)
                            total_output += usage.get("output_tokens", 0)
                            total_cache_read += usage.get("cache_read_input_tokens", 0)
                            total_cache_create += usage.get("cache_creation_input_tokens", 0)
                            if not timestamp:
                                ts_str = entry.get("timestamp")
                                if ts_str:
                                    try:
                                        timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                    except Exception:
                                        pass
                                if not timestamp:
                                    timestamp = datetime.fromtimestamp(
                                        jsonl_file.stat().st_mtime, tz=timezone.utc
                                    )
                    except (json.JSONDecodeError, KeyError):
                        continue

            if total_input + total_output > 0 and timestamp:
                sessions.append({
                    "timestamp": timestamp,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cache_read": total_cache_read,
                    "cache_create": total_cache_create,
                    "total": total_input + total_output + total_cache_read + total_cache_create,
                })
        except Exception:
            continue

    return sorted(sessions, key=lambda s: s["timestamp"])


def get_5h_blocks(sessions: list[dict]) -> list[dict]:
    """Group sessions into 5-hour blocks aligned to UTC hours."""
    if not sessions:
        return []

    blocks = {}
    for s in sessions:
        # Round down to nearest 5-hour boundary
        ts = s["timestamp"]
        block_hour = (ts.hour // 5) * 5
        block_start = ts.replace(hour=block_hour, minute=0, second=0, microsecond=0)
        block_key = block_start.isoformat()

        if block_key not in blocks:
            blocks[block_key] = {
                "start": block_start,
                "end": block_start + timedelta(hours=5),
                "total_tokens": 0,
                "sessions": 0,
            }
        blocks[block_key]["total_tokens"] += s["total"]
        blocks[block_key]["sessions"] += 1

    return sorted(blocks.values(), key=lambda b: b["start"])


def main():
    sessions = load_sessions()
    if not sessions:
        print(json.dumps({"error": "no sessions found"}))
        return

    blocks = get_5h_blocks(sessions)
    now = datetime.now(timezone.utc)

    # Find active block
    active = None
    for b in blocks:
        if b["start"] <= now < b["end"]:
            active = b
            break

    # P90 limit from historical blocks (last 8 days = ~38 blocks)
    recent_blocks = [b for b in blocks if b["start"] > now - timedelta(days=8)]
    if len(recent_blocks) >= 3:
        sorted_totals = sorted([b["total_tokens"] for b in recent_blocks])
        p90_idx = int(len(sorted_totals) * 0.9)
        p90_limit = sorted_totals[min(p90_idx, len(sorted_totals) - 1)]
    else:
        p90_limit = None

    result = {
        "active_block": None,
        "p90_limit": p90_limit,
        "total_blocks_analyzed": len(recent_blocks),
    }

    if active:
        remaining_minutes = int((active["end"] - now).total_seconds() / 60)
        pct_used = (active["total_tokens"] / p90_limit * 100) if p90_limit else None
        remaining_tokens = (p90_limit - active["total_tokens"]) if p90_limit else None

        result["active_block"] = {
            "tokens": active["total_tokens"],
            "sessions": active["sessions"],
            "start": active["start"].strftime("%H:%M UTC"),
            "end": active["end"].strftime("%H:%M UTC"),
            "reset_in_minutes": remaining_minutes,
            "percent_used": round(pct_used, 1) if pct_used else None,
            "remaining_tokens": remaining_tokens,
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
