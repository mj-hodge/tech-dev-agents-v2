-- Migration: 057_failure_policy_expansion.sql
-- Story: STORY-857a — failure classifier expansion
-- Created: 2026-05-04
--
-- Adds 7 new failure_class rows to dispatch_failure_policy. These cover the
-- real-world failure modes that were silently hitting the regex catch-all
-- (phase_runner_crash) and burning 3 retries each:
--
--   lease_lost           — restart victim, retry 2x with 60s cooldown
--   workspace_missing    — operator must fix, attention queue
--   auth_expired         — page Mark to refresh tokens, attention queue
--   quota_exceeded       — API/spend limit, retry 1x with 30min cooldown
--   sigterm_shutdown     — restart victim, retry 3x with 60s cooldown
--   git_push_failed      — branch out of date, retry 2x with 60s cooldown
--   (branch_setup_failed already exists; refined regex coverage in classifier)
--
-- This migration is ADDITIVE ONLY. All inserts use ON CONFLICT DO UPDATE so
-- it is idempotent and safe to re-run.
--
-- Dependency: migration 051 (dispatch_failure_policy.sql) must be applied first.

BEGIN;

INSERT INTO dispatch_failure_policy (failure_class, retryable, max_attempts, cooldown_sec, next_lane, notes)
VALUES
    -- lease_lost: poller restarted or lease TTL expired mid-run; safe to retry
    ('lease_lost',
                                 TRUE,   2,    60,   'work_queue',
                                 'STORY-857a: restart victim — lease lost/stale/expired; safe retry with cooldown'),

    -- workspace_missing: workspace dir or .git missing; operator must fix
    ('workspace_missing',
                                 FALSE,  0,    0,    'attention_queue',
                                 'STORY-857a: workspace dir/.git missing; operator must fix before retry'),

    -- auth_expired: 401/403/invalid token; page Mark to refresh credentials
    ('auth_expired',
                                 FALSE,  0,    0,    'attention_queue',
                                 'STORY-857a: 401/403/invalid token — credentials need refresh by operator'),

    -- quota_exceeded: API quota or spend limit hit; long cooldown then 1 retry
    ('quota_exceeded',
                                 TRUE,   1,    1800, 'work_queue',
                                 'STORY-857a: API quota/spend limit; 30min cooldown then single retry'),

    -- sigterm_shutdown: process received SIGTERM (deploy/restart); retry safely
    ('sigterm_shutdown',
                                 TRUE,   3,    60,   'work_queue',
                                 'STORY-857a: SIGTERM received — restart/deploy victim; retry up to 3x'),

    -- git_push_failed: push rejected, non-fast-forward, conflict; rebase + retry
    ('git_push_failed',
                                 TRUE,   2,    60,   'work_queue',
                                 'STORY-857a: git push rejected/non-fast-forward; branch out of date — retry'),

    -- branch_setup_failed already seeded by 051; bump notes/cooldown if missing
    ('branch_setup_failed',
                                 TRUE,   2,    120,  'work_queue',
                                 'STORY-857a: refined — branch create/checkout failed; transient, retry')
ON CONFLICT (failure_class) DO UPDATE SET
    retryable    = EXCLUDED.retryable,
    max_attempts = EXCLUDED.max_attempts,
    cooldown_sec = EXCLUDED.cooldown_sec,
    next_lane    = EXCLUDED.next_lane,
    notes        = EXCLUDED.notes;

COMMIT;
