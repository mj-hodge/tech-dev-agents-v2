-- STORY-700: Append-only dispatch event log table.
-- Every queue + phase transition writes one row. Source of truth for
-- dispatch lifecycle history (dispatch_items mutates in place).
-- Idempotent: safe to re-run on a DB that already has the table.

CREATE TABLE IF NOT EXISTS dispatch_events (
  id          BIGSERIAL    PRIMARY KEY,
  story_id    VARCHAR(20)  NOT NULL,
  repo        VARCHAR(200) NOT NULL,
  event_type  VARCHAR(40)  NOT NULL,
  agent       VARCHAR(50),
  phase_num   SMALLINT,
  payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  ts          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT valid_event_type CHECK (event_type ~ '^[a-z_]+$')
);

CREATE INDEX IF NOT EXISTS idx_de_story_repo_ts ON dispatch_events (story_id, repo, ts);
CREATE INDEX IF NOT EXISTS idx_de_event_type_ts ON dispatch_events (event_type, ts);
CREATE INDEX IF NOT EXISTS idx_de_agent_ts      ON dispatch_events (agent, ts) WHERE agent IS NOT NULL;
