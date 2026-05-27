-- STORY-702: Claim heartbeat + auto-release columns.
-- Idempotent: safe to re-run on a DB that already has these columns.

ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS claim_heartbeat_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS stale_release_count  INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN dispatch_items.claim_heartbeat_at IS
    'Last heartbeat from the claiming agent. Updated every ~5min while status=claimed. NULL when status != claimed.';

COMMENT ON COLUMN dispatch_items.stale_release_count IS
    'Count of stale-release events. After 3, row transitions to failed with failure_reason=agent_died.';

CREATE INDEX IF NOT EXISTS idx_di_stale_check ON dispatch_items (claim_heartbeat_at)
    WHERE status = 'claimed';
