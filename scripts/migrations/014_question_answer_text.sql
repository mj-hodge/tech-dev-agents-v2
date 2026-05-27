-- Migration 014: Add question_text and answer_text columns for DB-mediated Q&A (STORY-738)
--
-- Enables operators to read agent questions and submit answers entirely through
-- the dashboard UI, eliminating SSH-based operator friction.
--
-- Both columns are nullable TEXT with application-level 64 KB cap.
-- question_text: agent's blocking question, posted alongside question_file_path
-- answer_text: operator's answer, written via POST /dispatch/{story_id}/answer
--
-- Rollback: ALTER TABLE dispatch_items DROP COLUMN IF EXISTS question_text;
--           ALTER TABLE dispatch_items DROP COLUMN IF EXISTS answer_text;

ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS question_text TEXT;
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS answer_text TEXT;
