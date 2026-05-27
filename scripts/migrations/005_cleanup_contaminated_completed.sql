-- STORY-507 AC-13: Dry-run cleanup of contaminated 'completed' rows.
--
-- Context: On 2026-04-15, 7 stories were fraudulently marked complete without
-- a real commit_sha. These rows bypassed the proof-of-work guard (added in
-- STORY-253) and should be re-labelled as 'failed' so they can be re-dispatched.
--
-- SAFE TO RUN AS-IS: This script only SELECTs (dry-run).
-- The UPDATE is commented out -- review SELECT output with Mark before enabling.
--
-- Usage:
--   1. Run this script to preview affected rows
--   2. Review output with Mark
--   3. Uncomment the UPDATE block and re-run to re-label

-- Step 1: Count contaminated rows
SELECT COUNT(*) AS contaminated_count
FROM dispatch_items
WHERE status = 'completed'
  AND commit_sha IS NULL;

-- Step 2: Preview all contaminated rows (most recent first)
SELECT
    story_id,
    repo,
    scope,
    status,
    completed_at,
    commit_sha,
    enqueued_by,
    claimed_by
FROM dispatch_items
WHERE status = 'completed'
  AND commit_sha IS NULL
ORDER BY completed_at DESC;

-- Step 3: Re-label to 'failed' (COMMENTED OUT -- confirm dry-run first)
-- UPDATE dispatch_items
--    SET status = 'failed',
--        updated_at = now()
--  WHERE status = 'completed'
--    AND commit_sha IS NULL;
