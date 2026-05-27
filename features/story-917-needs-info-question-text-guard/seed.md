# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Criticality | important |
| Feature Name | needs-info-question-text-guard |
| Frontend | false |

## Problem Statement

On 2026-05-12 a triage of the dispatch queue found **at least 14 rows** sitting in `needs_info` (or `in_review`/`leased` while v1 still listed them as `needs_info`) with `question_text=NULL`, `needs_info_path=NULL`, and no `QUESTION.md` anywhere on the story's branch. Three of those rows reproduced the bug in real time — they were enqueued fresh and flipped to `needs_info` within an hour with nothing for a human or Morris to act on.

This is **leak #7** discovered after the 5-fix queue-stability batch shipped earlier the same day:
- STORY-902 + the working-tree submitted→in_review guard fixed `in_review` rows with NULL pr_number.
- STORY-873/872 fixed the duplicate-failed-event and unknown→typed classifier paths.
- The duplicate `git_rebase_failed` policy key was removed.
- The new `dispatch_stale_unlinked_in_review_sweeper` requeues stale review rows.

**None** of those fixes addressed `needs_info`. The transition still accepts an empty `event_data` and the row enters `needs_info` with no recorded question. Morris by design does not touch `needs_info` rows (those are the human boundary), so they accumulate. The cleanup pattern after the 11+1 cancels showed every single zombie was the same shape: agent flipped state, wrote nothing.

The fix is the same shape used for `submitted → in_review` requiring `pr_number` (the working-tree guard from 2026-05-12 in `dispatch_v2_service.transition`): refuse the transition unless the event payload provides usable question context, and back the refusal with a DB trigger so callers that bypass the service can't slip violators in.

## Target User / Use Case

**Ops automation correctness + Mark's `/answer-needs-info` workflow:** every row in `needs_info` represents a real, answerable question. A row in `needs_info` without `question_text` (and without a corresponding `needs_info_path` pointing at a real, fetchable `QUESTION.md`) cannot be acted on — it just sits forever. After this story ships, the system refuses to create such rows.

## Success Criteria

- [ ] Service-layer guard in `tech_dev_agents/ops_console/services/dispatch_v2_service.py::DispatchV2Service.transition()` for `event_type == "needs_info"`:
  - Require `event_data.question_text` to be a non-empty string after `strip()`, **OR** `event_data.needs_info_path` to be a non-empty string after `strip()`. (Either provides a place a human can read the question.)
  - On violation, raise `InvalidEventDataError("needs_info transition requires non-empty question_text or needs_info_path in event_data")`.
  - Mirror the structural pattern of the existing `submitted` guard immediately above it in the same function (the one added 2026-05-12 for PR linkage).
  - When `question_text` is provided and the underlying `dispatch_jobs` row has `question_text IS NULL`, backfill `dispatch_jobs.question_text` in the same transaction (mirror how the `submitted` guard backfills `pr_number`).
- [ ] New migration `scripts/migrations/058_needs_info_requires_question.sql`:
  - **Phase 1 (data cleanup):** any pre-existing rows with `state='needs_info' AND question_text IS NULL AND needs_info_path IS NULL` get a `cancelled` event emitted via `dispatch_v2_events` INSERT with `actor='migration_058'` and `event_data={"reason":"zombie_needs_info_cleanup","source":"migration_058"}`. The existing state trigger flips them to `cancelled` — do not write to `dispatch_state_current` directly.
  - **Phase 2 (invariant):** Install a `BEFORE INSERT OR UPDATE` trigger on `dispatch_state_current` that joins `dispatch_jobs` and raises `EXCEPTION 'needs_info state requires question_text or needs_info_path on dispatch_jobs (job_id=%)'` when `NEW.state='needs_info'` AND `dispatch_jobs.question_text IS NULL` AND `dispatch_jobs.needs_info_path IS NULL`.
- [ ] Migration is idempotent — re-running it must be a no-op. Use `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` before `CREATE TRIGGER`, and a guarded `WHERE NOT EXISTS (...)` on the cleanup pass.
- [ ] Existing event-driven trigger on `dispatch_state_current` (sets `state`/`lane` from inserted events) must keep working — the new trigger fires `BEFORE` and only checks the would-be `needs_info` invariant.
- [ ] New tests in `tests/test_epic_queue_v2_q2.py` under a new class `TestNeedsInfoRequiresQuestion`:
  - Service guard: `transition(event_type='needs_info', event_data={})` raises `InvalidEventDataError`.
  - Service guard: `transition(event_type='needs_info', event_data={'question_text': '   '})` (whitespace only) raises.
  - Service guard: `transition(event_type='needs_info', event_data={'question_text': 'Hit merge conflict on file X.py — keep theirs or ours?'})` succeeds and backfills `dispatch_jobs.question_text`.
  - Service guard: `transition(event_type='needs_info', event_data={'needs_info_path': 'features/story-N/QUESTION.md'})` succeeds.
  - DB trigger: clean schema + insert violator state row directly → raises trigger exception.
  - DB trigger: cleanup phase of migration 058 emits exactly one `cancelled` event per pre-existing violator.
  - Migration idempotency: apply 058 twice → second apply is a no-op (no new cancel events).
  - Existing `transition` happy paths (other event_types) all still pass.
- [ ] Migration 058 added to the test fixture in `tests/test_epic_queue_v2_q2.py::raw_conn` after 057 (or alongside 056 if 057 hasn't landed yet — handle either ordering).

## Constraints
| Constraint | Value |
|------------|-------|
| Language | Python 3.12 (service guard) + SQL (Postgres 15+ migration) |
| Service pattern | Mirror the existing `submitted` guard in `dispatch_v2_service.transition()` — same control flow, same exception type |
| Migration style | Idempotent, transactional (BEGIN/COMMIT) |
| State trigger | Use the existing event-driven state machine — do NOT mutate `dispatch_state_current` directly in cleanup |
| Test DB | Use the `TEST_DATABASE_URL` pattern from existing Q2 tests |
| Backward compat | The service-layer guard is the fast-fail; the DB trigger is defense-in-depth. Both stay. |
| Mutation guard | Do NOT change `dispatch_v2_events` triggers, `dispatch_state_apply`, or the `_VALID_TRANSITIONS` table. Additive only. |
| Coordination | If STORY-914 (migration 057) is in flight or merged first, this story uses migration 058. If STORY-914 has not landed, this story still uses 058 (skip number 057). Do NOT renumber. |

## Security Constraints (Non-Negotiable)

- [ ] Trigger error message must NOT echo `question_text` values from other rows — only reference the offending `job_id`.
- [ ] Cleanup phase must not bulk-delete any rows — only emit `cancelled` events.
- [ ] Migration must be reversible-by-design: dropping the trigger un-enforces the invariant; cleanup events are durable history.
- [ ] `question_text` may contain user/PR content — must NOT be logged at INFO; DEBUG only.

## Test Criteria

- [ ] T01 — service guard: `event_type='needs_info'` with empty `event_data` → `InvalidEventDataError`.
- [ ] T02 — service guard: whitespace-only `question_text` → `InvalidEventDataError`.
- [ ] T03 — service guard: non-empty `question_text` → succeeds; `dispatch_jobs.question_text` backfilled.
- [ ] T04 — service guard: `needs_info_path` provided, no `question_text` → succeeds; `dispatch_jobs.needs_info_path` backfilled.
- [ ] T05 — DB trigger: direct INSERT into `dispatch_state_current` with `state='needs_info'` while `question_text IS NULL AND needs_info_path IS NULL` → raises trigger exception.
- [ ] T06 — DB trigger: legitimate `needs_info` transition (question_text set first) → succeeds.
- [ ] T07 — migration cleanup: pre-existing violator gets exactly one `cancelled` event emitted by migration 058.
- [ ] T08 — migration idempotency: apply 058 twice → no error, no extra cancel events on second apply.
- [ ] T09 — regression: all existing Q2 tests still pass with 058 in fixture.

## Validation

- Apply 058 to a snapshot of prod DB (or a copy) and confirm:
  - Number of `cancelled` events emitted equals the count of pre-existing zombies reported by `SELECT count(*) FROM dispatch_state_current sc JOIN dispatch_jobs j USING (job_id) WHERE sc.state='needs_info' AND j.question_text IS NULL AND j.needs_info_path IS NULL`
  - Post-migration that same SELECT returns 0
  - `pg_trigger` lists the new BEFORE trigger on `dispatch_state_current`
- After deploy, enqueue a fresh canary story; if the dispatch poller / agent attempts a `needs_info` transition without question text, expect a clean rejection in `dispatch_poller_v2` logs rather than a zombie row.

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/services/dispatch_v2_service.py` (add guard inside `transition()`), `scripts/migrations/058_needs_info_requires_question.sql` (new), `tests/test_epic_queue_v2_q2.py` (new test class + fixture update) |
| Reference patterns (in same file) | `transition()` submitted → in_review PR-link guard added 2026-05-12 (lines ~579–617) — exact structural template for the new needs_info guard |
| Reference migrations | `050_dispatch_v2_schema.sql` (event-driven state trigger pattern), `056_dispatch_v2_correlation_index_fix.sql` (idempotent BEGIN/COMMIT pattern), STORY-914 / migration 057 (parallel two-phase cleanup + invariant pattern for `in_review`) |
| DB columns referenced | `dispatch_jobs.question_text`, `dispatch_jobs.needs_info_path`, `dispatch_state_current.state`, `dispatch_v2_events` (event_type, event_data, actor) |
| Existing transition validator | `_validate_transition()` and `_VALID_TRANSITIONS` table at the bottom of `dispatch_v2_service.py` — do not modify; the new check is an extra guard inside `transition()` after `_validate_transition()` passes |
| Related skill | `/answer-needs-info` (consumes question_text/needs_info_path — this story makes that skill's input reliably present) |
| Pairs with | STORY-914 (in_review PR-link DB invariant — same shape, different state) |
| Out of scope | Building a UI for question_text; modifying the agent-side code that *writes* the needs_info event (a separate hardening pass — this story rejects malformed writes at the server) |
