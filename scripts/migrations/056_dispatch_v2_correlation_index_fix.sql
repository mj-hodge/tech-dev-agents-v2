-- Migration: 056_dispatch_v2_correlation_index_fix.sql
-- Story: queue stability hardening
--
-- Problem:
--   idx_dispatch_jobs_active_correlation was created as a UNIQUE index on
--   dispatch_jobs(correlation_key) where correlation_key IS NOT NULL.
--   That enforces uniqueness across all time, not just active jobs, and blocks
--   legitimate redispatch retries for the same repo+PR after terminal states.
--
-- Fix:
--   Drop the global UNIQUE index and replace it with a non-unique lookup index.
--   Active-conflict enforcement remains in service logic by joining
--   dispatch_state_current and checking only active states.

BEGIN;

DROP INDEX IF EXISTS idx_dispatch_jobs_active_correlation;

CREATE INDEX IF NOT EXISTS idx_dispatch_jobs_correlation_lookup
    ON dispatch_jobs (correlation_key)
    WHERE correlation_key IS NOT NULL;

COMMIT;
