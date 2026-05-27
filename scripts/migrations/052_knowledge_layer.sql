-- Migration: 052_knowledge_layer.sql
-- Story: Epic-Queue-v2 Q7 — Knowledge Layer (gc-knowledgebase as Agent Memory)
-- Created: 2026-05-02
--
-- Schema contract committed in Phase 7 (test design).
-- Phase 8 must not alter column names, types, or constraint names defined here.
--
-- Tables:
--   dispatch_qa_cache        — normalized Q&A pairs with trigram search index
--   knowledge_citations      — per-job page usage tracking; weekly audit source
--   knowledge_ingest_queue   — staging area for proposed knowledge pages
--
-- Dependency: dispatch_jobs (migration 050) must be applied first.
--             pg_trgm extension required for idx_qa_cache_text_trgm.

BEGIN;

-- Enable trigram extension (idempotent)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- dispatch_qa_cache
-- Stores normalized Q&A pairs extracted from ANSWER.md files and answered
-- needs_info events. question_hash enables fast dedup on ingest.
-- Trigram GIN index enables similarity search via the pre-question hook.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatch_qa_cache (
    qa_id           BIGSERIAL PRIMARY KEY,
    question_hash   TEXT NOT NULL,             -- normalized + hashed for dedup
    question_text   TEXT NOT NULL,
    answer_text     TEXT NOT NULL,
    repo            TEXT,                      -- scope of applicability (NULL = global)
    source_job_id   UUID REFERENCES dispatch_jobs(job_id),
    answered_by     TEXT,                      -- 'mark' | 'morris' | 'auto-cluster' | 'backfill'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    use_count       INT NOT NULL DEFAULT 0,
    CONSTRAINT dispatch_qa_cache_hash_unique UNIQUE (question_hash)
);

CREATE INDEX IF NOT EXISTS idx_qa_cache_hash
    ON dispatch_qa_cache (question_hash);

CREATE INDEX IF NOT EXISTS idx_qa_cache_text_trgm
    ON dispatch_qa_cache
    USING gin (question_text gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- knowledge_citations
-- Records each time an agent uses a knowledge page (via context-load skill
-- or cache hit). Weekly Morris audit queries this table to find zero-citation
-- pages older than 90 days.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_citations (
    citation_id     BIGSERIAL PRIMARY KEY,
    job_id          UUID REFERENCES dispatch_jobs(job_id),
    knowledge_path  TEXT NOT NULL,             -- e.g., 'wiki/processes/sdlc.md'
    cited_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    phase           TEXT                       -- which phase used this page (e.g. 'phase-1')
);

CREATE INDEX IF NOT EXISTS idx_knowledge_citations_job_id
    ON knowledge_citations (job_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_citations_path_cited
    ON knowledge_citations (knowledge_path, cited_at DESC);

-- ---------------------------------------------------------------------------
-- knowledge_ingest_queue
-- Staging area for knowledge pages proposed by ingest_from_completed_story().
-- classification='auto:mechanical' → promoted without human approval.
-- classification='human:judgment'  → surfaces in Mark's review queue.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_ingest_queue (
    ingest_id       BIGSERIAL PRIMARY KEY,
    job_id          UUID REFERENCES dispatch_jobs(job_id),
    proposed_path   TEXT NOT NULL,             -- 'wiki/research/decisions/2026-05-02-story-Q7.md'
    proposed_body   TEXT NOT NULL,
    classification  TEXT NOT NULL             -- 'auto:mechanical' | 'human:judgment'
                        CHECK (classification IN ('auto:mechanical', 'human:judgment')),
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'promoted', 'rejected')),
    decided_by      TEXT,
    decided_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_ingest_queue_status
    ON knowledge_ingest_queue (status, classification);

COMMIT;
