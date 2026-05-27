-- STORY-253: Commit-gated dispatch completion
-- Adds commit_sha + pr_number columns to dispatch_items so that
-- POST /api/dispatch/complete/<id> can require proof of work.
-- Background: 6 stories were marked "completed" in ~30 min on 2026-04-15
-- with zero commits. No endpoint-level guard existed.

ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS commit_sha TEXT,
    ADD COLUMN IF NOT EXISTS pr_number  INTEGER;

CREATE INDEX IF NOT EXISTS idx_dispatch_items_commit_sha
    ON dispatch_items(commit_sha)
    WHERE commit_sha IS NOT NULL;
