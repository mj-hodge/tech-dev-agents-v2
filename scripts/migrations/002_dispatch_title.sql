-- STORY-034: Add title column to dispatch_items
-- Idempotent: safe to run multiple times (checks column existence)
--
-- Short human-readable title for dashboard display.
-- Nullable for backward compatibility with existing rows.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dispatch_items' AND column_name = 'title'
    ) THEN
        ALTER TABLE dispatch_items ADD COLUMN title VARCHAR(200);
    END IF;
END
$$;
