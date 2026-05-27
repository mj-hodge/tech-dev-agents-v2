-- STORY-496: Add in_review status for dispatch items
-- Safe to run on live DB — additive only (new constraint value + new column)

-- Step 1: Drop and recreate constraint with in_review
ALTER TABLE dispatch_items
    DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE dispatch_items
    ADD CONSTRAINT valid_status
        CHECK (status IN ('pending', 'claimed', 'in_review', 'completed', 'cancelled', 'failed'));

-- Step 2: Add review_started_at column
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS review_started_at TIMESTAMPTZ;

-- Step 3: Partial index for in_review queries
CREATE INDEX IF NOT EXISTS idx_dispatch_in_review
    ON dispatch_items(status) WHERE status = 'in_review';

-- Rollback (run to revert):
-- UPDATE dispatch_items SET status = 'claimed' WHERE status = 'in_review';
-- ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS valid_status;
-- ALTER TABLE dispatch_items ADD CONSTRAINT valid_status
--     CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled', 'failed'));
-- ALTER TABLE dispatch_items DROP COLUMN IF EXISTS review_started_at;
-- DROP INDEX IF EXISTS idx_dispatch_in_review;
