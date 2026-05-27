# STORY-531: Composite Unique Key `(story_id, repo)` on `dispatch_items`

## Problem Statement

`dispatch_items` has a single-column active-state unique index on `story_id`:

```sql
-- scripts/migrations/004_paused_status.sql (current shape)
CREATE UNIQUE INDEX uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed', 'paused');
```

…and `DispatchDBService.enqueue()` (`tech_dev_agents/ops_console/services/dispatch_db_service.py:104-118`) looks up and deletes prior terminal-state rows **by `story_id` alone**:

```python
existing = await conn.fetchrow(
    "SELECT status FROM dispatch_items WHERE story_id = $1", story_id,
)
if existing:
    if status in ("pending", "claimed", "in_review", "paused"):
        raise DuplicateDispatchError(...)
    # Terminal state — remove old record so we can re-enqueue
    await conn.execute("DELETE FROM dispatch_items WHERE story_id = $1", story_id)
```

This creates two defects now that the fleet dispatches the **same semantic story ID across different repos** (STORY-N numbers restart per repo in Mark's workflow):

1. **History is erased on cross-repo reuse.** When Mark dispatches `STORY-527` for `advertising-amazon`, the enqueue path first `DELETE`s the old `STORY-527` row from `tech-dev-agents` (which had completed as the ccusage quota work). The prior record — commit SHA, PR #, elapsed time, enqueued-by, all audit data — is wiped. Four historical collisions are already known: **STORY-427, STORY-443, STORY-495, STORY-527** (Mark 2026-04-22). Once wiped, we cannot reconstruct Morris's tool-use audit, Loki quota attribution, or dashboard completion timelines for those stories.
2. **Morris & the dashboard see an ambiguous server state.** `/api/dispatch/queue`, `/api/dispatch/history`, and the completion event stream all key off `story_id` alone. When two different stories share an ID across repos, Morris's fleet-vigilance skill cannot tell whether "STORY-527 is active" refers to the current advertising-amazon dispatch or to the tech-dev-agents ccusage record. Operator views show a single row where two semantically distinct units of work existed.

**Fix:** make `(story_id, repo)` the composite unique key on the active-state partial index, and teach `enqueue()` / `claim()` / `complete()` / `fail()` / `cancel()` / `get()` to disambiguate by the pair. Same `story_id` can coexist across repos; the same `(story_id, repo)` pair can have at most one active row at a time; terminal rows are preserved forever (delete-on-re-enqueue only matches within the same repo).

## Target User

- **Mark** (engineering manager) — re-uses `STORY-N` IDs across repos deliberately; today this silently deletes audit history.
- **Morris** (fleet-vigilance agent) — consumes `/api/dispatch/queue` and `/api/dispatch/history`; needs `(story_id, repo)` disambiguation to reason about server state.
- **Agent dispatch poller** (`deployment/hermes/dispatch_poller.py`) — claims stories by `story_id`; when the queue has two rows for the same ID across repos the poller must target the one matching its repo.
- **Dashboard `WorkHistoryPanel`** — renders history rows; today collapses cross-repo same-ID stories into one, hiding the earlier row.

## Acceptance Criteria

- [ ] **AC1 (schema):** `scripts/migrations/007_composite_key_story_repo.sql` drops the old `uq_story_active_idx` (on `story_id` alone) and creates a new partial unique index on `(story_id, repo)` covering active states `('pending', 'claimed', 'in_review', 'paused')`. Migration is idempotent (`IF EXISTS` / `IF NOT EXISTS`) and includes a reversible `-- DOWN` block as a comment.
- [ ] **AC2 (cross-repo coexistence):** Two enqueues with the same `story_id` but different `repo` values both succeed (HTTP 201 each). Both rows remain distinct in `dispatch_items`.
- [ ] **AC3 (same-repo duplicate blocked):** Two enqueues with the same `(story_id, repo)` pair while the first is active (`pending`/`claimed`/`in_review`/`paused`) → second enqueue returns HTTP 409 `DuplicateDispatchError`.
- [ ] **AC4 (terminal state isolation):** `complete` / `fail` / `cancel` on `(STORY-X, repo-A)` does NOT affect a row with `(STORY-X, repo-B)` in any state. Verified by an integration test that enqueues the same story into two repos, completes one, and asserts the other's row is unchanged.
- [ ] **AC5 (history preserved):** Re-enqueuing `STORY-X` in `repo-A` after its prior `repo-A` row reached a terminal state → new row created, **prior terminal row preserved for history** (dispatched via FIFO, prior row retains commit_sha / pr_number / completed_at). Terminal rows in `repo-B` are never touched.
- [ ] **AC6 (service API backward-compat):** `DispatchDBService.enqueue()`, `.claim()`, `.complete()`, `.fail()`, `.cancel()`, `.get()`, `.set_priority()`, `.force_claim()`, `.pause()`, `.transition_to_review()` accept an **optional `repo` qualifier**. When `repo` is omitted and exactly one row matches `story_id`, behaviour is identical to today (backward-compat with `dispatch_poller.py` and existing tests). When `repo` is omitted and **multiple** rows match, the service raises `AmbiguousStoryError` — callers must disambiguate.
- [ ] **AC7 (route API):** `POST /api/dispatch/claim/{story_id}`, `POST /api/dispatch/complete/{story_id}`, `POST /api/dispatch/fail/{story_id}`, `DELETE /api/dispatch/queue/{story_id}`, `POST /api/dispatch/pause/{story_id}`, `POST /api/dispatch/reclaim/{story_id}`, `POST /api/dispatch/review/{story_id}` accept an optional `?repo=X` query parameter. When absent and unambiguous → today's behaviour. When absent and ambiguous → HTTP 409 with `detail` listing the candidate repos. When present → scope the operation to that `(story_id, repo)` pair.
- [ ] **AC8 (enqueue delete-on-terminal scoped to repo):** The `enqueue()` "terminal-state delete before re-enqueue" step matches on `(story_id, repo)`, not `story_id` alone. A terminal row in `repo-B` is never deleted when re-enqueuing `repo-A`.
- [ ] **AC9 (tests):** `tests/ops_console/test_dispatch_composite_key.py` covers AC2–AC8 with PostgreSQL integration tests (using the existing `conftest.py` fixture). Uses test repos `repo-alpha` / `repo-beta` to avoid touching real dispatches.
- [ ] **AC10 (reversibility):** Migration is safe to roll back on a prod copy. Down path: `DROP INDEX uq_story_repo_active_idx; CREATE UNIQUE INDEX uq_story_active_idx ON dispatch_items (story_id) WHERE status IN (...)`. A down migration that succeeds on data that has NO cross-repo `story_id` collisions is sufficient; it may fail if cross-repo duplicates exist (documented risk).
- [ ] **AC11 (deployment validation):** Against a copy of production DB, the four known collisions (427, 443, 495, 527) — once the migration is in place — can be re-enqueued across repos with full history preserved. Documented in `feature-spec.md` validation section.
- [ ] **AC12 (docs):** `tech_dev_agents/ops_console/services/dispatch_db_service.py` module docstring is updated to reflect `(story_id, repo)` as the logical key. Route docstrings on `dispatch.py` note the optional `?repo=` param.

## Scope Classification

**Medium** (per SDLC dispatch note).

Rationale:
- Single DB migration (partial index swap) — no data backfill, no large table rewrite.
- Service-layer changes to one file (`dispatch_db_service.py`) with backward-compat fallback for the single-row case — no caller migrations required on day 1.
- Route-layer changes: add one optional query param across ~7 endpoints.
- No new auth surface, no new service, no new external contract with the agent VMs.
- Blast radius is bounded to the ops-console DB layer. The dispatch poller, Morris skills, and frontend will adopt the `?repo=` param opportunistically; none break on day 1.
- Medium phase path: `1 → 4 → 6 → 7 → 8 → Done`. Deliverables required: `seed.md` ✱, `analysis.md`, `feature-spec.md`, `test-design.md`.

## Technical Notes

### Current state (2026-04-22)

- **Migrations touching `story_id` uniqueness:**
  - `001_dispatch_queue.sql` — original partial unique index on `(story_id)` where status ∈ {pending, claimed}.
  - `004_paused_status.sql` — drops + recreates that index to include `paused`.
  - (`sql/002_in_review_status.sql` adds `in_review` to the CHECK but does **not** add it to the unique index — a latent gap that the new composite index will also close.)
- **Existing `repo` column:** already `NOT NULL` on the table (from 001), populated on every insert from `DispatchRequest.repo`. Perfect — no data backfill needed; the column is guaranteed non-null historically.
- **Service methods that query by `story_id` alone** (will need a `repo` qualifier option): `enqueue`, `claim`, `force_claim`, `cancel`, `get`, `complete`, `fail`, `transition_to_review`, `pause`, `set_priority`.
- **Route endpoints with `{story_id}` path param** (will need an optional `?repo=` query param): `POST /dispatch/claim/{id}`, `POST /dispatch/complete/{id}`, `POST /dispatch/fail/{id}`, `DELETE /dispatch/queue/{id}`, `POST /dispatch/pause/{id}`, `POST /dispatch/reclaim/{id}`, `POST /dispatch/review/{id}`.
- **Callers of the service that might pass a `repo`:** `dispatch_poller.py` already knows the repo of the story it just claimed — so it can thread the qualifier through on `/complete` and `/fail` once the route accepts it. Morris's fleet skills can adopt the qualifier where they currently rely on `story_id` alone.

### Proposed direction (to be confirmed in Phase 4 / locked in Phase 6)

**Migration (`scripts/migrations/007_composite_key_story_repo.sql`):**

```sql
BEGIN;

-- Drop the single-column active index
DROP INDEX IF EXISTS uq_story_active_idx;

-- Create the composite active index over ALL active states
-- (closes the in_review latent gap at the same time)
CREATE UNIQUE INDEX IF NOT EXISTS uq_story_repo_active_idx
    ON dispatch_items (story_id, repo)
    WHERE status IN ('pending', 'claimed', 'in_review', 'paused');

-- Supporting index for (story_id, repo) lookups in service layer
CREATE INDEX IF NOT EXISTS idx_dispatch_story_repo
    ON dispatch_items (story_id, repo);

COMMIT;

-- DOWN (manual rollback):
-- BEGIN;
-- DROP INDEX IF EXISTS uq_story_repo_active_idx;
-- DROP INDEX IF EXISTS idx_dispatch_story_repo;
-- CREATE UNIQUE INDEX uq_story_active_idx
--     ON dispatch_items (story_id)
--     WHERE status IN ('pending', 'claimed', 'paused');
-- -- NOTE: down-migration fails if cross-repo duplicates exist in active state.
-- COMMIT;
```

**Service layer** — add an `AmbiguousStoryError(DispatchDBError)` class. Each lookup method gets:

```python
async def claim(self, story_id: str, agent_name: str, *, repo: str | None = None) -> dict[str, Any]:
    ...
```

Resolution rule (applied in a single helper `_resolve_pair()`):
- If `repo` provided → operate on `(story_id, repo)` exactly.
- If `repo` omitted → query for all rows with `story_id`. If 0 → `NotFoundError`. If 1 → proceed (backward-compat). If >1 → `AmbiguousStoryError` with the candidate repos.

**Route layer** — each `{story_id}` endpoint gets an optional `repo: str | None = Query(None)`. `AmbiguousStoryError` → HTTP 409 `{ "detail": "STORY-X exists in multiple repos: [repo-a, repo-b] — specify ?repo=" }`.

### Risk / blast radius

- **Risk: dispatch poller breakage.** The poller today calls `/complete/{story_id}` without a repo. If two active stories with the same ID exist, the call becomes ambiguous. Mitigation: the poller knows the repo of the story it claimed from `/dispatch/next` and will thread it through `/complete` and `/fail`. Implementation detail for Phase 8.
- **Risk: monday.com sync.** `monday_service` uses `story_id` as a correlation key. Out of scope for this story — monday.com already uses a separate surrogate ID (item GID). Verify in Phase 4 that the dispatch→monday bridge doesn't collapse rows.
- **Risk: migration ordering on live prod.** The drop+create is within a single `BEGIN/COMMIT` — no window where the table is un-indexed. On a busy ops-console (low traffic, single-digit inserts/minute), this is a sub-second operation.
- **Risk: existing rows violate the new constraint.** The active-state partial index will only fail on rows that currently hold two ACTIVE `(story_id, repo)` pairs — impossible under the current single-column unique index. Terminal rows are exempt from the partial index, so they never conflict. Migration is safe to apply without data cleanup.
- **Risk: over-matching by Morris or the dashboard.** Anywhere that currently queries `WHERE story_id = $1` and assumes a single row will now potentially see multiple. Phase 4 must enumerate these call sites; Phase 6 decides whether each site needs repo-scoping or a "latest" rule.

## Dependencies

- **Existing:** `dispatch_items` table, `DispatchDBService`, `/api/dispatch/*` routes, `conftest.py` PG test fixture, `scripts/migrations/` runner.
- **No new Python packages. No new secrets. No new external services.**
- **Upstream callers that read `story_id` without `repo`:** `deployment/hermes/dispatch_poller.py`, `frontend/src/components/WorkHistoryPanel.tsx`, Morris skills (`deployment/vm/skills/fleet-vigilance/*`), `monday_service.py`. Phase 4 enumerates; Phase 8 updates where needed (opportunistic — none blocks this story's MVP).

## Out of Scope

- Backfilling / de-duping the four known historical collisions (427, 443, 495, 527) — validation only. A separate data-repair story covers any restoration work if required.
- Migrating dispatch poller, frontend, or Morris skills to always send `?repo=`. Phase 8 includes poller updates only where correctness requires it; frontend / Morris adoption is follow-up.
- Primary-key change. We keep `id BIGSERIAL PRIMARY KEY`; `(story_id, repo)` is a *logical* composite key enforced by the partial unique index plus service-layer resolution.
- Changing the `monday.com` correlation strategy.
- Adding a `repo` filter to `/api/dispatch/history` pagination (useful but separable; file as follow-up).

## Recommended Next Phase

**Phase 4 (Analysis)** — Medium scope. Analysis will:
1. Enumerate every call site that queries `dispatch_items` by `story_id` alone (service, routes, poller, Morris skills, frontend, monday bridge) and classify each as "must repo-scope", "latest-row rule is fine", or "no change needed".
2. Evaluate two resolution strategies for `repo=None`: (a) raise `AmbiguousStoryError` on multi-match (safer, breaks silent behaviour) vs. (b) return "most recent active" (backward-compat, but hides bugs). Lock the choice.
3. Confirm the migration is safe on a prod DB copy (no active-state cross-repo duplicates — sanity-check with a `SELECT` before migrating).
4. Decide whether to also tighten `WorkHistoryPanel` and `/api/dispatch/history` to group by `(story_id, repo)` now, or defer to a follow-up.

Then Phase 6 (`feature-spec.md`), Phase 7 (`test-design.md` + RED tests), Phase 8 (migration + service + routes + poller thread-through + PR).

## Test Criteria

Phase 7 produces `test-design.md` plus RED tests covering:
- Composite-key claim: claiming `(STORY-N, repo_A)` does not conflict with `(STORY-N, repo_B)`.
- Migration up-and-down cycle leaves the table in the original shape (reversibility).
- `/api/dispatch/history` returns one row per `(story_id, repo)` pair.
- Poller passes `repo` through all completion/fail/needs-info calls.

## Validation

After Phase 8 lands:
1. `python-tests` CI is GREEN; the new migration runs cleanly on a fresh integration DB.
2. A hand-crafted pair of dispatches with matching `story_id` but different `repo` both claim and complete independently against a live dev dispatch instance.
3. `WorkHistoryPanel` renders two distinct rows for the pair (visual check).
