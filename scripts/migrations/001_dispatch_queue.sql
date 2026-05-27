-- STORY-028: Dispatch Queue Database Persistence
-- Idempotent: safe to run multiple times (CREATE IF NOT EXISTS)
--
-- Tables:
--   dispatch_items — replaces dispatch-queue.json
--   agents         — auto-registered agents from dispatch polling

-- Dispatch items table
CREATE TABLE IF NOT EXISTS dispatch_items (
    id            BIGSERIAL PRIMARY KEY,
    story_id      TEXT NOT NULL,
    repo          TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'small'
        CHECK (scope IN ('small', 'medium', 'large')),
    prompt        TEXT NOT NULL,
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    enqueued_by   TEXT NOT NULL DEFAULT 'mark',
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled', 'failed')),
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    cancelled_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    title         VARCHAR(200)
);

-- Partial unique index: only one active (non-terminal) dispatch per story_id
CREATE UNIQUE INDEX IF NOT EXISTS uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed');

-- Query indexes
CREATE INDEX IF NOT EXISTS idx_dispatch_status ON dispatch_items (status);
CREATE INDEX IF NOT EXISTS idx_dispatch_enqueued_at ON dispatch_items (enqueued_at);
CREATE INDEX IF NOT EXISTS idx_dispatch_claimed_by ON dispatch_items (claimed_by);

-- Agent registry table
CREATE TABLE IF NOT EXISTS agents (
    name           TEXT PRIMARY KEY,
    email          TEXT,
    vm             TEXT,
    ip             TEXT,
    ssh_port       INTEGER DEFAULT 443,
    status         TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'draining')),
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_via TEXT DEFAULT 'auto'
        CHECK (registered_via IN ('manual', 'auto'))
);
