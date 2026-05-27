# STORY-545 — Test Design: Fix PR #85 AmbiguousStoryError + Migration 007 Collision

**Scope:** small
**Coverage target:** 50% (critical paths)
**Test file:** `tests/ops_console/test_story545_complete_ambiguous.py`

## Test Strategy

STORY-545 has two concerns:
1. **Migration renumber** — `007_composite_key_story_repo.sql` → `008_composite_key_story_repo.sql`
2. **Graceful AmbiguousStoryError handling in `complete()`** — catch, log warning, pick most recently claimed row

Both are testable without a live PostgreSQL instance using the FakeConn/FakePool pattern established in `test_dispatch_claim_sync.py`.

## Test Groups

### Group A: Migration File Correctness (2 tests)

| Test | What It Verifies |
|------|------------------|
| `test_migration_008_composite_key_exists` | File `scripts/migrations/008_composite_key_story_repo.sql` exists on disk |
| `test_migration_007_composite_key_does_not_exist` | File `scripts/migrations/007_composite_key_story_repo.sql` does NOT exist (collision resolved) |

### Group B: complete() Graceful Ambiguity Handling (4 tests)

These tests target the `complete()` method after STORY-531's `_resolve_row()` + `AmbiguousStoryError` are merged and the STORY-545 fix is applied.

| Test | What It Verifies |
|------|------------------|
| `test_complete_ambiguous_no_repo_picks_most_recently_claimed` | When `repo=None` and `_resolve_row()` raises `AmbiguousStoryError`, `complete()` catches it, logs a warning, picks the row with the most recent `claimed_at`, and returns the completed row dict |
| `test_complete_ambiguous_no_repo_logs_warning` | Same scenario — verify `logger.warning` is called with story_id and candidate repos |
| `test_complete_single_row_no_repo_no_warning` | When `repo=None` and only one active row exists, `complete()` returns normally with no warning logged (backward compat) |
| `test_complete_with_explicit_repo_exact_match` | When `repo` is provided, `complete()` uses exact `(story_id, repo)` match — no ambiguity path |

### Group C: Error Observability (Gate 10) (1 test)

| Test | What It Verifies |
|------|------------------|
| `test_complete_ambiguous_warning_includes_context` | The warning log message includes the story_id and the list of candidate repos for observability |

## RED State Rationale

Tests in Group A will FAIL because:
- Migration 008 does not exist yet (it's still named 007 on the PR branch)

Tests in Group B will FAIL because:
- Current `complete()` on `main` has no `repo` parameter
- `AmbiguousStoryError` class does not exist on `main`
- `_resolve_row()` helper does not exist on `main`
- The graceful catch-and-fallback logic doesn't exist anywhere yet

## Defensive Gates Checklist

- [x] Gate 1 (Null/None boundary): `test_complete_single_row_no_repo_no_warning` covers `repo=None` happy path
- [x] Gate 10 (Error observability): `test_complete_ambiguous_warning_includes_context` verifies structured logging on catch
- [x] Output-variance: `test_complete_ambiguous_no_repo_picks_most_recently_claimed` vs `test_complete_with_explicit_repo_exact_match` — two different inputs, two different code paths, verifiable different behaviors
- [x] No external APIs touched — no Gate 2a needed
- [x] No file uploads — no Gate 7 needed
- [x] No ORM/Alembic — raw SQL migrations, verified by file existence tests
- [x] No frontend — no Playwright/Vitest needed
