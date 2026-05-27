#!/usr/bin/env python3
"""DLQ triage: query recent dispatch_items failures and output JSON to stdout.

STORY-701: Invoked by Morris's daily 10am UTC cron. Morris's skill formats
the Teams DM to Mark using the JSON produced here.

Usage:
    DATABASE_URL=postgresql://... python3 dlq_triage.py

Outputs a JSON array of failed dispatch items from the last 7 days that have
a classified failure_reason. Items are ordered newest-first (completed_at DESC).
"""
import asyncio
import json
import os
import sys

import asyncpg

QUERY = """
    -- Migrated 2026-05-03 to v2 event log (was: dispatch_items.status='failed').
    -- The v2 trigger requires failure_class + failure_reason on every failed
    -- event, so the IS NOT NULL filter is structural rather than defensive.
    SELECT j.story_id,
           j.repo,
           e.event_data->>'failure_reason' AS failure_reason,
           e.event_data->>'agent' AS claimed_by,
           e.occurred_at AS failed_at,
           j.prompt
    FROM dispatch_v2_events e
    JOIN dispatch_jobs j ON j.job_id = e.job_id
    WHERE e.event_type = 'failed'
      AND e.event_data->>'failure_reason' IS NOT NULL
      AND e.occurred_at > now() - interval '7 days'
    ORDER BY e.occurred_at DESC
    LIMIT 25
"""


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(QUERY)
        result = [dict(r) for r in rows]
        # Convert datetimes to ISO strings for JSON serialization
        for row in result:
            if row.get("failed_at"):
                row["failed_at"] = row["failed_at"].isoformat()
        print(json.dumps(result, indent=2))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
