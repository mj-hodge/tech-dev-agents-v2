-- STORY-507: Add paused status to dispatch_items table.
-- Idempotent: safe to run multiple times (uses IF NOT EXISTS / DROP IF EXISTS).
--
-- Changes:
--   1. New column paused_at TIMESTAMPTZ      -- when the story was paused
--   2. New column current_phase INTEGER       -- last phase in progress
--   3. Drop + recreate status CHECK constraint with 'paused' included
--   4. Drop + recreate unique active index to include 'paused'
--      (prevents enqueueing a duplicate while a story is paused)

BEGIN;

-- 1. Add paused_at column
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;

-- 2. Add current_phase column
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS current_phase INTEGER;

-- 3. Update CHECK constraint to allow 'paused'
--    PostgreSQL does not support ALTER CONSTRAINT, so we drop and recreate.
--    Replay-safety: include statuses introduced by later migrations so
--    re-running 004 on a live DB cannot fail on existing rows.
ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS dispatch_items_status_check;
ALTER TABLE dispatch_items
    ADD CONSTRAINT dispatch_items_status_check
    CHECK (
        status IN (
            'pending',
            'claimed',
            'in_review',
            'completed',
            'cancelled',
            'failed',
            'paused',
            'needs_info'
        )
    );

-- 4. Recreate unique index to block duplicate enqueue while paused
--    Old index: WHERE status IN ('pending', 'claimed')
--    New index: WHERE status IN ('pending', 'claimed', 'paused')
DROP INDEX IF EXISTS uq_story_active_idx;
CREATE UNIQUE INDEX uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed', 'paused');

COMMIT;
