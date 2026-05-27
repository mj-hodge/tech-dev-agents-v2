-- Migration: 060_needs_info_question_text_guard.sql
-- Story: STORY-917 — needs_info question_text guardrail (service-layer + DB trigger)
-- Created: 2026-05-14
--
-- Two-part defense-in-depth:
--
--   Part 1 (service layer — dispatch_v2_service.py):
--     transition() now rejects needs_info events when event_data has neither
--     question_text nor needs_info_path. Added separately in Python (this migration
--     handles only the DB side).
--
--   Part 2 (this migration):
--     a. Cleanup: emit `cancelled` events for any pre-existing zombie needs_info rows
--        (rows where state='needs_info' and their originating event has neither key).
--        Uses the existing event-driven state machine — does NOT write to
--        dispatch_state_current directly.
--     b. Trigger: installs a BEFORE INSERT OR UPDATE trigger on dispatch_state_current
--        that raises when NEW.state='needs_info' and the most recent needs_info event
--        for that job_id lacks both question_text and needs_info_path.
--
-- Mirrors STORY-914's in_review→pr_number guard (057_in_review_requires_pr_link.sql).
--
-- Idempotency:
--   - CREATE OR REPLACE FUNCTION — safe to re-run
--   - DROP TRIGGER IF EXISTS before CREATE TRIGGER — no duplicate trigger
--   - Cleanup WHERE NOT EXISTS (latest cancelled event) — no double-cancel
--
-- Dependency: migration 050 (dispatch_v2_schema.sql) must be applied first.
-- Compatible with 051, 056, 057, 058, 059 — additive only.
-- Does NOT modify dispatch_v2_events triggers or dispatch_state_apply().

BEGIN;

-- ---------------------------------------------------------------------------
-- Part 1: Cleanup — cancel pre-existing zombie needs_info rows
-- ---------------------------------------------------------------------------
--
-- A zombie is a job where:
--   1. dispatch_state_current.state = 'needs_info'
--   2. The most recent event with event_type='needs_info' for that job_id has
--      event_data that contains neither 'question_text' nor 'needs_info_path'
--
-- We emit a 'cancelled' event for each zombie. The dispatch_state_apply_trg
-- trigger fires on that INSERT and updates dispatch_state_current to 'cancelled'
-- within the same transaction.
--
-- Idempotency: WHERE NOT EXISTS guard prevents double-cancellation on re-run.

DO $$
DECLARE
    v_job_id UUID;
    v_zombie_event_id BIGINT;
BEGIN
    FOR v_job_id, v_zombie_event_id IN
        SELECT sc.job_id, e.event_id
        FROM dispatch_state_current sc
        JOIN LATERAL (
            SELECT event_id, event_data
            FROM dispatch_v2_events
            WHERE job_id = sc.job_id
              AND event_type = 'needs_info'
            ORDER BY event_id DESC
            LIMIT 1
        ) e ON TRUE
        WHERE sc.state = 'needs_info'
          AND NOT (e.event_data ? 'question_text' OR e.event_data ? 'needs_info_path')
          -- Idempotency: skip if a migration_060 cancellation already exists
          AND NOT EXISTS (
              SELECT 1 FROM dispatch_v2_events
              WHERE job_id = sc.job_id
                AND event_type = 'cancelled'
                AND actor = 'migration_060'
          )
    LOOP
        INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
        VALUES (
            v_job_id,
            'cancelled',
            '{"reason":"zombie_needs_info_cleanup","source":"migration_060"}'::jsonb,
            'migration_060'
        );
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------------
-- Part 2: DB trigger — reject future needs_info INSERTs/UPDATEs without a question
-- ---------------------------------------------------------------------------
--
-- The trigger fires BEFORE INSERT OR UPDATE on dispatch_state_current.
-- When NEW.state = 'needs_info', it looks up the most recent needs_info event
-- for that job_id and raises if neither question_text nor needs_info_path is present.
--
-- This is defense-in-depth: the Python service-layer guard fires first in normal
-- flow, but this trigger catches any bypass path (direct DB writes, future bugs).

CREATE OR REPLACE FUNCTION needs_info_question_text_guard()
RETURNS TRIGGER AS $$
DECLARE
    v_event_data JSONB;
BEGIN
    -- Only enforce on needs_info state transitions
    IF NEW.state <> 'needs_info' THEN
        RETURN NEW;
    END IF;

    -- Look up the most recent needs_info event for this job
    SELECT event_data INTO v_event_data
    FROM dispatch_v2_events
    WHERE job_id = NEW.job_id
      AND event_type = 'needs_info'
    ORDER BY event_id DESC
    LIMIT 1;

    -- If no event found or event lacks both question keys, reject
    IF v_event_data IS NULL
       OR NOT (v_event_data ? 'question_text' OR v_event_data ? 'needs_info_path')
    THEN
        RAISE EXCEPTION
            'needs_info state requires question_text or needs_info_path in event_data (job_id=%)',
            NEW.job_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop and recreate to ensure idempotency (no duplicate trigger)
DROP TRIGGER IF EXISTS needs_info_question_text_trg ON dispatch_state_current;

CREATE TRIGGER needs_info_question_text_trg
    BEFORE INSERT OR UPDATE ON dispatch_state_current
    FOR EACH ROW
    EXECUTE FUNCTION needs_info_question_text_guard();

COMMIT;
