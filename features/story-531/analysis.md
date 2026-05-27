# STORY-531: Analysis — Composite `(story_id, repo)` Unique Key

## Analysis Summary

Three approaches were evaluated across Technical (40%), Business Value (30%), and Risk/Effort (30%) dimensions. Sub-agents independently analysed codebase call sites, effort, and deployment risks.

---

## Call-Site Audit

### Service Methods Querying by `story_id` Alone

All 11 service methods in `dispatch_db_service.py` query by `story_id` alone:

| Method | Line(s) | Operation |
|--------|---------|-----------|
| `enqueue()` | 104, 116 | SELECT existing + DELETE terminal ← **root cause** |
| `set_priority()` | 209, 221 | SELECT + UPDATE |
| `claim()` | 250, 260–263 | UPDATE + exists check |
| `force_claim()` | 287, 297 | SELECT + UPDATE |
| `pause()` | 335, 366 | SELECT + UPDATE |
| `cancel()` | 389–400 | UPDATE + exists check |
| `get()` | 407–410 | SELECT |
| `complete()` | 437, 455, 460 | UPDATE + exists checks |
| `fail()` | 523–531, 546–548 | SELECT + UPDATE |
| `transition_to_review()` | 476, 488 | UPDATE + exists check |
| `recover_stale_claims()` | 623–633 | UPDATE (no story_id — fleet-wide; OK) |

**11 methods need an optional `repo=` qualifier.**

### Route Handlers with `{story_id}` Path Param

All 7 routes in `dispatch.py` pass `story_id` to the service without `repo`:

| Route | Line | Calls |
|-------|------|-------|
| `POST /dispatch/claim/{story_id}` | 318 | `db_svc.claim(story_id, ...)` |
| `DELETE /dispatch/queue/{story_id}` | 373 | `db_svc.cancel(story_id)` |
| `POST /dispatch/complete/{story_id}` | 542 | `db_svc.complete(story_id, ...)` |
| `POST /dispatch/fail/{story_id}` | 697 | `db_svc.fail(story_id, ...)` |
| `POST /dispatch/review/{story_id}` | 759 | `db_svc.transition_to_review(story_id)` |
| `POST /dispatch/reclaim/{story_id}` | 916 | `db_svc.force_claim(story_id, ...)` |
| `POST /dispatch/pause/{story_id}` | 866 | `db_svc.pause(story_id, ...)` |

**7 routes need an optional `?repo=X` query parameter.**
`POST /dispatch/priority` (line 967) uses `body.story_id` not a path param — same treatment applies.

### External Callers

| Caller | API calls | repo available? |
|--------|-----------|-----------------|
| `deployment/hermes/dispatch_poller.py` | `/complete`, `/fail`, `/claim`, re-enqueue | `/complete` — **no** (not in function sig); `/fail` — **yes** (`repo` param at line 312, unused in URL); `/claim` — no need (only one active row per story+repo at time of claim) |
| Morris fleet-vigilance skills | `/queue`, `/history` read-only; `/reclaim` in edge cases | Read-only paths: no change needed. Reclaim: add `?repo=` as a follow-up |
| Dashboard `WorkHistoryPanel` | `GET /dispatch/history` | Read-only, no repo filter today — follow-up story |

**Key gap:** `_report_complete()` (dispatch_poller.py:234) has no `repo` parameter — it must gain one and pass it as `?repo=` on the URL. `_report_fail()` (line 305) already has `repo: str = ""` in its signature but doesn't use it on the URL. Both need a 1-line URL change plus the caller site thread-through at line 612.

---

## Approaches

### Approach A — Composite Partial Index + `AmbiguousStoryError` ✓ SELECTED

**What it is:**
1. Migration: `DROP INDEX uq_story_active_idx`; `CREATE UNIQUE INDEX uq_story_repo_active_idx ON dispatch_items (story_id, repo) WHERE status IN ('pending', 'claimed', 'in_review', 'paused')` (also closes the `in_review` constraint gap). Support index on `(story_id, repo)` for lookup performance.
2. Service: `AmbiguousStoryError(DispatchDBError)` added. All lookup methods gain `repo: str | None = None`. Shared helper `_resolve_single()` encapsulates: given `(story_id, repo)`, return the row or raise `NotFoundError` / `AmbiguousStoryError`.
3. Routes: `?repo=` optional `Query` param on all 7 `{story_id}` endpoints. `AmbiguousStoryError` → HTTP 409 with body `{"detail": "STORY-X exists in multiple active repos: [a, b] — add ?repo="}`.
4. `enqueue()` terminal-delete scoped to `(story_id, repo)` — preserves rows from other repos.
5. Poller update: `_report_complete()` gains `repo` param; both `_report_complete` and `_report_fail` thread `?repo=` on the URL.

**Technical score: 5/5** — Correct at schema level. AmbiguousStoryError forces callers to be explicit; silent failures are impossible.

**Business value: 5/5** — Fully solves both problems (history erasure + server-state confusion). Explicit 409 ensures broken callers are discovered, not silently misbehaving.

**Effort: 4/5** — 5 files: migration, `dispatch_db_service.py`, `dispatch.py`, `dispatch_poller.py`, `responses.py` (new error model). All changes are local and well-scoped. Poller change is 1 new param + 2 URL f-strings.

**Risk: mitigable** — Main risk is the poller calling `/complete` without `?repo=` during the deployment window between schema deploy and code deploy. Mitigation: deploy schema + code together; schema's new index is backward-compat until a second active (story_id) row actually appears (which can't happen while the old index is still enforced during the deploy window). See Deployment section below.

---

### Approach B — Composite Partial Index + Silent Latest-Active Fallback

**What it is:** Same migration as A; service picks `ORDER BY enqueued_at DESC LIMIT 1` when `repo=None` + multi-match instead of raising. Routes don't need `?repo=` changes for existing callers.

**Technical score: 2/5** — Masks ambiguity. "Most recently enqueued" is undefined behaviour when two different repos enqueue the same story_id within seconds. The complete/fail calls are now silently operating on an unintended row. Hard to observe in prod until a correctness failure surfaces.

**Business value: 4/5** — Fixes history erasure fully. Doesn't fully address the server-state confusion (operator still sees ambiguous queue; Morris can't distinguish repos without a repo field in responses).

**Recommendation: Rejected.** Zero-signal on correctness issues; masks caller bugs. Not appropriate for a queue that manages production code deployments.

---

### Approach C — Fix `enqueue()` DELETE Only

**What it is:** Scope the DELETE at `dispatch_db_service.py:116` to `WHERE story_id = $1 AND repo = $2`. No index change.

**Technical score: 1/5** — Does not prevent two simultaneous active rows for the same `story_id` across different repos. `next_pending()`, `list_queue()`, and the unique constraint still operate as if there's one globally unique active story per `story_id`. The queue can bifurcate.

**Business value: 2/5** — Fixes re-enqueue history erasure only. Leaves all server-state confusion intact.

**Recommendation: Rejected.** Incomplete fix; deferred debt.

---

## Scoring Matrix

| Dimension | Weight | Approach A | Approach B | Approach C |
|-----------|--------|-----------|-----------|-----------|
| Technical soundness | 40% | 5.0 | 2.0 | 1.0 |
| Business value | 30% | 5.0 | 4.0 | 2.0 |
| Risk / Effort | 30% | 3.5 | 4.5 | 5.0 |
| **Weighted total** | | **4.55** | **3.35** | **2.50** |

---

## Key Decisions Locked

| Question | Decision | Rationale |
|----------|----------|-----------|
| Multi-match without `repo=` | `AmbiguousStoryError` → HTTP 409 | Silent fallback hides correctness bugs in callers. Explicit 409 surfaces the problem. |
| `in_review` in composite index | Yes — include it | Closes the constraint gap from migration 004. In_review stories can now only have one active row per (story_id, repo). |
| Poller thread-through | `/complete` and `/fail` must send `?repo=` | Poller already has `repo` from the claimed item; 1-line URL change per call. |
| Terminal-delete scope | `(story_id, repo)` only | Rows from other repos must be preserved; this is the root cause of STORY-427/443/495/527 history loss. |
| Migration index name | `uq_story_repo_active_idx` | Distinguishes from old `uq_story_active_idx`; keeps DROP IF EXISTS + CREATE IF NOT EXISTS pattern idempotent. |
| `?repo=` absent + single match | Backward-compat pass-through | Existing callers with single active row per story_id work unchanged on day 1. |
| Down migration feasibility | One-way; document rollback hazard | Down migration fails if cross-repo active rows exist. Acceptable — document clearly in migration header. |

---

## Risk Register

| Risk | L | S | Mitigation |
|------|---|---|-----------|
| DROP INDEX lock window — concurrent INSERT during gap | M | M | Use `CREATE INDEX CONCURRENTLY` on new index before `DROP` old; wrap in BEGIN/COMMIT only for the final DROP + rename step |
| Poller calls `/complete` without `?repo=` during schema-only deploy window | L | M | Deploy schema + code atomically (same push-code.sh run). Old index still active until the deploy push-code restarts the poller with the new URL. |
| Down migration fails if cross-repo duplicates exist | M | H | Document as one-way. Down path requires data-cleanup step before index recreation; included as commented SQL in migration. |
| `in_review` gap closure adds new constraint | L | L | No existing data violates `(story_id, repo)` uniqueness in any status — `in_review` rows are per-story, per-agent. Checked via pre-flight SELECT. |
| Morris fleet skills call `/reclaim/{story_id}` without repo | L | L | Multi-match on reclaim defers to the queue ordering logic; these are rare ops-correction calls. Add `?repo=` as follow-up. |
| Dashboard WorkHistoryPanel renders first cross-repo match | L | L | History endpoint unchanged; `repo` field is already in the response schema (it was always in the DB). Front-end grouping by (story_id, repo) is a follow-up. |

---

## Deployment Order

1. **Pre-flight query** on prod DB: `SELECT story_id, COUNT(*) FROM dispatch_items WHERE status IN ('pending','claimed','in_review','paused') GROUP BY story_id HAVING COUNT(*) > 1;` — must return 0 rows (impossible under current index; confirms clean state).
2. **Deploy ops-console code** (migration + updated service/routes). `push-code.sh` copies files + restarts poller.
3. Migration runs at startup via the ops-console migration runner (existing pattern: `scripts/migrations/` applied in order).
4. **Migration `007` execution**: `CREATE UNIQUE INDEX CONCURRENTLY` (safe under load) then DROP old index in a transaction.
5. **Poller restarts** with new code — `/complete` and `/fail` now send `?repo=` — no window of ambiguity because the old index prevented any cross-repo active rows from existing before the migration.
6. **Post-deploy smoke test**: enqueue `STORY-999` for `repo-alpha` and `repo-beta` simultaneously; confirm both succeed (201 each); confirm `?repo=` routes work.

---

## Follow-ups (out of scope for STORY-531)

- **WorkHistoryPanel** grouping by `(story_id, repo)` in history view — separate frontend story.
- **Morris reclaim + priority skills** threaded with `?repo=` — opportunistic, non-blocking.
- **`GET /dispatch/history` repo filter param** — useful for cross-repo audit; separate story.
- **monday.com correlation audit** — dispatch-to-monday bridge uses story_id; verify it doesn't collapse cross-repo rows. Low priority (monday uses its own surrogate GID).

---

## Recommendation

**Implement Approach A.** The composite `(story_id, repo)` partial index + `AmbiguousStoryError` is the architecturally correct solution. All risks are mitigable within the scope of this story. The poller change is 2 trivial function-signature additions. The total blast radius is 5 files with no structural refactors required.

**Next phase:** Phase 6 (Design) — produce `feature-spec.md` locking the full API surface, migration SQL, service signatures, and test matrix.
