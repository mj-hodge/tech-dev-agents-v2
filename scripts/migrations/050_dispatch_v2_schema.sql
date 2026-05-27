-- Migration: 050_dispatch_v2_schema.sql
-- Epic: EPIC-Queue-v2, Story Q1 — Schema + Event Log as Source of Truth
-- Created: 2026-05-02
--
-- Gap after 014 is intentional: 015–049 reserved for Phase 0 hotfixes and
-- parallel stories in other waves.
--
-- This migration is ADDITIVE ONLY. No existing tables are modified.
--
-- Tables added:
--   dispatch_jobs            — canonical job record (no status column)
--   dispatch_v2_events       — append-only event log (source of truth)
--   dispatch_leases          — active lease per job (at most one row)
--   dispatch_state_current   — projection cache (rebuilt from events on divergence)
--
-- Triggers added:
--   dispatch_state_apply_trg        — maintains dispatch_state_current on every INSERT
--   dispatch_failed_required_trg    — rejects failed events without required fields
--
-- Functions added:
--   dispatch_state_apply()
--   dispatch_failed_event_required_fields()

BEGIN;

-- ---------------------------------------------------------------------------
-- dispatch_jobs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_jobs (
    job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo            TEXT NOT NULL,
    story_id        TEXT NOT NULL,
    correlation_key TEXT,                           -- e.g. "repo:<r>|pr:<n>"
    scope           TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    target_role     TEXT NOT NULL DEFAULT 'developer',
    rework_of       UUID REFERENCES dispatch_jobs(job_id),
    enqueued_by     TEXT NOT NULL,
    title           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Correlation lookup index.
-- Active-conflict enforcement is handled in service logic against
-- dispatch_state_current active states (not a global unique DB index).
CREATE INDEX IF NOT EXISTS idx_dispatch_jobs_correlation_lookup
    ON dispatch_jobs (correlation_key)
    WHERE correlation_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- dispatch_v2_events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_v2_events (
    event_id    BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES dispatch_jobs(job_id),
    event_type  TEXT NOT NULL CHECK (event_type IN (
        'enqueued', 'leased', 'heartbeat', 'released',
        'needs_info', 'resumed', 'submitted', 'accepted',
        'rejected', 'failed', 'cancelled', 'dead_lettered',
        'quarantined', 'requeued'
    )),
    event_data  JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dispatch_v2_events_job_created
    ON dispatch_v2_events(job_id, event_id DESC);

-- ---------------------------------------------------------------------------
-- dispatch_leases
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_leases (
    job_id        UUID PRIMARY KEY REFERENCES dispatch_jobs(job_id),
    lease_token   UUID NOT NULL DEFAULT gen_random_uuid(),
    agent_name    TEXT NOT NULL,
    leased_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    heartbeat_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dispatch_leases_expires
    ON dispatch_leases(expires_at);

-- ---------------------------------------------------------------------------
-- dispatch_state_current   (projection / cache)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_state_current (
    job_id          UUID PRIMARY KEY REFERENCES dispatch_jobs(job_id),
    state           TEXT NOT NULL,
    lane            TEXT NOT NULL,
    last_event_id   BIGINT NOT NULL REFERENCES dispatch_v2_events(event_id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Denormalised hot-path fields for queue rendering
    leased_by       TEXT,
    leased_at       TIMESTAMPTZ,
    needs_info_kind TEXT,       -- 'question' | 'attention'
    failure_class   TEXT
);

CREATE INDEX IF NOT EXISTS idx_dispatch_state_lane
    ON dispatch_state_current(lane, updated_at);

-- ---------------------------------------------------------------------------
-- Trigger: dispatch_failed_event_required_fields  (BEFORE INSERT)
--
-- Enforces that every 'failed' event carries both failure_class and
-- failure_reason in event_data. This is AC2 and is a BEFORE trigger so
-- the row is rejected before any projection update can happen.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION dispatch_failed_event_required_fields() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.event_type = 'failed' THEN
        IF NOT (
            (NEW.event_data ? 'failure_class') AND (NEW.event_data ? 'failure_reason')
        ) THEN
            RAISE EXCEPTION
                'failed event requires event_data.failure_class and event_data.failure_reason (job_id=%)',
                NEW.job_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'dispatch_failed_required_trg'
    ) THEN
        CREATE TRIGGER dispatch_failed_required_trg
        BEFORE INSERT ON dispatch_v2_events
        FOR EACH ROW EXECUTE FUNCTION dispatch_failed_event_required_fields();
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Trigger: dispatch_state_apply  (AFTER INSERT)
--
-- Maps each event_type to (state, lane) following architecture.md § 4.
--
-- State/lane mapping table:
--   enqueued      → pending      / work_queue
--   leased        → leased       / in_progress
--   heartbeat     → (no change)  / (no change)  — only updates lease timing
--   released      → pending      / work_queue
--   submitted     → in_review    / in_review
--   accepted      → completed    / terminal
--   rejected      → pending      / work_queue
--   needs_info    → needs_info   / human_queue (kind=question)
--                                  attention_queue (kind=attention or default)
--   resumed       → leased       / in_progress
--   failed        → failed       / attention_queue  (Q3 will add policy routing)
--   cancelled     → cancelled    / terminal
--   dead_lettered → dead_letter  / terminal
--   quarantined   → quarantined  / quarantined
--   requeued      → pending      / work_queue
--
-- Denormalised fields populated:
--   leased_by       — from event_data.agent on leased/resumed; NULL on release/submit/etc
--   leased_at       — from dispatch_leases.leased_at on leased/resumed; NULL on release
--   needs_info_kind — from event_data.kind on needs_info; NULL otherwise
--   failure_class   — from event_data.failure_class on failed; NULL otherwise
-- ---------------------------------------------------------------------------

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
    -- heartbeat: do not change state/lane on the projection;
    -- the lease heartbeat_at update is handled by the service layer directly.
    IF NEW.event_type = 'heartbeat' THEN
        RETURN NEW;
    END IF;

    -- Default nulls for denormalised fields
    v_leased_by  := NULL;
    v_leased_at  := NULL;
    v_needs_info := NULL;
    v_fail_class := NULL;

    CASE NEW.event_type
        WHEN 'enqueued' THEN
            v_state := 'pending';
            v_lane  := 'work_queue';

        WHEN 'leased' THEN
            v_state     := 'leased';
            v_lane      := 'in_progress';
            v_leased_by := NEW.event_data->>'agent';
            -- Capture leased_at from the lease row if available, else now()
            SELECT leased_at INTO v_leased_at
            FROM dispatch_leases
            WHERE job_id = NEW.job_id;
            IF v_leased_at IS NULL THEN
                v_leased_at := now();
            END IF;

        WHEN 'released' THEN
            v_state := 'pending';
            v_lane  := 'work_queue';

        WHEN 'submitted' THEN
            v_state := 'in_review';
            v_lane  := 'in_review';

        WHEN 'accepted' THEN
            v_state := 'completed';
            v_lane  := 'terminal';

        WHEN 'rejected' THEN
            v_state := 'pending';
            v_lane  := 'work_queue';

        WHEN 'needs_info' THEN
            v_state      := 'needs_info';
            v_kind       := NEW.event_data->>'kind';
            v_needs_info := COALESCE(v_kind, 'question');
            IF v_kind = 'attention' THEN
                v_lane := 'attention_queue';
            ELSE
                v_lane := 'human_queue';
            END IF;

        WHEN 'resumed' THEN
            v_state     := 'leased';
            v_lane      := 'in_progress';
            v_leased_by := NEW.event_data->>'agent';
            SELECT leased_at INTO v_leased_at
            FROM dispatch_leases
            WHERE job_id = NEW.job_id;
            IF v_leased_at IS NULL THEN
                v_leased_at := now();
            END IF;

        WHEN 'failed' THEN
            v_state      := 'failed';
            v_lane       := 'attention_queue';      -- default; Q3 adds policy routing
            v_fail_class := NEW.event_data->>'failure_class';

        WHEN 'cancelled' THEN
            v_state := 'cancelled';
            v_lane  := 'terminal';

        WHEN 'dead_lettered' THEN
            v_state := 'dead_letter';
            v_lane  := 'terminal';

        WHEN 'quarantined' THEN
            v_state := 'quarantined';
            v_lane  := 'quarantined';

        WHEN 'requeued' THEN
            v_state := 'pending';
            v_lane  := 'work_queue';

        ELSE
            -- Should never reach here due to CHECK constraint, but be defensive
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

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'dispatch_state_apply_trg'
    ) THEN
        CREATE TRIGGER dispatch_state_apply_trg
        AFTER INSERT ON dispatch_v2_events
        FOR EACH ROW EXECUTE FUNCTION dispatch_state_apply();
    END IF;
END $$;

COMMIT;
