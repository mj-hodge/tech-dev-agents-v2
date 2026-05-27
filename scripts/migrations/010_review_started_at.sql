-- 010_review_started_at.sql
-- STORY-496 column was added to the Pydantic model + service layer but the
-- migration was never written. /api/dispatch/review/{id} 500'd against prod
-- with `column "review_started_at" of relation "dispatch_items" does not exist`
-- on 2026-04-25 once the gate was flipped on.

ALTER TABLE dispatch_items
  ADD COLUMN IF NOT EXISTS review_started_at TIMESTAMP WITH TIME ZONE;
