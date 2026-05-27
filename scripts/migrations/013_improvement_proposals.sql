-- Migration: 013_improvement_proposals.sql
-- Story: STORY-727 — Continuous Self-Improvement Loop
-- Created: 2026-04-26
--
-- Note: The original spec named this 004_improvement_proposals.sql but migrations
-- 004–012 were claimed by other stories that merged first. This file uses the
-- next available index (013).

BEGIN;

CREATE TABLE IF NOT EXISTS improvement_proposals (
    id                    SERIAL PRIMARY KEY,
    proposed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    pattern_key           TEXT NOT NULL,
    pattern_evidence_json JSONB NOT NULL DEFAULT '{}',
    target_file           TEXT NOT NULL DEFAULT '',
    diff_text             TEXT NOT NULL DEFAULT '',
    rationale             TEXT NOT NULL,
    expected_metric       TEXT NOT NULL DEFAULT '',
    expected_direction    TEXT NOT NULL DEFAULT 'decrease'
                            CHECK (expected_direction IN ('decrease', 'increase')),
    proposal_type         TEXT NOT NULL DEFAULT 'tier1'
                            CHECK (proposal_type IN ('tier1', 'tier2', 'informational')),
    teams_message_id      TEXT,
    status                TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected',
                                              'applied', 'reverted')),
    decided_at            TIMESTAMPTZ,
    decided_by            TEXT,
    applied_commit        TEXT,
    applied_at            TIMESTAMPTZ
);

-- Prevents a second 'pending' row for the same (pattern_key, target_file).
-- Once status transitions away from 'pending', a new pending row can be
-- inserted after the rejection cooldown expires.
CREATE UNIQUE INDEX IF NOT EXISTS improvement_proposals_pending_unique
    ON improvement_proposals (pattern_key, target_file)
    WHERE status = 'pending';

-- Supports the tracker's daily check-back query filtering on applied proposals.
CREATE INDEX IF NOT EXISTS improvement_proposals_check_back_idx
    ON improvement_proposals (status, applied_at)
    WHERE status = 'applied';

CREATE TABLE IF NOT EXISTS improvement_tracking (
    proposal_id   INTEGER NOT NULL
                    REFERENCES improvement_proposals(id) ON DELETE CASCADE,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric_name   TEXT NOT NULL,
    metric_value  NUMERIC NOT NULL,
    window_days   INTEGER NOT NULL,
    note          TEXT,
    PRIMARY KEY (proposal_id, measured_at, metric_name)
);

COMMIT;
