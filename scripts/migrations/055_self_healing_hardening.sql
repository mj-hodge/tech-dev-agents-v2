-- Migration: 055_self_healing_hardening.sql
-- Story: Epic-Queue-v2 hardening follow-up (H8/C2/C3 activation)
-- Created: 2026-05-02
--
-- Purpose:
--   1) Add dispatch_leases.heartbeat_data JSONB contract column used by
--      StuckAgentWatcher heuristics.
--   2) Align needs_info TTL index with occurred_at query path.

BEGIN;

ALTER TABLE dispatch_leases
    ADD COLUMN IF NOT EXISTS heartbeat_data JSONB NOT NULL DEFAULT '{}'::jsonb;

DROP INDEX IF EXISTS idx_dispatch_v2_events_needs_info_age;
CREATE INDEX IF NOT EXISTS idx_dispatch_v2_events_needs_info_age
    ON dispatch_v2_events (job_id, occurred_at)
    WHERE event_type = 'needs_info';

COMMIT;
