#!/usr/bin/env bash
# STORY-700: Weekly retention — delete dispatch_events older than 120 days.
# Batched 10k rows per loop to avoid long-running transactions.
# Schedule: 0 4 * * 0 (Sunday 04:00 UTC) on Morris's cron.
set -euo pipefail

psql "${DATABASE_URL}" <<'SQL'
DO $$
DECLARE rows_deleted int;
BEGIN
  LOOP
    DELETE FROM dispatch_events
    WHERE ctid IN (
      SELECT ctid FROM dispatch_events
      WHERE ts < now() - interval '120 days'
      LIMIT 10000
    );
    GET DIAGNOSTICS rows_deleted = ROW_COUNT;
    EXIT WHEN rows_deleted = 0;
  END LOOP;
END $$;
SQL
