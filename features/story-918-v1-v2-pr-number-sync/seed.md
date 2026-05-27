# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Criticality | important |
| Feature Name | v1-v2-pr-number-sync |
| Frontend | false |

## Problem Statement

On 2026-05-12, a triage of 26 stuck `in_review` rows (ages 1.7–10.8 days) revealed a sync gap between v1 and v2 dispatch tables:

- v1 (`dispatch_items`) shows the row in `in_review` with operational metadata.
- v2 (`dispatch_jobs`) knows the row exists (`operator/cancel` returns the correct `prior_state`), but `dispatch_jobs.pr_number` is **NULL**.
- All v2 background sweepers — `pr_link_backfill_sweeper` (STORY-902), `pr_merge_sweeper` (STORY-901), and `stale_unlinked_in_review_sweeper` (2026-05-12) — read from `dispatch_jobs.pr_number`. None can act on rows with NULL.
- The 2026-05-04 cutover should have mirrored `dispatch_items.pr_number` into `dispatch_jobs.pr_number` via a trigger. For these 26 rows that mirror never happened.

Mark's memory note `dispatch_v2_only` already flagged "v1→v2 mirror silently drops rows (2026-05-04)." Today's cancel batch confirms the bug is still active for the `pr_number` column specifically: rows mirror their existence (state, story_id, repo) but not their PR linkage.

This story finds the gap, fixes it for future rows, and one-shot backfills any in-flight rows that the bug stranded.

## Target User / Use Case

**Sweeper correctness:** Every v2 background task that joins `dispatch_jobs.pr_number` needs that column to reflect reality. Today it doesn't, and that's why 26 in_review rows sat for up to 1.5 weeks without progressing. Fixing this unblocks every downstream automation that gates on PR linkage.

## Success Criteria

- [ ] Root-cause the v1→v2 `pr_number` sync gap:
  - Inspect the mirror trigger(s) on `dispatch_items` writes. If a trigger is supposed to copy `pr_number` to `dispatch_jobs` and isn't, fix it. If no such trigger exists, add it.
  - Cover both INSERT (v1 row created with pr_number set) and UPDATE (v1 row's pr_number changed after creation).
- [ ] New migration `scripts/migrations/059_v1_v2_pr_number_sync.sql`:
  - **Phase 1 (forward sync):** install/replace the trigger so any new write to `dispatch_items.pr_number` syncs to `dispatch_jobs.pr_number` for the matching `(repo, story_id)`. Use `COALESCE` so a v2-native value isn't overwritten by NULL.
  - **Phase 2 (backfill):** one-shot `UPDATE dispatch_jobs dj SET pr_number = di.pr_number FROM dispatch_items di WHERE dj.repo = di.repo AND dj.story_id = di.story_id AND di.pr_number IS NOT NULL AND dj.pr_number IS NULL`. Should be a no-op now that today's 26-row cancel drained the cohort, but the migration must remain safe for any future stranded rows.
  - **Phase 3 (correlation_key)**: same backfill logic for `dispatch_jobs.correlation_key` (`repo:R|pr:N`) if NULL and `pr_number` is now set.
- [ ] Migration is idempotent — second apply is a no-op. Use `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` before `CREATE TRIGGER`, and guarded `WHERE … IS NULL` on backfill.
- [ ] Trigger is **additive**, not destructive. Do NOT mutate any v2-native rows whose `pr_number` is already set.
- [ ] New tests in `tests/test_epic_queue_v2_q2.py` under a new class `TestV1V2PrNumberSync`:
  - INSERT into `dispatch_items` with `pr_number` set → `dispatch_jobs.pr_number` populated for the matching `(repo, story_id)` within the same transaction.
  - UPDATE `dispatch_items.pr_number` from NULL → 123 → `dispatch_jobs.pr_number` updates to 123.
  - UPDATE `dispatch_items.pr_number` from 123 → NULL (rare but possible) → `dispatch_jobs.pr_number` left unchanged (COALESCE protects against accidental NULL-out).
  - Backfill phase: pre-existing v1 row with `pr_number=456`, v2 row with `pr_number=NULL` → after migration, v2 row has `pr_number=456` and `correlation_key='repo:R|pr:456'`.
  - Idempotency: applying 059 twice produces no second backfill update and no trigger duplicate.
  - Regression: existing Q2 tests pass with 059 in fixture.
- [ ] Migration 059 added to the `tests/test_epic_queue_v2_q2.py::raw_conn` fixture chain.

## Constraints
| Constraint | Value |
|------------|-------|
| Language | SQL (Postgres 15+) + Python tests |
| Trigger style | `AFTER INSERT OR UPDATE` on `dispatch_items`, `FOR EACH ROW`, `EXECUTE FUNCTION` |
| Backfill safety | Use `COALESCE(dj.pr_number, di.pr_number)` so v2-native values are never clobbered |
| Migration style | Idempotent, transactional |
| Test DB | Use the existing `TEST_DATABASE_URL` pattern from Q2 tests |
| Coordination | Must not conflict with STORY-914's migration 057 or STORY-917's migration 058. Take number 059. |
| Mutation guard | Do NOT change `dispatch_v2_events`, `dispatch_state_apply`, or the state-trigger on `dispatch_state_current`. Additive only. |

## Security Constraints (Non-Negotiable)

- [ ] Trigger function runs `SECURITY INVOKER` (default) — do NOT use `SECURITY DEFINER` unless required and documented.
- [ ] Backfill must not touch rows whose `dispatch_jobs.state` is already terminal (`completed`, `cancelled`, `failed`, `dead_letter`). Filter `dispatch_state_current.state NOT IN (terminal)` in the WHERE.
- [ ] Migration must be reversible-by-design: dropping the trigger un-installs the sync. The backfill itself is one-shot data; it doesn't need rollback.

## Test Criteria

- [ ] T01 — forward sync on INSERT: new v1 row with pr_number → v2 row gets pr_number.
- [ ] T02 — forward sync on UPDATE NULL→N: v1 row's pr_number changes from NULL to 123 → v2 row picks up 123.
- [ ] T03 — NULL guard: v1 row pr_number changes from 123 → NULL → v2 row keeps 123.
- [ ] T04 — backfill happy path: pre-existing stranded row in v1 with pr_number, v2 with NULL → after migration, v2 has the value AND correlation_key.
- [ ] T05 — backfill respects terminal states: if v2 row is already `completed`/`cancelled`, backfill skips it.
- [ ] T06 — idempotency: apply 059 twice, no extra writes, no trigger duplicates.
- [ ] T07 — additive: a v2-native row with pr_number set and no matching v1 row → migration does nothing to it.
- [ ] T08 — regression: existing test_epic_queue_v2_q2.py suite passes.

## Validation

- Apply 059 to a prod snapshot. Confirm:
  - Trigger exists on `dispatch_items` (visible via `pg_trigger`).
  - Backfill SQL emits 0 updates (cohort drained today). On a snapshot with a fresh stranded row, emits exactly one update.
- Synthesize a test: insert a v1 row with pr_number → confirm v2 row updates within the same transaction.

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `scripts/migrations/059_v1_v2_pr_number_sync.sql` (new), `tests/test_epic_queue_v2_q2.py` (new test class + fixture update) |
| Reference triggers | Existing mirror triggers in early v2 migrations (search `scripts/migrations/05*.sql` for `CREATE TRIGGER.*dispatch_items` and `dispatch_v2_events` state trigger) |
| DB tables | `dispatch_items` (v1, source of truth for pr_number on legacy rows), `dispatch_jobs` (v2, consumed by all sweepers), `dispatch_state_current` (terminal-state gate for backfill) |
| Failure incident | 2026-05-12 — 26 in_review rows stranded for 1.7–10.8 days; all had pr_number=NULL in v2 despite v1 having values. Bulk-cancelled rather than recovered. |
| Pairs with | STORY-919 (backfill sweeper handles rework jobs — different failure mode for same data shape) |
| Memory note | `dispatch_v2_only` — predicted this exact bug class on 2026-05-04; this story closes it |
| Out of scope | Removing the v1 tables entirely. v1 stays as legacy read-path for QV2-FU-2 (separate retirement story). |
