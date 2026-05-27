-- Migration: 058_failure_policy_rebase.sql
-- Story: STORY-859 — poller-side rebase prep + git_rebase_failed failure class
-- Created: 2026-05-04
--
-- Adds a single new failure_class row to dispatch_failure_policy.
--
--   git_rebase_failed — non-retryable, attention queue.
--
-- Why non-retryable: rebase conflicts on a story branch almost always need a
-- human to resolve. The prior cluster-1 incident on 2026-05-03 burned 3
-- retries per dispatch (~$2-5 of agent tokens each) classifying these as
-- the catch-all `phase_runner_crash` (retryable=TRUE, max_attempts=3).
-- Routing to attention_queue immediately stops the bleeding.
--
-- This migration is ADDITIVE ONLY. INSERT ... ON CONFLICT DO UPDATE makes
-- it idempotent and safe to re-run.
--
-- Dependency: migration 051 (dispatch_failure_policy.sql) must be applied first.
-- Coexists with migration 057 (STORY-857a expansion) — no row collisions.

BEGIN;

INSERT INTO dispatch_failure_policy
    (failure_class, retryable, max_attempts, cooldown_sec, next_lane, notes)
VALUES
    ('git_rebase_failed',
     FALSE, 0, 0, 'attention_queue',
     'STORY-859: git rebase failed in poller pre-step (fetch/checkout/pull --rebase). Conflict needs human resolution; do not auto-retry.')
ON CONFLICT (failure_class) DO UPDATE SET
    retryable    = EXCLUDED.retryable,
    max_attempts = EXCLUDED.max_attempts,
    cooldown_sec = EXCLUDED.cooldown_sec,
    next_lane    = EXCLUDED.next_lane,
    notes        = EXCLUDED.notes;

COMMIT;
