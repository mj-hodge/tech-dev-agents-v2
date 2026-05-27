-- Migration: 054_dispatch_decisions.sql
-- Story: Epic-Queue-v2 Q9 — Declining Overwatch / Apprenticeship Loop
-- Created: 2026-05-02
--
-- Schema contract committed in Phase 7 (test design).
-- Phase 8 must not alter column names, types, or constraint names defined here.
--
-- Tables:
--   dispatch_decisions   — decision log; every Mark/Morris/auto-rule action
--   dispatch_rules       — proposed and approved automation rules
--
-- Dependency: migration 050 (dispatch_v2_events) must be applied first.
--             dispatch_decisions.downstream_event_id FK references dispatch_v2_events(event_id).

BEGIN;

-- ---------------------------------------------------------------------------
-- dispatch_decisions
--
-- Every decision made in the ops console or via skill — by Mark, Morris, or
-- an approved auto-rule — writes one row here. The inputs_hash enables the
-- pattern proposer to cluster semantically identical decisions across time.
--
-- decided_by values:
--   'mark'           — human decision by Mark via dashboard or skill
--   'morris'         — Morris autonomous decision
--   'auto-rule:N'    — executed by approved dispatch_rules row with rule_id=N
--
-- decision_kind examples (non-exhaustive):
--   'answer-needs-info', 'cancel', 'redispatch', 'force-release',
--   'dead-letter', 'change-priority', 'approve-pr', 'promote-knowledge',
--   'reject-knowledge', 'prune-cited-page', 'change-failure-class-ceiling'
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatch_decisions (
    decision_id           BIGSERIAL PRIMARY KEY,
    decided_by            TEXT NOT NULL,
    decided_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_kind         TEXT NOT NULL,
    inputs_hash           TEXT NOT NULL,           -- sha256 of json.dumps(inputs_json, sort_keys=True)
    inputs_json           JSONB NOT NULL,
    outcome               TEXT NOT NULL,           -- the action taken / value chosen
    downstream_event_id   BIGINT REFERENCES dispatch_v2_events(event_id),
    rule_id               INT,                     -- set when decided_by='auto-rule:N'
    overrode_rule_id      INT                      -- set when human overrode an auto-rule decision
);

-- Primary lookup for pattern proposer: cluster by kind + hash, ordered by recency
CREATE INDEX IF NOT EXISTS idx_decisions_hash
    ON dispatch_decisions(decision_kind, inputs_hash, decided_at DESC);

-- Touch-rate dashboard query support: filter + sort by decided_at per actor
CREATE INDEX IF NOT EXISTS idx_decisions_decided_by_at
    ON dispatch_decisions(decided_by, decided_at DESC);

-- ---------------------------------------------------------------------------
-- dispatch_rules
--
-- Proposed and approved automation rules. A rule is proposed automatically by
-- the weekly PatternProposer job when a cluster of ≥3 decisions shares the same
-- (decision_kind, inputs_hash, outcome). Rules are pending until Mark approves
-- them via POST /api/apprenticeship/rules/{rule_id}/approve.
--
-- Counter-rule enforcement (server-side, not bypassable):
--   1. High-blast-radius decision_kinds are never inserted here by the proposer.
--   2. If override_count / fire_count > 0.10 over a 30-day window, the rule
--      is auto-disabled (disabled_at set, disabled_reason='override_rate_exceeded').
--   3. If Mark's quarterly touch rate is rising, new rule approvals are paused.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatch_rules (
    rule_id                        SERIAL PRIMARY KEY,
    decision_kind                  TEXT NOT NULL,
    trigger_pattern                JSONB NOT NULL,       -- {decision_kind, inputs_hash, ...}
    outcome                        TEXT NOT NULL,
    proposed_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposed_from_decision_ids     BIGINT[],             -- source decision_ids from clustering
    approved_at                    TIMESTAMPTZ,          -- NULL = pending; non-NULL = approved
    approved_by                    TEXT,
    disabled_at                    TIMESTAMPTZ,          -- NULL = active; non-NULL = disabled
    disabled_reason                TEXT,                 -- 'override_rate_exceeded' | 'manual'
    fire_count                     INT NOT NULL DEFAULT 0,
    override_count                 INT NOT NULL DEFAULT 0
);

-- Rule lookup during dispatch: find approved, non-disabled rules by kind
CREATE INDEX IF NOT EXISTS idx_rules_active
    ON dispatch_rules(decision_kind)
    WHERE approved_at IS NOT NULL AND disabled_at IS NULL;

COMMIT;
