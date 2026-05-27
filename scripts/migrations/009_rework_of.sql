-- STORY-rework-plumbing: add rework_of column to dispatch_items.
--
-- Problem: when ops-console dispatches a rework (a fix for an existing PR),
-- the row carries no link to the base story. The phase runner then can't
-- check out the existing PR branch, SDLC deliverables land in a fresh
-- features/story-<rework-id>/ folder, and the deliverable verifier rejects
-- the run with a 422 gate failure. On 2026-04-24, 19 of 46 dispatch
-- failures were this class (STORY-570/571/572 all exhausted 3 retries).
--
-- Fix: add ``rework_of VARCHAR(20)`` nullable. The poller reads it from
-- /dispatch/next, threads it through start_story → run_sdlc_phases →
-- _ensure_branch / _extract_story_folder. Non-rework dispatches leave it
-- NULL and behavior is unchanged.
--
-- SAFE TO RE-RUN: uses IF NOT EXISTS for idempotency.
-- TRANSACTIONAL: simple ALTER TABLE, no CONCURRENTLY, fits in a BEGIN block.
--   Run: psql -d ops_console -f 009_rework_of.sql
--
-- DOWN: ALTER TABLE dispatch_items DROP COLUMN IF EXISTS rework_of;
--   (safe; no dependent indexes/constraints introduced by this migration)

ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS rework_of VARCHAR(20);

COMMENT ON COLUMN dispatch_items.rework_of IS
    'Base story id (e.g. STORY-169) when this row is a rework of an existing '
    'PR. NULL for greenfield dispatches. Read by the phase runner to resolve '
    'branch + feature folder against the base story.';

CREATE INDEX IF NOT EXISTS idx_dispatch_rework_of
    ON dispatch_items(rework_of)
    WHERE rework_of IS NOT NULL;
