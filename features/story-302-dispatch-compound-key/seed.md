# Seed: STORY-302 — Compound-Key Dispatch Queue (per-repo story IDs)

**Story:** STORY-302
**Date:** 2026-04-15
**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done
**Branch:** `story-302/dispatch-compound-key`

---

## Problem Statement

The dispatch queue dedupes items by `story_id` alone. The SDLC convention treats story IDs as **repo-scoped** (each repo independently assigns STORY-001, STORY-002, …). When two repos happen to allocate the same number, the dispatch queue treats them as duplicates and rejects the second one.

**Real incident on 2026-04-15:**

Mark was preparing a SharePoint client story. Morris had already enqueued an unrelated story numbered STORY-254 minutes earlier (his story was in-flight for Derrick). When Mark posted his STORY-254, the dispatch API returned `409 Conflict: STORY-254 already in dispatch queue`. Mark had to manually renumber his story to STORY-301 to land it. The collision wasted a dispatch cycle and forced rename of unrelated work tracking, planning, and seed folders.

This will happen again as multi-agent + multi-repo throughput grows. Today we have ~8 active repos and 3 agents each enqueuing stories — collision probability rises with every dispatch.

## Goals

1. **Story IDs are scoped to a repo, not the queue.** `(repo, story_id)` is the natural unique key. Same story number can exist in different repos without conflict.
2. **Backward compatible.** Existing single-arg lookups (`/api/dispatch/complete/STORY-227`) still work when the story_id is unambiguous across repos. When ambiguous, the API requires repo qualification.
3. **No data loss in migration.** All currently-enqueued and completed dispatch items keep their identity.

## Non-goals

- Don't reformat existing story IDs (no `STORY-tech-dev-agents-227` rewrite). Keep IDs short.
- Don't break the SDLC `features/story-XXX-slug/` folder convention. Folders stay per-repo.
- Don't change the `STORY-NNN` regex pattern itself (`^STORY-\d+$`) — only how it's keyed.

## Acceptance Criteria

| ID | Criterion | Measurable |
|---|---|---|
| AC-1 | Two stories with the same `story_id` but different `repo` can both be enqueued | `POST /api/dispatch` with `(STORY-100, repo-a)` and `(STORY-100, repo-b)` both return 201 |
| AC-2 | DB `dispatch_items` has `(repo, story_id, status)` partial unique index instead of `(story_id)` partial unique | `\d dispatch_items` shows the new constraint |
| AC-3 | `/api/dispatch/complete/<id>` accepts an optional `?repo=<name>` query param to disambiguate | When two stories with same id exist in different repos, providing `?repo=` selects correctly |
| AC-4 | When ambiguous and no `repo` provided, complete returns 400 with a list of (repo, story_id) candidates | Test: enqueue `(STORY-100, a)` + `(STORY-100, b)`, claim both, POST complete without `?repo=` returns 400 |
| AC-5 | Dispatch poller updated to send `repo` along with story_id on complete + claim calls | Tests + live verification |
| AC-6 | Migration is idempotent and reversible | Up + down migration scripts |
| AC-7 | All existing 140+ ops_console tests still pass after migration | `pytest tests/ops_console/` green |

## Out of Scope

- Web UI changes (the dashboard already groups by repo from STORY-024). May get cosmetic update later.
- Cross-repo dependency tracking ("STORY-100 in repo-a depends on STORY-200 in repo-b"). That's a future story.
- Renaming any of the existing `features/story-XXX-*` folders.

## Technical Plan

### 1. DB migration (`scripts/migrations/004_compound_key_dispatch.sql`)

```sql
-- Drop the global story_id partial-unique constraint
DROP INDEX IF EXISTS uq_story_active_idx;

-- Replace with (repo, story_id) partial unique
CREATE UNIQUE INDEX uq_repo_story_active_idx
    ON dispatch_items (repo, story_id)
    WHERE status IN ('pending', 'claimed');

-- (Optional) Index for efficient lookups by story_id alone
CREATE INDEX IF NOT EXISTS idx_story_id ON dispatch_items(story_id);
```

### 2. DB service (`dispatch_db_service.py`)

- `enqueue()` already takes `repo` as a parameter — no signature change
- `get(story_id, repo: str | None = None)` — accept optional repo qualifier
- `complete(story_id, repo: str | None = None, ...)` — same
- `claim(story_id, repo: str | None = None, ...)` — same
- When `repo` is None and the story_id appears in multiple rows, raise `AmbiguousStoryError(story_id, candidates: list[tuple[str,str]])`

### 3. Route (`routes/dispatch.py`)

- `POST /dispatch/complete/{story_id}?repo=<name>` — accept query param, pass to service
- Convert `AmbiguousStoryError` → 400 with the candidate list in the response body
- Same pattern for `claim`, `cancel`, `get_one`

### 4. Poller (`dispatch_poller.py`)

- `_report_complete` already has access to `repo` via `start_story` parameter — append `?repo=<repo>` to the URL
- `_claim` (similar treatment)
- Tests updated to assert the query param appears

### 5. Tests

- `tests/ops_console/test_dispatch_db_service.py`: same-id-different-repo enqueue, ambiguous-lookup error, qualified lookup
- `tests/ops_console/test_routes_dispatch.py`: 400 on ambiguous, 200 on qualified, 200 on unambiguous-without-repo (backward compat)
- `tests/deployment/test_dispatch_poller.py`: PL-01/PL-03 + new PL-08 for repo qualifier

## Constraints

- Migration MUST be runnable on the live ops-console without dropping data (use ALTER + CREATE INDEX, not table rebuild)
- Must not break the dispatch_poller running on Dan/Derrick/Morris VMs that are mid-cycle
- Existing dispatch IDs in completed/cancelled status keep the same semantics — only the active partial-unique changes

## Rollout

1. Apply migration on ops-console postgres (idempotent — safe to re-run)
2. Deploy ops-console with new route handlers
3. Deploy poller updates to Dan + Derrick VMs simultaneously (small enough to be a single restart)
4. Verify with two enqueues of the same story_id in different repos

## Success Criteria

- No more 409 collisions across repos
- Mark can dispatch STORY-100 to advertising-amazon and STORY-100 to tech-dev-agents in the same minute without renumbering
- All dispatch poller flows work unchanged

## Version

0.1.0
