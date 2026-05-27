-- Migration 014: dispatch_quarantine table for auto-quarantine (Epic-Queue-v2 Phase 0)
--
-- When a (story_id, repo) pair triggers > 5 claim-409s in 120s, the item is
-- force-released and a quarantine row is inserted here.  The /next endpoint
-- excludes items whose (story_id, repo) has an active quarantine
-- (cleared_at IS NULL).  Operators clear quarantines via
-- POST /api/dispatch/quarantine/{id}/clear.

CREATE TABLE IF NOT EXISTS dispatch_quarantine (
    id             SERIAL PRIMARY KEY,
    story_id       TEXT        NOT NULL,
    repo           TEXT        NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason         TEXT,
    cleared_at     TIMESTAMPTZ
);

-- Fast lookup for the /next exclusion query
CREATE INDEX IF NOT EXISTS idx_dispatch_quarantine_active
    ON dispatch_quarantine (story_id, repo)
    WHERE cleared_at IS NULL;
