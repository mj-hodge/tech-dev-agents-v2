#!/usr/bin/env python3
"""Drain gate for the Epic-Queue-v2 v1 -> v2 cutover.

Story Q5: a one-shot script that an operator runs **before** flipping
DISPATCH_PROTOCOL=v2 on an agent VM (or fleet-wide). It refuses to give the
all-clear while any v1 dispatch_items row is still in an active status, so we
never strand a claimed/in-review/paused story when the new poller comes up.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db \
        python3 deployment/hermes/drain_v1_queue.py

Exit codes:
    0  v1 queue is empty (no active rows). Safe to flip DISPATCH_PROTOCOL=v2.
    1  v1 queue still has active rows. DO NOT flip yet — drain or reassign first.
    2  Configuration / connection error (DATABASE_URL missing, DB unreachable).

This script intentionally does NOT mutate any rows. The cutover playbook is:

    1. Stop new dispatches into v1 (operator decision / dispatch tooling).
    2. Wait for in-flight v1 stories to reach a terminal status
       (completed / cancelled / failed) or get migrated to v2 by hand.
    3. Run this script. Loop until exit 0.
    4. Edit /opt/agent/.env on each agent: set DISPATCH_PROTOCOL=v2.
    5. systemctl restart dispatch-poller.

Active statuses (per dispatch_db_service.count_active_stories + the pending
row carve-out): pending, claimed, in_review, paused, needs_info.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

# v1 statuses that prevent a clean flip. Mirrors the active-set used by
# DispatchDBService.count_active_stories plus 'pending' (rows that haven't
# been picked up yet but would be silently ignored by the v2 poller because
# v2 reads from dispatch_jobs, not dispatch_items).
ACTIVE_STATUSES: tuple[str, ...] = (
    "pending",
    "claimed",
    "in_review",
    "paused",
    "needs_info",
)

# Postgres parameter placeholder list for ANY($1::text[]) style query.
_QUERY = """
    SELECT status, COUNT(*) AS n
    FROM dispatch_items
    WHERE status = ANY($1::text[])
    GROUP BY status
    ORDER BY status
"""


async def _check(db_url: str) -> int:
    """Connect, count active v1 rows, print human-readable report.

    Returns the script exit code (0 clean, 1 dirty, 2 connection failure).
    """
    try:
        conn = await asyncpg.connect(db_url)
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"ERROR: cannot connect to database: {exc}", file=sys.stderr)
        return 2

    try:
        rows = await conn.fetch(_QUERY, list(ACTIVE_STATUSES))
    finally:
        await conn.close()

    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())

    if total == 0:
        print("v1 queue is empty — safe to switch DISPATCH_PROTOCOL=v2")
        return 0

    print(f"WARNING: v1 dispatch_items still has {total} active row(s):")
    for status in ACTIVE_STATUSES:
        n = counts.get(status, 0)
        if n:
            print(f"  {status:>11}: {n}")
    print()
    print("Drain or reassign these rows before flipping DISPATCH_PROTOCOL=v2.")
    print("Inspect with:")
    print("  SELECT story_id, status, claimed_by, claimed_at, updated_at")
    print(f"    FROM dispatch_items WHERE status = ANY('{{{','.join(ACTIVE_STATUSES)}}}'::text[])")
    print("    ORDER BY updated_at DESC;")
    return 1


def main() -> int:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("OPS_DATABASE_URL")
    if not db_url:
        print(
            "ERROR: DATABASE_URL (or OPS_DATABASE_URL) environment variable is not set.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(_check(db_url))


if __name__ == "__main__":
    sys.exit(main())
