-- STORY-531: Composite (story_id, repo) partial unique index on dispatch_items.
--
-- Problem: the old partial unique index on (story_id) alone collides when
-- the same STORY-N is reused across repos. enqueue()'s terminal-state DELETE
-- matched by story_id alone, silently erasing history for cross-repo story IDs
-- (known collisions: STORY-427, STORY-443, STORY-495, STORY-527).
--
-- Fix: replace the single-column index with a composite (story_id, repo) index
-- covering ALL active states (pending, claimed, in_review, paused).
-- Also closes the 'in_review' gap from migration 004 which only covered
-- (pending, claimed, paused).
--
-- SAFE TO RE-RUN: uses IF NOT EXISTS / IF EXISTS for idempotency.
-- NOT TRANSACTIONAL: CREATE INDEX CONCURRENTLY cannot run inside a BEGIN block.
--   Run this file with: psql -d ops_console -f 007_composite_key_story_repo.sql
--
-- DOWN (one-way hazard — read before running):
--   Safe only if no cross-repo active rows exist. Verify first:
--     SELECT story_id, COUNT(DISTINCT repo)
--       FROM dispatch_items
--      WHERE status IN ('pending','claimed','in_review','paused')
--      GROUP BY story_id HAVING COUNT(DISTINCT repo) > 1;
--   If the above returns rows, rollback is NOT safe (old index would reject
--   the duplicate story_id rows). In that case, drain the queue first.
--
--   DROP INDEX IF EXISTS uq_story_repo_active_idx;
--   DROP INDEX IF EXISTS idx_dispatch_story_repo;
--   CREATE UNIQUE INDEX uq_story_active_idx
--       ON dispatch_items (story_id)
--       WHERE status IN ('pending', 'claimed', 'paused');
--   -- NOTE: down-migration does NOT restore in_review coverage (it was never there).

-- Step 1: Pre-flight — abort if active cross-repo duplicates exist.
-- (Impossible under the current single-column index, but we assert defensively.)
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count FROM (
        SELECT story_id, repo
          FROM dispatch_items
         WHERE status IN ('pending', 'claimed', 'in_review', 'paused')
         GROUP BY story_id, repo
        HAVING COUNT(*) > 1
    ) t;
    IF dup_count > 0 THEN
        RAISE EXCEPTION
            'STORY-531 migration aborted: % (story_id, repo) pair(s) with multiple '
            'active rows detected — manual cleanup required before applying this migration',
            dup_count;
    END IF;
END $$;

-- Step 2: Build the new composite active-state index concurrently.
-- No table lock; safe under concurrent reads/writes.
-- Covers in_review (which the old index silently missed).
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_story_repo_active_idx
    ON dispatch_items (story_id, repo)
    WHERE status IN ('pending', 'claimed', 'in_review', 'paused', 'needs_info');

-- Step 3: Supporting lookup index for (story_id, repo) service-layer queries.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dispatch_story_repo
    ON dispatch_items (story_id, repo);

-- Step 4: Drop the old single-column index — only after the new index is live.
DROP INDEX IF EXISTS uq_story_active_idx;
