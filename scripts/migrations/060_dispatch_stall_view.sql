-- Migration: 060_dispatch_stall_view.sql
-- Adds semantic last_action tracking on dispatch_leases for the /stalls view.
--
-- Background: dispatch_leases.heartbeat_at already tracks wrapper liveness
-- (5-min cadence from dispatch_poller_v2). This migration adds a sibling
-- column for the agent's "what am I doing right now" string, written on
-- the same heartbeat cycle. Together they answer "is this story stalled,
-- and if so, where did the agent last get to?"
--
-- ADDITIVE ONLY. No existing tables modified.

BEGIN;

ALTER TABLE dispatch_leases
    ADD COLUMN IF NOT EXISTS last_action TEXT;

COMMENT ON COLUMN dispatch_leases.last_action IS
    'Free-form short string set by the agent on each heartbeat. Examples: '
    '"committed abc123 (Phase 8)", "drafting test cases (Phase 7)". Nullable '
    'when the agent has not yet emitted a semantic update.';

-- Index for the /stalls view: surfaces leased rows whose heartbeat is older
-- than a threshold. Partial WHERE keeps the index narrow.
CREATE INDEX IF NOT EXISTS idx_dispatch_leases_stale
    ON dispatch_leases (heartbeat_at);

COMMIT;
