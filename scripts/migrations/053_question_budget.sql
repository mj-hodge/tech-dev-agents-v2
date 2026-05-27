-- Migration: 053_question_budget.sql
-- Story: Epic-Queue-v2 Q8 — Self-Healing (Question Budget + Stuck-Agent Heuristics + needs_info TTL)
-- Created: 2026-05-02
--
-- Schema contract committed in Phase 7 (test design).
-- Phase 8 must not alter column names, types, or seed values defined here.
--
-- Tables:
--   dispatch_question_budget  — per-scope question budget limits
--
-- Dependency: dispatch_jobs (migration 050) must be applied first.
--             dispatch_failure_policy (migration 051) must be applied first
--             (Q8 failure classes are seeded there as Q8-extension rows).

BEGIN;

-- ---------------------------------------------------------------------------
-- dispatch_question_budget
-- Per-scope configuration for the question budget enforcement gate.
-- Enforced server-side in /api/dispatch/v2/transition when event_type='needs_info'.
--
-- max_questions: total needs_info events allowed for a job of this scope
-- max_per_phase: max needs_info events allowed in a single phase execution
--
-- Seed values are the canonical contract; changes require a new migration.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatch_question_budget (
    scope           TEXT PRIMARY KEY,          -- 'small' | 'medium' | 'large' | 'epic'
    max_questions   INT NOT NULL               -- total per-job ceiling
                        CHECK (max_questions > 0),
    max_per_phase   INT NOT NULL               -- per-phase ceiling
                        CHECK (max_per_phase > 0),
    CONSTRAINT dispatch_question_budget_per_phase_lte_total
        CHECK (max_per_phase <= max_questions)
);

-- Seed data — canonical values, do not modify inline; add a new migration instead
INSERT INTO dispatch_question_budget (scope, max_questions, max_per_phase)
VALUES
    ('small',  2, 1),
    ('medium', 3, 2),
    ('large',  5, 2),
    ('epic',   8, 3)
ON CONFLICT (scope) DO NOTHING;

-- ---------------------------------------------------------------------------
-- heartbeat_data contract documentation column
-- The dispatch_leases.heartbeat_data JSONB must contain these keys for the
-- stuck-agent watcher to evaluate heuristics. This comment is the schema
-- contract; the actual column was added in migration 050.
--
-- Expected heartbeat_data structure:
-- {
--   "git_head_sha":    TEXT,          -- current working-tree SHA (from `git rev-parse HEAD`)
--   "current_phase":   TEXT,          -- e.g. 'phase-8'
--   "phase_started_at": TIMESTAMPTZ,  -- ISO-8601 string
--   "last_test_status": TEXT          -- 'RED' | 'GREEN' | null
-- }
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Strengthen the dispatch_failed_event_required_fields trigger (Q3 regression fix)
-- The Q3 trigger checked only that failure_class/failure_reason keys exist in
-- event_data JSON; Q8 adds a check that the values are non-null strings.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dispatch_failed_event_required_fields()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_type = 'failed' THEN
        IF NOT (
            (NEW.event_data ? 'failure_class') AND
            (NEW.event_data->>'failure_class' IS NOT NULL) AND
            (NEW.event_data ? 'failure_reason') AND
            (NEW.event_data->>'failure_reason' IS NOT NULL)
        ) THEN
            RAISE EXCEPTION
                'failed event requires non-null event_data.failure_class and event_data.failure_reason (job_id=%)',
                NEW.job_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- Add occurred_at column to dispatch_v2_events
-- Canonical event timestamp for the self-healing TTL queries.
-- occurred_at defaults to now() and reflects when the event logically occurred
-- (may differ from created_at for backfilled/replayed events).
-- ---------------------------------------------------------------------------
ALTER TABLE dispatch_v2_events
    ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Index on dispatch_v2_events for efficient TTL scan (needs_info older than 24h)
-- This supports dispatch_needs_info_ttl() background task performance.
CREATE INDEX IF NOT EXISTS idx_dispatch_v2_events_needs_info_age
    ON dispatch_v2_events (job_id, created_at)
    WHERE event_type = 'needs_info';

COMMIT;
