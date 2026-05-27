-- Migration: 051_dispatch_failure_policy.sql
-- Story: Epic-Queue-v2 Q3 — Centralized Retry/Failure Policy + DLQ
-- Created: 2026-05-02
--
-- NOTE: The numeric prefix "051" refers to the Q3 failure-policy feature.
-- The file 051_dispatch_v2_dependencies.sql (Q2) is a separate migration that
-- also runs in this namespace; both are idempotent via IF NOT EXISTS guards.
--
-- This migration is ADDITIVE ONLY. No existing tables are modified.
--
-- Tables added:
--   dispatch_failure_policy  — failure class registry with retry/routing rules
--
-- Seed data: 10 baseline Q3 classes + 5 Q8 extension classes = 15 rows minimum.
--
-- Dependency: migration 050 (dispatch_v2_schema.sql) must be applied first.

BEGIN;

-- ---------------------------------------------------------------------------
-- dispatch_failure_policy
--
-- Single source of truth for retry and routing decisions on failed jobs.
-- One row per failure_class. The Python service mirrors this table in the
-- POLICY_TABLE module-level constant for fast in-process lookups.
--
-- next_lane values:
--   'work_queue'      — re-queued for retry
--   'quarantined'     — held until dependency resolves (watcher handles release)
--   'dead_letter'     — terminal; requires human intervention
--   'attention_queue' — routed to human review queue
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_failure_policy (
    failure_class   TEXT PRIMARY KEY,
    retryable       BOOLEAN NOT NULL DEFAULT FALSE,
    max_attempts    INT NOT NULL DEFAULT 0,
    cooldown_sec    INT NOT NULL DEFAULT 0,
    next_lane       TEXT NOT NULL DEFAULT 'attention_queue',
    notes           TEXT,

    CONSTRAINT dispatch_failure_policy_next_lane_check
        CHECK (next_lane IN ('work_queue', 'quarantined', 'dead_letter', 'attention_queue'))
);

-- ---------------------------------------------------------------------------
-- Seed data — baseline 10 Q3 classes
-- ---------------------------------------------------------------------------

INSERT INTO dispatch_failure_policy (failure_class, retryable, max_attempts, cooldown_sec, next_lane, notes)
VALUES
    -- argparse_reject: agent rejected the prompt at argument-parse time; user fix needed
    ('argparse_reject',          FALSE,  0,    0,    'attention_queue', 'Prompt rejected by agent argparse; requires operator correction'),

    -- rate_limited: API rate limit hit; retry after cooldown
    ('rate_limited',             TRUE,   5,    300,  'work_queue',      'Claude API rate limit; exponential backoff applied by poller'),

    -- branch_setup_failed: git branch could not be created/checked out
    ('branch_setup_failed',      TRUE,   3,    60,   'work_queue',      'Transient git error; safe to retry'),

    -- needs_info_unanswered: 24h TTL exceeded with no human response
    ('needs_info_unanswered',    FALSE,  0,    0,    'attention_queue', 'needs_info TTL exceeded; routed to human attention queue'),

    -- sdk_died_silent: Claude SDK returned exit 0 but no output — silent failure
    ('sdk_died_silent',          TRUE,   3,    120,  'work_queue',      'STORY-762 class: silent SDK exit; retry with diagnostics'),

    -- phase_runner_crash: unhandled exception in phase runner
    ('phase_runner_crash',       TRUE,   3,    30,   'work_queue',      'Phase runner crashed; transient; retry'),

    -- code_test_red: tests failed after implementation — not a transient error
    ('code_test_red',            FALSE,  0,    0,    'attention_queue', 'Tests are RED after implementation; needs human review'),

    -- adversarial_block: agent refused to proceed (adversarial/safety block)
    ('adversarial_block',        FALSE,  0,    0,    'attention_queue', 'Agent safety block; requires human review before retry'),

    -- dependency_missing: a required upstream job or artifact is not yet complete
    ('dependency_missing',       FALSE,  0,    0,    'quarantined',     'Blocked on upstream dependency; dependency watcher will release'),

    -- unknown: classifier could not identify the failure class
    ('unknown',                  FALSE,  0,    0,    'attention_queue', 'Unclassified failure; route to human attention queue')
ON CONFLICT (failure_class) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Seed data — Q8 extension classes (5 rows)
-- ---------------------------------------------------------------------------

INSERT INTO dispatch_failure_policy (failure_class, retryable, max_attempts, cooldown_sec, next_lane, notes)
VALUES
    -- agent_stuck_question_budget_exceeded: agent consumed all question budget
    ('agent_stuck_question_budget_exceeded',
                                 FALSE,  0,    0,    'attention_queue', 'Q8: agent exhausted question budget; needs_info loop exceeded'),

    -- agent_repetition: agent repeated the same action N times without progress
    ('agent_repetition',         FALSE,  0,    0,    'attention_queue', 'Q8: repetition loop detected; requires human review'),

    -- phase_overrun: phase ran past its time/token budget ceiling
    ('phase_overrun',            FALSE,  0,    0,    'attention_queue', 'Q8: phase budget ceiling exceeded; requires human intervention'),

    -- agent_flapping: agent oscillated between two states with no progress
    ('agent_flapping',           FALSE,  0,    0,    'attention_queue', 'Q8: agent flapping detected; route for human review'),

    -- dependency_unresolved_7d: dependency_missing job quarantined for >7 days
    ('dependency_unresolved_7d', FALSE,  0,    0,    'dead_letter',     'Q8: dependency unresolved for 7d; dead-letter the job')
ON CONFLICT (failure_class) DO NOTHING;

COMMIT;
