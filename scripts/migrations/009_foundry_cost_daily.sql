-- STORY-576: Fleet-wide Foundry cost cache, bucketed by model deployment.
-- Populated by refresh_foundry_cost.py cron (every 2h).
-- Read by GET /api/fleet/foundry-cost.

CREATE TABLE IF NOT EXISTS foundry_cost_daily (
  usage_date DATE        PRIMARY KEY,
  opus_usd   NUMERIC(10,2) NOT NULL DEFAULT 0,
  sonnet_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
  haiku_usd  NUMERIC(10,2) NOT NULL DEFAULT 0,
  other_usd  NUMERIC(10,2) NOT NULL DEFAULT 0,
  fetched_at TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Index on fetched_at for cache-age queries (single row lookup by max).
CREATE INDEX IF NOT EXISTS idx_foundry_cost_daily_fetched
  ON foundry_cost_daily (fetched_at DESC);
