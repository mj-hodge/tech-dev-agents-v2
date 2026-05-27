-- STORY-860: Add phase orchestration event types to dispatch_v2_events.
--
-- Phase events (phase_started, phase_completed, phase_failed) are informational
-- — they do NOT change the job's state in dispatch_state_current. They are
-- treated like 'heartbeat' in the trigger: early-return without updating state.
--
-- Changes:
--   1. Drop and recreate the CHECK constraint to include new event types.
--   2. Update dispatch_state_apply() trigger to pass-through phase events.
--   3. Add partial index for resume queries (AC-10).

-- Step 1: Expand the event_type CHECK constraint
ALTER TABLE dispatch_v2_events
    DROP CONSTRAINT IF EXISTS dispatch_v2_events_event_type_check;

ALTER TABLE dispatch_v2_events
    ADD CONSTRAINT dispatch_v2_events_event_type_check
    CHECK (event_type IN (
        'enqueued', 'leased', 'heartbeat', 'released',
        'needs_info', 'resumed', 'submitted', 'accepted',
        'rejected', 'failed', 'cancelled', 'dead_lettered',
        'quarantined', 'requeued',
        -- STORY-860: phase orchestration events (informational, no state change)
        'phase_started', 'phase_completed', 'phase_failed'
    ));

-- Step 2: Update the state trigger to pass-through phase events
CREATE OR REPLACE FUNCTION dispatch_state_apply() RETURNS TRIGGER AS $$
DECLARE
    v_state         TEXT;
    v_lane          TEXT;
    v_leased_by     TEXT;
    v_leased_at     TIMESTAMPTZ;
    v_needs_info    TEXT;
    v_fail_class    TEXT;
    v_kind          TEXT;
BEGIN
    -- heartbeat + phase events: informational only, no state/lane change
    IF NEW.event_type IN ('heartbeat', 'phase_started', 'phase_completed', 'phase_failed') THEN
        RETURN NEW;
    END IF;

    v_leased_by  := NULL;
    v_leased_at  := NULL;
    v_needs_info := NULL;
    v_fail_class := NULL;

    CASE NEW.event_type
        WHEN 'enqueued' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        WHEN 'leased' THEN
            v_state := 'leased';     v_lane := 'in_progress';
            v_leased_by := NEW.event_data->>'agent';
            SELECT leased_at INTO v_leased_at FROM dispatch_leases WHERE job_id = NEW.job_id;
            IF v_leased_at IS NULL THEN v_leased_at := now(); END IF;
        WHEN 'released' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        WHEN 'submitted' THEN
            v_state := 'in_review';  v_lane := 'in_review';
        WHEN 'accepted' THEN
            v_state := 'completed';  v_lane := 'terminal';
        WHEN 'rejected' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        WHEN 'needs_info' THEN
            v_state := 'needs_info';
            v_kind := NEW.event_data->>'kind';
            v_needs_info := COALESCE(v_kind, 'question');
            IF v_kind = 'attention' THEN v_lane := 'attention_queue';
            ELSE v_lane := 'human_queue'; END IF;
        WHEN 'resumed' THEN
            v_state := 'leased';     v_lane := 'in_progress';
            v_leased_by := NEW.event_data->>'agent';
            SELECT leased_at INTO v_leased_at FROM dispatch_leases WHERE job_id = NEW.job_id;
            IF v_leased_at IS NULL THEN v_leased_at := now(); END IF;
        WHEN 'failed' THEN
            v_state := 'failed';     v_lane := 'attention_queue';
            v_fail_class := NEW.event_data->>'failure_class';
        WHEN 'cancelled' THEN
            v_state := 'cancelled';  v_lane := 'terminal';
        WHEN 'dead_lettered' THEN
            v_state := 'dead_letter'; v_lane := 'terminal';
        WHEN 'quarantined' THEN
            v_state := 'quarantined'; v_lane := 'quarantined';
        WHEN 'requeued' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        ELSE
            RAISE EXCEPTION 'Unknown event_type: %', NEW.event_type;
    END CASE;

    INSERT INTO dispatch_state_current
        (job_id, state, lane, last_event_id, updated_at,
         leased_by, leased_at, needs_info_kind, failure_class)
    VALUES
        (NEW.job_id, v_state, v_lane, NEW.event_id, now(),
         v_leased_by, v_leased_at, v_needs_info, v_fail_class)
    ON CONFLICT (job_id) DO UPDATE SET
        state           = EXCLUDED.state,
        lane            = EXCLUDED.lane,
        last_event_id   = EXCLUDED.last_event_id,
        updated_at      = EXCLUDED.updated_at,
        leased_by       = EXCLUDED.leased_by,
        leased_at       = EXCLUDED.leased_at,
        needs_info_kind = EXCLUDED.needs_info_kind,
        failure_class   = EXCLUDED.failure_class;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Step 3: Partial index for resume queries (AC-10)
-- Uses CREATE INDEX (not CONCURRENTLY) — safe in migration context.
CREATE INDEX IF NOT EXISTS idx_dispatch_v2_events_phase
    ON dispatch_v2_events(job_id, event_type)
    WHERE event_type IN ('phase_started', 'phase_completed', 'phase_failed');
