# Seed: STORY-700 — `dispatch_events` log (foundation for observability)

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_add |
| Scope | medium |
| Frontend | false |
| Feature Name | Append-only `dispatch_events` table; every queue + phase transition logged |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Status | Seed written 2026-04-25 |
| Priority | 80 — foundation for STORY-701 (DLQ), STORY-702 (heartbeat), STORY-720 (codex debt log) and the broader observability story |

---

## 1. Idea / Trigger

The dispatch queue mutates `dispatch_items` in place. Tonight's bug investigations took 30+ minutes each because the only history we have is `updated_at`, scattered journalctl logs across multiple agent VMs, and recall.

This story creates an append-only `dispatch_events` table. Every state transition writes one row. Becomes the durable timeline foundation for: DLQ classification (STORY-701), claim heartbeat detection (STORY-702), codex debt tracking (STORY-720), and real-flow alerts.

## 2. Problem Statement

- Debugging a single story's lifecycle requires correlating `dispatch_items.updated_at`, multiple `journalctl -u dispatch-poller` outputs across VMs, and Morris's state files. Tonight's STORY-575 needs_info loop took 20 min to diagnose; with events it would have taken 30 seconds.
- Phase 2 alert rules (real-flow observability) need event-stream data to compute things like "no claims in 30 min during work hours" or "claimed → phase_started lag P95." There's no source of that data today.
- Schema migrations on `dispatch_items` are scary because the table holds both current state AND the closest thing we have to history. Splitting them de-risks future changes.

## 3. Scope Classification

**Medium.** New table, new helper service, ~6 producer call-sites across 3 files, retention cron, and tests. No frontend, no API contract change for callers, no backwards-incompatible behavior.

## 4. Codebase Context

### New: `scripts/migrations/010_dispatch_events.sql`

Minimal-column schema (Mark's call: minimal + JSONB payload, easier to extend than to retract):

```sql
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
```

Native columns are minimal on purpose. `duration_ms`, `exit_code`, `session_id`, etc. all live inside `payload` JSONB and are queryable via standard JSONB operators.

### New: `tech_dev_agents/ops_console/services/dispatch_events.py`

```python
async def emit(
    story_id: str,
    repo: str,
    event_type: str,
    *,
    agent: str | None = None,
    phase_num: int | None = None,
    payload: dict | None = None,
) -> None
```

Best-effort: caller supplies details, helper does the INSERT. **Never raises** to the caller — DB failures are logged but never block forward progress on the queue. Without this property, an event-write outage would halt the fleet.

### Producers (callers)

- **`tech_dev_agents/ops_console/routes/dispatch.py`** — emit on enqueue, claim, complete, fail, cancel, release, needs_info_set, resume.
- **`deployment/hermes/sdlc_phase_runner.py`** — emit on phase_started, phase_ended (payload: rc + duration_s), sdk_invoke_started, sdk_invoke_ended, validation_passed, validation_failed.
- **`deployment/hermes/dispatch_poller.py`** — emit on retry_enqueued (in `_report_fail` retry path); emit on rate_limited (subset of failed; payload: reset_time).

### Mutation strategy (Q4: A now, B later)

`dispatch_items` continues to mutate as it does today. Events are emitted ALONGSIDE — not in place of — those mutations. Every reader of `dispatch_items` continues to work unchanged. If state and events disagree on any row, **events are the source of truth** (document this in the service docstring).

Migrating to a pure projection (B) is a separate future story; not in scope here.

### Backfill (Q2: forward-only on first deploy)

The migration creates an empty table. No journalctl backfill on first deploy. A separate `scripts/backfill_dispatch_events.py` can be hand-run later if we ever need historical data — designed but not scheduled, so the first deploy is bounded.

### Retention (Q3: trim after 120 days)

A separate Postgres cron (or a Morris weekly cron, or a pg_cron extension) deletes rows older than 120 days. Implemented as one DELETE statement — non-trivial to run as a single transaction at scale, so wrap in a 10k-row batched loop:

```sql
DO $$
DECLARE rows_deleted int;
BEGIN
  LOOP
    DELETE FROM dispatch_events
    WHERE ts < now() - interval '120 days'
      AND id IN (SELECT id FROM dispatch_events WHERE ts < now() - interval '120 days' LIMIT 10000);
    GET DIAGNOSTICS rows_deleted = ROW_COUNT;
    EXIT WHEN rows_deleted = 0;
  END LOOP;
END $$;
```

Schedule: weekly on Morris (Sunday 04:00 UTC), no urgency.

### Files NOT to touch

- `dispatch_items` table itself — keep as-is (Q4 decision).
- The dispatch API request/response shapes — unchanged.

## 5. Out of Scope

- Replaying events to recompute `dispatch_items` (B path; future story).
- A read API at `/api/dispatch/events` — direct SQL is fine for now; can be added by Morris's monitoring story later.
- Frontend timeline view — pull from raw SQL; UI can come after the alerts story.
- The retention backfill script (forward-only first deploy).

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/ops_console/test_dispatch_events.py`. Coverage:

1. **Migration applies idempotently** — `010_dispatch_events.sql` runs on fresh and pre-populated DBs without data loss.
2. **`emit()` writes the expected row** — call with all field permutations (agent None vs set, phase_num None vs set, payload None vs dict).
3. **`emit()` failures don't block callers** — mock the DB to raise; verify `emit()` returns without raising.
4. **All routes emit on transitions** — for each of `/dispatch`, `/claim`, `/complete`, `/fail`, `/cancel`, `/release`, `/needs_info`, `/resume`, mock the DB and verify the expected event row would be written.
5. **Phase runner emits on phase boundaries** — mock SDK + DB; run a fake 2-phase loop; verify 4 events (2× phase_started, 2× phase_ended) with correct duration_s in payload.
6. **Poller emits retry_enqueued** — exercise `_report_fail`'s retry path with a successful re-enqueue; verify event row.
7. **Indexes present** — query `pg_indexes` after migration; assert all three named indexes exist.
8. **Sample queries return well-typed results on a populated test DB** — fixture seeds 100 events; queries from Section 7 below return non-empty.

All tests are unit-level except the migration test (uses a real Postgres fixture from `tests/conftest.py`). No live ops-console required.

## Validation

After Phase 8 lands + ops-console redeploys (carries the new migration via STORY-118's auto-migrate fix in deploy.sh):

1. Dispatch a small story; watch `SELECT * FROM dispatch_events WHERE story_id='STORY-N' ORDER BY ts` populate in real time as the story advances.
2. Confirm timeline includes: `enqueued → claimed → phase_started (×N) → phase_ended (×N) → completed` (or fail/needs_info terminal).
3. Run these reference queries against production data; confirm non-empty plausible results:
   ```sql
   -- Time-to-first-claim P50/P95 over 7 days
   SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY claim_lag_s) AS p50,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY claim_lag_s) AS p95
     FROM ( SELECT EXTRACT(EPOCH FROM
              MIN(ts) FILTER (WHERE event_type='claimed')
            - MIN(ts) FILTER (WHERE event_type='enqueued')) AS claim_lag_s
            FROM dispatch_events
            WHERE ts > now() - interval '7 days'
            GROUP BY story_id, repo ) x
     WHERE claim_lag_s IS NOT NULL;
   ```
4. STORY-701 + STORY-702 are now unblocked.

## 8. Acceptance Diff

Must-contain:
- `scripts/migrations/010_dispatch_events.sql`
- `tech_dev_agents/ops_console/services/dispatch_events.py`
- ≥6 `emit(...)` call sites in `routes/dispatch.py`
- ≥4 `emit(...)` call sites in `deployment/hermes/sdlc_phase_runner.py`
- ≥1 `emit(...)` call site in `deployment/hermes/dispatch_poller.py`
- `tests/ops_console/test_dispatch_events.py`

Must-NOT-contain:
- Schema changes to `dispatch_items` (separate concern).
- Removal of any current `dispatch_items` columns.
- A read API for events (out of scope).

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-700/dispatch-events-table`
- Scope: medium
- Priority: 80
- Expected runtime: Phase 7 ~25 min, Phase 8 ~45–60 min.
- Blocking: STORY-701 + STORY-702 wait on this; ship first.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
