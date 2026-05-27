-- Migration: 051_dispatch_v2_dependencies.sql
-- Epic: EPIC-Queue-v2, Story Q2 — Atomic claim-next + Lease Token + Worker Version Contract
-- Created: 2026-05-02
--
-- This migration is ADDITIVE ONLY. No existing tables are modified.
--
-- Tables added:
--   dispatch_dependencies     — job → job dependency edges (resolved to job_id lazily)
--   dispatch_v2_quarantine    — v2-native quarantine by job_id (mirrors v1 dispatch_quarantine but keyed by job_id)
--
-- The v1 dispatch_quarantine table is untouched — it is still used by v1 routes.
-- The atomic_claim_next CTE in dispatch_v2_service joins dispatch_v2_quarantine.
-- When the v2 cutover completes (Q5), v1 quarantine is retired.

BEGIN;

-- ---------------------------------------------------------------------------
-- dispatch_dependencies
-- Cross-repo dependency edges for the claim-next eligibility CTE.
-- depends_on_job_id may be NULL when the dep has not yet been enqueued into
-- the v2 queue (lazy resolution: set on every claim-next eligibility check
-- or on every 'completed' event for the dep job).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_dependencies (
    id                   BIGSERIAL PRIMARY KEY,
    job_id               UUID NOT NULL REFERENCES dispatch_jobs(job_id),
    depends_on_repo      TEXT NOT NULL,
    depends_on_story_id  TEXT NOT NULL,
    depends_on_job_id    UUID REFERENCES dispatch_jobs(job_id),   -- NULL until resolved
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dispatch_deps_job
    ON dispatch_dependencies(job_id);

CREATE INDEX IF NOT EXISTS idx_dispatch_deps_depends_on_job
    ON dispatch_dependencies(depends_on_job_id)
    WHERE depends_on_job_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- dispatch_v2_quarantine
-- v2-native quarantine by job_id, used by the atomic_claim_next CTE.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dispatch_v2_quarantine (
    job_id          UUID PRIMARY KEY REFERENCES dispatch_jobs(job_id),
    quarantined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason          TEXT,
    cleared_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dispatch_v2_quarantine_active
    ON dispatch_v2_quarantine(job_id)
    WHERE cleared_at IS NULL;

COMMIT;
