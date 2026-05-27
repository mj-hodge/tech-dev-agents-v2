-- STORY-701: Add failure_reason column to dispatch_items.
-- Stores a categorical reason when a row transitions to status='failed'.
-- Set by the dispatch poller's _classify_failure_reason() helper.
-- Idempotent. Safe to re-run.

ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(40);

COMMENT ON COLUMN dispatch_items.failure_reason IS
    'Categorical reason for status=failed. NULL when status != failed. '
    'Set by the dispatch poller when it transitions a row to failed.';
