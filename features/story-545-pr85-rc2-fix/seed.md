# STORY-545 — Fix PR #85 (STORY-531): Unhandled AmbiguousStoryError + Migration 007 Collision

**Scope:** small
**Repo:** tech-dev-agents
**Target Branch:** story-531/story-531
**Parent PR:** #85 (STORY-531: Composite `(story_id, repo)` unique key on dispatch_items)

## Context

PR #85 (STORY-531, branch `story-531/story-531`) introduced composite `(story_id, repo)` keys for the dispatch queue. Morris's code review (2026-04-22) identified two release-critical defects that block merge:

### RC-1: Migration 007 Filename Collision

PR #85 adds `scripts/migrations/007_composite_key_story_repo.sql`. Meanwhile, STORY-532 (PR #87, now merged to main) added `scripts/migrations/007_needs_info_state.sql`. Both PRs created migration 007 independently. The branch must be rebased onto `origin/main` and the STORY-531 migration renumbered to `008_composite_key_story_repo.sql` so it applies after the needs_info migration.

**Impact:** If applied as-is, the migration numbering is ambiguous. Operators running migrations sequentially would see two 007 files and not know the ordering.

### RC-2: Unhandled AmbiguousStoryError in `complete()` (and other service methods)

STORY-531 introduced `AmbiguousStoryError`, raised by `_resolve_row()` when `repo=None` and multiple active rows match. The HTTP route layer catches this and returns 409, but the **dispatch poller** (`deployment/hermes/dispatch_poller.py`) calls `POST /dispatch/complete/{story_id}` — and during the transition window (old pollers, new schema), `repo` may not be provided.

More critically, the design decision from STORY-531 Phase 4 analysis explicitly rejected silent fallback (Approach B) in favor of explicit errors. However, Morris's review notes that `complete_story` (the `complete()` method) should **not** hard-fail the entire completion flow when the ambiguity is resolvable. The service layer should:

1. **Log a warning** with the story_id and candidate repos.
2. **Pick the most recently claimed record** (`ORDER BY claimed_at DESC NULLS LAST LIMIT 1`).
3. **Continue** with the completion rather than raising an unrecoverable 500.

This is a pragmatic middle ground: the route still returns 409 for genuinely ambiguous GETs/operations where user disambiguation is appropriate, but for `complete()` — which is called at the end of a successful agent run — hard-failing loses work. The poller has the context to complete correctly; the service should be resilient.

## Problem Statement

PR #85 cannot merge to main because:
1. It conflicts with `origin/main` (STORY-532's migration 007 merged after STORY-531 branched).
2. The `complete()` method raises `AmbiguousStoryError` through `_resolve_row()` when `repo=None` with multiple active rows, and the poller calling the API may not pass `repo` during rolling deployments, causing a 500 → lost completion signal → story stuck in "claimed" forever.

## Target User

Fleet operators (Mark) and autonomous agents (Devon, Daisy, Dan, Derrick) whose completion signals must not be silently dropped during schema migration windows.

## Success Criteria

1. Branch `story-531/story-531` is rebased cleanly onto `origin/main` with all conflicts resolved preserving STORY-531 features.
2. Migration file is renumbered from `007_composite_key_story_repo.sql` to `008_composite_key_story_repo.sql` (no file named `007_composite_key_story_repo.sql` remains).
3. In `dispatch_db_service.py`, the `complete()` method catches `AmbiguousStoryError`, logs a structured warning (`logger.warning` with story_id and candidate_repos), selects the most recently claimed active record (`ORDER BY claimed_at DESC NULLS LAST`), and completes it successfully.
4. The `complete()` method returns the completed row dict (same return type as today) — callers see no behavioral change.
5. All existing tests pass (`pytest tests/ -q` — 394+ tests GREEN, PG-only tests SKIP cleanly).
6. PR #85 is push-able (force-push with lease to `story-531/story-531`) and no longer has merge conflicts with `origin/main`.

## Acceptance Diff

The implementation MUST modify these files:

- `scripts/migrations/008_composite_key_story_repo.sql` — renamed from `007_composite_key_story_repo.sql`; file `007_composite_key_story_repo.sql` must NOT exist
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — must-contain `except AmbiguousStoryError`, must-contain `logger.warning`, must-contain `claimed_at DESC`

## Test Criteria

1. `complete()` with `repo=None` and two active rows for the same story_id across different repos → returns the row with the most recent `claimed_at`, logs a warning (no exception raised).
2. `complete()` with `repo=None` and one active row → returns that row (backward-compat, no warning).
3. `complete()` with explicit `repo=` → returns exact match (unchanged behavior).
4. Migration 008 applies cleanly after migration 007 (needs_info_state) on a fresh database.
5. No file named `007_composite_key_story_repo.sql` exists in the repo.

## Scope Classification

**Small.** Two files changed (migration rename + service method fix). No new API surface. No schema change beyond renumbering. Phase path: 1 → 7 → 8 → Done.

## Constraints

| Dimension | Constraint |
|-----------|-----------|
| Branch | Must work on `story-531/story-531` (PR #85), not create a new PR |
| Rebase | Must rebase onto `origin/main`, not merge — keeps linear history |
| Conflict resolution | Keep STORY-531 features; integrate STORY-532 migration 007 |
| Backward compat | `complete()` return type and HTTP status codes unchanged for single-row case |
| Error handling | `AmbiguousStoryError` in `complete()` becomes a warning, not a failure |
| Fallback heuristic | Most recently claimed = `ORDER BY claimed_at DESC NULLS LAST` |

## Risks

| Risk | Mitigation |
|------|-----------|
| Rebase conflicts beyond migration file | Resolve keeping branch features; run full test suite after rebase |
| "Most recently claimed" heuristic picks wrong row | Acceptable: this only fires during rolling deploy with `repo=None`. Once all pollers pass `repo=`, the code path is never reached. Warning log provides observability. |
| Other methods also need AmbiguousStoryError softening | Out of scope. Only `complete()` needs it — other methods (claim, pause, cancel) require user disambiguation and correctly return 409. |

## Validation

After Phase 8 lands:
1. `python-tests` CI is GREEN on the updated PR #85 branch.
2. Migration 007 applies cleanly on a fresh integration DB (no filename collision with the existing revision).
3. Hand-calling `complete()` on an ambiguous `(story_id, repo=None)` row returns the most-recently-claimed row with a warning log, instead of raising `AmbiguousStoryError`. Verified against a seeded fixture with two rows.
