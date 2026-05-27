-- STORY-532: Add 'needs_info' status to dispatch_items.
-- Idempotent: safe to run multiple times (uses IF NOT EXISTS / DROP IF EXISTS).
--
-- Changes:
--   1. New column needs_info_path TEXT         -- path to QUESTION.md written by agent
--   2. Drop + recreate status CHECK constraint with 'needs_info' included
--   3. Drop + recreate unique active index to include 'needs_info'
--      (prevents re-enqueueing a story while awaiting human answer)

BEGIN;

-- 1. Add needs_info_path column
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS needs_info_path TEXT;

-- 2. Update CHECK constraint to allow 'needs_info'
--    PostgreSQL does not support ALTER CONSTRAINT, so we drop and recreate.
ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS dispatch_items_status_check;
ALTER TABLE dispatch_items ADD CONSTRAINT dispatch_items_status_check
    CHECK (status IN ('pending','claimed','in_review','completed','cancelled','failed','paused','needs_info'));

-- 3. Recreate unique index to block duplicate enqueue while needs_info
--    Old index: WHERE status IN ('pending', 'claimed', 'paused')
--    New index: WHERE status IN ('pending', 'claimed', 'in_review', 'paused', 'needs_info')
DROP INDEX IF EXISTS uq_story_active_idx;
CREATE UNIQUE INDEX uq_story_active_idx ON dispatch_items (story_id)
    WHERE status IN ('pending','claimed','in_review','paused','needs_info');

COMMIT;
