# STORY-531: Feature Specification — Composite `(story_id, repo)` Unique Key

**Scope:** Medium | **Approach:** A (composite partial index + `AmbiguousStoryError`)

## Overview

Replace the `dispatch_items` active-state partial unique index on `(story_id)` with a composite partial unique index on `(story_id, repo)`. Update `DispatchDBService` methods to accept an optional `repo` qualifier and raise `AmbiguousStoryError` when a bare `story_id` matches more than one row. Update `/api/dispatch/*` routes with an optional `?repo=` query parameter. Update `dispatch_poller.py` to thread `repo` through `/complete` and `/fail` calls.

**File footprint:** 6 files (1 new migration, 1 new test file, 4 edited files).

---

## 1. Database Migration

**File:** `scripts/migrations/007_composite_key_story_repo.sql` (NEW)

```sql
-- STORY-531: Composite (story_id, repo) partial unique index on dispatch_items.
--
-- Problem: the current partial unique index on (story_id) alone collides when
-- the same STORY-N is reused across repos. enqueue()'s terminal-state DELETE
-- by story_id alone has silently erased history for STORY-427, 443, 495, 527.
--
-- Fix: scope the unique constraint to (story_id, repo), and include 'in_review'
-- in the active set (closes a latent gap from migration 004 where in_review
-- rows were not covered).
--
-- Idempotent: safe to re-run (IF EXISTS / IF NOT EXISTS).
-- Concurrency-safe: uses CREATE INDEX CONCURRENTLY outside a transaction block.
--
-- DOWN path (commented at bottom): NOT safe to run if cross-repo active rows
-- exist. Documented as one-way.

-- Pre-flight sanity: no existing active row should violate the new constraint.
-- (Impossible under the current single-column unique index, but we assert.)
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count FROM (
        SELECT story_id, repo
        FROM dispatch_items
        WHERE status IN ('pending', 'claimed', 'in_review', 'paused')
        GROUP BY story_id, repo
        HAVING COUNT(*) > 1
    ) t;
    IF dup_count > 0 THEN
        RAISE EXCEPTION 'STORY-531 migration aborted: % duplicate (story_id, repo) active rows found — manual cleanup required', dup_count;
    END IF;
END $$;

-- Build the new composite index concurrently (no table lock).
-- Must be outside a transaction block for CONCURRENTLY to work.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_story_repo_active_idx
    ON dispatch_items (story_id, repo)
    WHERE status IN ('pending', 'claimed', 'in_review', 'paused');

-- Supporting lookup index for service-layer (story_id, repo) queries.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dispatch_story_repo
    ON dispatch_items (story_id, repo);

-- Drop the old single-column active index — ONCE the new index is validated.
DROP INDEX IF EXISTS uq_story_active_idx;

-- DOWN (manual, one-way hazard):
--
--   -- Only safe if NO cross-repo active rows exist:
--   SELECT story_id, COUNT(DISTINCT repo) FROM dispatch_items
--    WHERE status IN ('pending','claimed','in_review','paused')
--    GROUP BY story_id HAVING COUNT(DISTINCT repo) > 1;
--   -- If the above returns rows, down migration will fail.
--
--   DROP INDEX IF EXISTS uq_story_repo_active_idx;
--   DROP INDEX IF EXISTS idx_dispatch_story_repo;
--   CREATE UNIQUE INDEX uq_story_active_idx
--       ON dispatch_items (story_id)
--       WHERE status IN ('pending', 'claimed', 'paused');
--   -- NOTE: old index did NOT cover in_review — this is a regression acceptable
--   -- only for emergency rollback.
```

**Migration notes:**
- Uses `CREATE INDEX CONCURRENTLY` (PostgreSQL best practice for live schema changes) — no table lock, safe under concurrent writes.
- Must be executed outside a BEGIN/COMMIT block. The migration runner must invoke this file with statement-level execution (no transaction wrapper) — the DO block at the top self-aborts on duplicates; CREATE INDEX CONCURRENTLY proceeds independently.
- If the migration runner wraps every file in a transaction, **split this file into 3 sequential migrations:** `007a_preflight.sql` (DO block, transactional), `007b_build_composite_index.sql` (CONCURRENTLY, non-transactional), `007c_drop_old_index.sql` (transactional). To be confirmed against `scripts/migrations/run_migrations.py` behaviour during Phase 8.

---

## 2. Service Layer (`dispatch_db_service.py`)

### 2.1 New error class

```python
class AmbiguousStoryError(DispatchDBError):
    """Raised when a bare story_id matches multiple active rows across repos
    and no `repo=` qualifier was provided.

    Callers must retry with the `repo` argument set.
    """
    def __init__(self, story_id: str, candidate_repos: list[str]) -> None:
        self.story_id = story_id
        self.candidate_repos = candidate_repos
        super().__init__(
            f"{story_id} exists in multiple repos: {candidate_repos} — "
            "specify a repo qualifier"
        )
```

### 2.2 Resolution helper

Add a private helper used by all lookup methods:

```python
async def _resolve_row(
    self,
    conn: asyncpg.Connection,
    story_id: str,
    repo: str | None,
    *,
    statuses: tuple[str, ...] | None = None,
    for_update: bool = False,
) -> asyncpg.Record | None:
    """Return the single row matching (story_id, repo) or None.

    Resolution rules:
      - repo provided           → exact (story_id, repo) match
      - repo None + 0 rows      → None (caller raises NotFoundError)
      - repo None + 1 row       → that row (backward-compat)
      - repo None + >1 rows     → AmbiguousStoryError

    If ``statuses`` is given, limits the search to those statuses.
    If ``for_update`` is True, adds ``FOR UPDATE`` to the SELECT.
    """
    status_clause = ""
    params: list = [story_id]
    if statuses is not None:
        status_clause = f" AND status = ANY($2)"
        params.append(list(statuses))
    repo_clause = ""
    if repo is not None:
        repo_clause = f" AND repo = ${len(params) + 1}"
        params.append(repo)
    lock = " FOR UPDATE" if for_update else ""

    query = (
        f"SELECT * FROM dispatch_items "
        f"WHERE story_id = $1{status_clause}{repo_clause}{lock}"
    )
    rows = await conn.fetch(query, *params)
    if len(rows) == 0:
        return None
    if len(rows) == 1:
        return rows[0]
    # Multiple rows — ambiguous only if no repo was given
    if repo is None:
        candidate_repos = sorted({r["repo"] for r in rows})
        raise AmbiguousStoryError(story_id, candidate_repos)
    # repo given + >1 rows: impossible under the composite unique index,
    # but defensive — return the first.
    return rows[0]
```

### 2.3 Method signature changes

**All lookup methods gain `repo: str | None = None` as a keyword-only argument.** Behaviour:
- If `repo` is provided, the method scopes WHERE clauses to `(story_id, repo)`.
- If `repo` is `None`, methods call `_resolve_row()` for disambiguation and raise `AmbiguousStoryError` when >1 active row exists.

| Method | Signature change | WHERE clauses touched |
|--------|------------------|-----------------------|
| `enqueue()` | unchanged — `repo` is already a required arg | SELECT existing + DELETE terminal now both filter by `(story_id, repo)` |
| `list_queue()` | unchanged | no change — returns all rows |
| `next_pending()` | unchanged | no change |
| `set_priority()` | `+ repo: str \| None = None` | SELECT + UPDATE scoped |
| `claim()` | `+ repo: str \| None = None` | UPDATE + exists check scoped |
| `force_claim()` | `+ repo: str \| None = None` | SELECT + UPDATE scoped |
| `pause()` | `+ repo: str \| None = None` | SELECT + UPDATE scoped |
| `cancel()` | `+ repo: str \| None = None` | UPDATE + exists check scoped |
| `get()` | `+ repo: str \| None = None` | SELECT scoped |
| `complete()` | `+ repo: str \| None = None` | UPDATE + exists checks scoped |
| `fail()` | `+ repo: str \| None = None` | SELECT + UPDATE scoped |
| `transition_to_review()` | `+ repo: str \| None = None` | UPDATE + exists check scoped |
| `history()` | `+ repo: str \| None = None` (future-use, optional filter) | WHERE repo = $N when given |
| `in_progress_count()` | unchanged | no change |
| `pending_count()` | unchanged | no change |
| `recover_stale_claims()` | unchanged | fleet-wide, no story_id |

### 2.4 Example refactor — `claim()`

**Before:**
```python
async def claim(self, story_id: str, agent_name: str) -> dict[str, Any]:
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE dispatch_items
               SET status = 'claimed', claimed_by = $1, claimed_at = now(), updated_at = now()
               WHERE story_id = $2 AND status IN ('pending', 'paused')
               RETURNING *""",
            agent_name, story_id,
        )
        if row is not None:
            return _row_to_dict(row)
        exists = await conn.fetchval(
            "SELECT status FROM dispatch_items WHERE story_id = $1 AND status IN ('pending', 'claimed', 'paused')",
            story_id,
        )
        if exists == "claimed":
            raise AlreadyClaimedError(f"{story_id} already claimed")
        raise NotFoundError(f"{story_id} not found in pending/paused queue")
```

**After:**
```python
async def claim(
    self,
    story_id: str,
    agent_name: str,
    *,
    repo: str | None = None,
) -> dict[str, Any]:
    async with self._pool.acquire() as conn:
        async with conn.transaction():
            # Disambiguate (raises AmbiguousStoryError if needed)
            target = await self._resolve_row(
                conn, story_id, repo,
                statuses=("pending", "claimed", "paused"),
                for_update=True,
            )
            if target is None:
                raise NotFoundError(f"{story_id} not found in pending/paused queue")
            if target["status"] == "claimed":
                raise AlreadyClaimedError(f"{story_id} already claimed")

            row = await conn.fetchrow(
                """UPDATE dispatch_items
                   SET status = 'claimed', claimed_by = $1,
                       claimed_at = now(), updated_at = now()
                   WHERE id = $2 AND status IN ('pending', 'paused')
                   RETURNING *""",
                agent_name, target["id"],
            )
            if row is None:  # race: someone else claimed between resolve and update
                raise AlreadyClaimedError(f"{story_id} already claimed")
            return _row_to_dict(row)
```

**Key invariants of the refactor pattern:**
1. All lookups go through `_resolve_row()`.
2. Update statements operate on the row's PRIMARY KEY (`id`) after resolution — avoids second `story_id` lookup.
3. `FOR UPDATE` lock during resolution prevents TOCTOU between resolve and update.
4. `async with conn.transaction()` wraps resolve + update.

### 2.5 `enqueue()` refactor (root-cause fix)

**Before (lines 99–137):**
```python
existing = await conn.fetchrow(
    "SELECT status FROM dispatch_items WHERE story_id = $1",
    story_id,
)
if existing:
    status = existing["status"]
    if status in ("pending", "claimed", "in_review", "paused"):
        raise DuplicateDispatchError(...)
    await conn.execute("DELETE FROM dispatch_items WHERE story_id = $1", story_id)
```

**After:**
```python
async with conn.transaction():
    existing = await conn.fetchrow(
        "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
        story_id, repo,
    )
    if existing:
        status = existing["status"]
        if status in ("pending", "claimed", "in_review", "paused"):
            raise DuplicateDispatchError(
                f"{story_id} already has an active dispatch in {repo} (status={status})"
            )
        # Terminal state IN THIS REPO only — remove old row so we can re-enqueue.
        # Rows in OTHER repos are preserved (root-cause fix for STORY-531).
        await conn.execute(
            "DELETE FROM dispatch_items WHERE story_id = $1 AND repo = $2",
            story_id, repo,
        )

    try:
        row = await conn.fetchrow(
            """INSERT INTO dispatch_items
                   (story_id, repo, scope, prompt, enqueued_by, title)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING *""",
            story_id, repo, scope, prompt, enqueued_by, title,
        )
    except asyncpg.UniqueViolationError:
        raise DuplicateDispatchError(
            f"{story_id} already has an active dispatch in {repo}"
        )
return _row_to_dict(row)
```

---

## 3. Route Layer (`routes/dispatch.py`)

### 3.1 Route signature additions

Each of the 7 `{story_id}` endpoints gains an optional `repo: str | None = Query(None)` parameter. The route passes it through to the service.

**Example — `claim_story`:**
```python
@router.post("/dispatch/claim/{story_id}", response_model=ClaimResponse)
async def claim_story(
    story_id: str,
    body: ClaimRequest,
    request: Request,
    repo: str | None = Query(None, description="Disambiguate when story_id exists in multiple repos"),
):
    db_svc = _get_db_svc(request)
    # ... (existing auto-register block)
    try:
        row = await db_svc.claim(story_id, body.agent_name, repo=repo)
    except AmbiguousStoryError as exc:
        raise HTTPException(
            409,
            {
                "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
                "story_id": story_id,
                "candidate_repos": exc.candidate_repos,
            },
        )
    except AlreadyClaimedError:
        raise HTTPException(409, f"{story_id} already claimed")
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found in pending queue")
    # ... (rest unchanged)
```

Add `AmbiguousStoryError` to the import block:
```python
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    AmbiguousStoryError,  # NEW
    DuplicateDispatchError,
    InvalidTransitionError,
    NotFoundError,
)
```

### 3.2 Endpoints requiring `?repo=` support

| Endpoint | Method | Disambiguation needed? |
|----------|--------|-------------------------|
| `POST /dispatch/claim/{story_id}` | claim | Yes |
| `DELETE /dispatch/queue/{story_id}` | cancel | Yes |
| `POST /dispatch/complete/{story_id}` | complete | Yes |
| `POST /dispatch/fail/{story_id}` | fail | Yes |
| `POST /dispatch/review/{story_id}` | transition_to_review | Yes |
| `POST /dispatch/reclaim/{story_id}` | force_claim | Yes |
| `POST /dispatch/pause/{story_id}` | pause | Yes |
| `POST /dispatch/priority` (body.story_id) | set_priority | Yes — add `repo` to `PriorityRequest` body |

### 3.3 Ambiguity error response shape

HTTP 409, body:
```json
{
  "detail": "STORY-527 exists in multiple repos: ['advertising-amazon', 'tech-dev-agents'] — add ?repo=",
  "story_id": "STORY-527",
  "candidate_repos": ["advertising-amazon", "tech-dev-agents"]
}
```

FastAPI's default error response model serialises dict bodies correctly when passed to `HTTPException(status_code, detail=<dict>)`.

### 3.4 `PriorityRequest` model update

**File:** `tech_dev_agents/ops_console/models/responses.py`

```python
class PriorityRequest(BaseModel):
    story_id: str
    priority: int
    repo: str | None = None  # NEW — optional disambiguator
```

---

## 4. Dispatch Poller (`deployment/hermes/dispatch_poller.py`)

### 4.1 `_report_complete()` — add `repo`

```python
def _report_complete(
    *,
    session,
    base_url: str,
    api_key: str,
    story_id: str,
    repo: str,                    # NEW — required
    commit_sha: str,
    pr_number: int | None = None,
    duration_seconds: int | None = None,
    cost_usd: float | None = None,
    turns: int | None = None,
    error: bool = False,
) -> int:
    try:
        resp = session.post(
            f"{base_url}/api/dispatch/complete/{story_id}",
            params={"repo": repo},      # NEW
            json={...},
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        # ... unchanged
```

### 4.2 `_report_fail()` — thread existing `repo` arg onto URL

```python
def _report_fail(
    *,
    session,
    base_url: str,
    api_key: str,
    story_id: str,
    exit_code: int | None = None,
    repo: str = "",               # already exists
    scope: str = "small",
    prompt: str = "",
    duration_seconds: int | None = None,
):
    try:
        resp = session.post(
            f"{base_url}/api/dispatch/fail/{story_id}",
            params={"repo": repo} if repo else {},  # NEW
            json={"exit_code": exit_code},
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        # ... unchanged
```

### 4.3 Call-site update

At line 612 (inside `_run_and_complete`), the call to `_report_complete` must pass `repo=item["repo"]`:
```python
complete_status = _report_complete(
    session=session,
    base_url=base_url,
    api_key=api_key,
    story_id=story_id,
    repo=item["repo"],          # NEW — item is already in scope
    commit_sha=commit_sha,
    pr_number=pr_number,
    # ... (rest unchanged)
)
```

`_report_fail` calls already pass `repo=item.get("repo", "")` somewhere — verify during Phase 8 and add if missing.

### 4.4 Claim endpoint

`/claim/{story_id}` does **not** need `?repo=` threaded — at claim time, a story with a given `story_id` can only have ONE active row (the pending one), so there's no ambiguity possible during claim. (The composite unique index on `(story_id, repo)` allows same `story_id` across repos, but each repo's row is unambiguous by its repo.) The poller only needs to change `/complete` and `/fail`.

---

## 5. Test Matrix (detail in `test-design.md`)

**File:** `tests/ops_console/test_dispatch_composite_key.py` (NEW)

Required PostgreSQL integration test cases (existing `conftest.py` fixture provides a clean DB per test):

| # | Test | Covers |
|---|------|--------|
| T1 | `test_enqueue_same_story_different_repos_both_succeed` | AC2 cross-repo coexistence (201 + 201) |
| T2 | `test_enqueue_same_story_same_repo_active_returns_duplicate` | AC3 same-repo active duplicate → `DuplicateDispatchError` |
| T3 | `test_enqueue_same_story_same_repo_terminal_deletes_only_this_repo` | AC5 + AC8 terminal-delete scoped to `(story_id, repo)` |
| T4 | `test_enqueue_does_not_delete_terminal_in_other_repo` | AC4 repo-A complete must not affect repo-B row |
| T5 | `test_complete_disambiguates_by_repo` | `complete(..., repo="a")` only completes the repo-A row |
| T6 | `test_complete_raises_ambiguous_on_multi_match_without_repo` | `AmbiguousStoryError` when 2 active rows, no repo qualifier |
| T7 | `test_complete_backward_compat_single_row_no_repo` | `complete(story_id)` still works when exactly one row exists (backward-compat) |
| T8 | `test_claim_disambiguates_by_repo` | `claim(..., repo="a")` scopes to the right row |
| T9 | `test_fail_disambiguates_by_repo` | Same for `fail()` |
| T10 | `test_cancel_disambiguates_by_repo` | Same for `cancel()` |
| T11 | `test_pause_disambiguates_by_repo` | Same for `pause()` |
| T12 | `test_get_returns_none_when_no_repo_match` | `get(story_id, repo="nonexistent")` → None |
| T13 | `test_history_preserved_across_reenqueue_in_same_repo` | AC5 history chain — enqueue → complete → re-enqueue → old row still present in history query |
| T14 | `test_route_claim_accepts_repo_query_param` | `POST /dispatch/claim/STORY-X?repo=a` works |
| T15 | `test_route_complete_returns_409_on_ambiguous` | Route-level 409 response with candidate_repos list |
| T16 | `test_migration_idempotent` | Running migration 007 twice does not fail |
| T17 | `test_migration_preflight_aborts_on_duplicates` | DO-block check fails if pre-existing cross-repo active duplicates (simulated by direct INSERT bypassing constraints — use test-only fixture) |
| T18 | `test_in_review_included_in_unique_index` | Two `in_review` rows with same `(story_id, repo)` → UniqueViolationError |

**18 tests minimum.** Phase 7 finalises the list; `test-design.md` will document the exact assertions.

---

## 6. Deployment Plan

1. **Pre-deploy SELECT** on prod (operator runs manually):
   ```sql
   SELECT story_id, COUNT(DISTINCT repo)
   FROM dispatch_items
   WHERE status IN ('pending','claimed','in_review','paused')
   GROUP BY story_id HAVING COUNT(DISTINCT repo) > 1;
   ```
   Must return zero rows — guaranteed by the current single-column index, but asserted.

2. **PR merge triggers CI deploy.** `push-code.sh` (or ops-console CI) copies the migration + updated Python files to the ops-console host.

3. **Migration runner applies `007_composite_key_story_repo.sql`** on the running PG instance. `CREATE INDEX CONCURRENTLY` is safe under load.

4. **Service restart** picks up the new code with `AmbiguousStoryError`, `?repo=` routes, and the poller's new URL params.

5. **Smoke test** (post-deploy):
   - `POST /api/dispatch {"story_id":"STORY-999","repo":"test-alpha",...}` → 201
   - `POST /api/dispatch {"story_id":"STORY-999","repo":"test-beta",...}` → 201
   - `GET /api/dispatch/queue` → both visible
   - `POST /api/dispatch/complete/STORY-999?repo=test-alpha` → 200, only the alpha row is completed
   - `DELETE /api/dispatch/queue/STORY-999?repo=test-beta&reason=smoke test cleanup` → 200

6. **Backward-compat verification** — query existing production traffic:
   ```sql
   SELECT story_id, COUNT(*) FROM dispatch_items
   WHERE status IN ('pending','claimed','in_review','paused')
   GROUP BY story_id HAVING COUNT(*) > 1;
   ```
   Continues to return zero rows for at least 24 hours — confirms the poller is still calling `/complete` on the correct row (would not be true if poller were missing `?repo=` and landing on the wrong row).

---

## 7. Validation Against Dispatch ACs

| AC (from seed.md) | How spec addresses it |
|---|---|
| AC1 schema migration + reversible down | §1 — migration 007 with idempotent create + documented one-way down |
| AC2 cross-repo both succeed | §2.5 enqueue() — two different repos produce two rows |
| AC3 same-repo active duplicate → 409 | §2.5 — DuplicateDispatchError still raised; message includes repo |
| AC4 terminal isolation | §2.5 — DELETE scoped to (story_id, repo) |
| AC5 history preservation | §2.5 + T13 test; terminal rows in other repos never touched |
| AC6 service backward-compat (optional repo) | §2.3 + §2.4 — `_resolve_row()` single-row fallback |
| AC7 route `?repo=` | §3.1 + §3.2 |
| AC8 enqueue delete scoped to (story_id, repo) | §2.5 |
| AC9 tests | §5 — 18 tests in new file |
| AC10 migration reversibility | §1 — down path documented, one-way hazard noted |
| AC11 deployment validation | §6 — smoke test re-enqueues STORY-999 |
| AC12 docs | §2 + §3 — docstrings and module-level comments updated |

---

## 8. Follow-ups (deferred)

- `GET /dispatch/history?repo=X` filter parameter (useful; not required for the fix).
- Morris fleet-vigilance skills passing `?repo=` on `/reclaim` and `/priority` (opportunistic).
- `WorkHistoryPanel` frontend grouping by `(story_id, repo)` (separate story).
- monday.com correlation audit (verify dispatch → monday bridge doesn't collapse rows).
- Data-repair story to restore any history that Loki/git commit log can reconstruct for STORY-427, 443, 495, 527 (out of scope — this story is structural prevention only).

---

## 9. Recommended Next Phase

**Phase 7 (Test Design)** — produce `test-design.md` with:
- Concrete test fixtures (two test repos, multi-repo same-story_id scenarios)
- Assertions per test (status codes, SQL state, Pydantic response shapes)
- Ordering: all 18 tests as RED state before Phase 8.
