-- STORY-508: Add priority column to dispatch_items for priority-based scheduling.
--
-- Priority is an integer 0–100 (default 0 = normal priority).
-- Higher values are dequeued first; FIFO tie-break on equal priority.
--
-- next_pending() ORDER BY changes from:
--   ORDER BY enqueued_at ASC
-- to:
--   ORDER BY priority DESC, enqueued_at ASC

ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;

-- Index to support efficient priority + FIFO ordering in next_pending() and queue().
CREATE INDEX IF NOT EXISTS idx_dispatch_priority
    ON dispatch_items (status, priority DESC, enqueued_at ASC);
