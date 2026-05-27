-- STORY-028: Dispatch Queue Database Migration
-- Creates the dispatch_items table for PostgreSQL-backed dispatch queue.
-- Run once: psql -U ops_console -d ops_console -f 001_dispatch_items.sql

CREATE TABLE IF NOT EXISTS dispatch_items (
    id              SERIAL PRIMARY KEY,
    story_id        VARCHAR(20) NOT NULL,
    repo            VARCHAR(200) NOT NULL,
    scope           VARCHAR(10) NOT NULL DEFAULT 'small',
    prompt          TEXT NOT NULL,
    title           VARCHAR(200),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enqueued_by     VARCHAR(50) NOT NULL,
    claimed_by      VARCHAR(50),
    claimed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    cost_usd        NUMERIC(10, 4),
    turns           INTEGER,
    duration_seconds INTEGER,
    source          VARCHAR(20) NOT NULL DEFAULT 'queue',
    title           VARCHAR(200),
    error           BOOLEAN DEFAULT FALSE,
    error_message   TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT valid_status CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled', 'failed')),
    CONSTRAINT valid_source CHECK (source IN ('queue', 'teams', 'ssh', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_dispatch_status ON dispatch_items(status);
CREATE INDEX IF NOT EXISTS idx_dispatch_agent ON dispatch_items(claimed_by);
CREATE INDEX IF NOT EXISTS idx_dispatch_story ON dispatch_items(story_id);
CREATE INDEX IF NOT EXISTS idx_dispatch_enqueued ON dispatch_items(enqueued_at);

-- Optional: One-time migration from JSON file
-- Run this manually if there are pending/claimed items in dispatch-queue.json
-- INSERT INTO dispatch_items (story_id, repo, scope, prompt, status, enqueued_at, enqueued_by, claimed_by, claimed_at, source)
-- VALUES ('STORY-XXX', 'repo', 'small', 'prompt', 'pending', NOW(), 'mark', NULL, NULL, 'queue');
