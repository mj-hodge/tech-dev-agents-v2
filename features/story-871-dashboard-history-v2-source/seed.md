# STORY-871 — Dashboard History tab reads from v2 terminal lane

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | routine |
| Feature Name | dashboard_history_v2 |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | true (Playwright required) |
| Epic | EPIC-Queue-v2.1 (queue trust) |
| Hard Dep | PRs #301 + #302 (already merged 2026-05-04) — `accepted` event correctly transitions v2 jobs to `state=completed, lane=terminal` |

## Problem Statement

The dashboard's History tab queries `GET /api/dispatch/history`, which reads from v1 `dispatch_items` (status IN completed/cancelled/failed). The v2 dispatch poller never writes terminal state to `dispatch_items` — completed v2 jobs land in `dispatch_state_current` with `state='completed', lane='terminal'`. As a result, **the History tab stopped showing wins after 2026-05-02** even though agents have shipped 22+ PRs since.

Today's reconciler proved the data is there: 19 v2 completions already sit in the terminal lane, invisible to the dashboard. Until the History tab reads from v2, every successful run looks like a non-event.

## Target User / Use Case

**Primary user:** Mark, checking the dashboard for "did anything ship today?" — currently sees a blank or stale History tab and has to cross-reference GitHub PRs manually.
**Secondary user:** Morris, validating that completions Morris merged are properly reflected in the dispatch system.

## Success Criteria

1. **SC-1** — `GET /api/dispatch/v2/history?limit=&offset=&status=` returns paginated terminal v2 jobs in the same shape as `DispatchHistoryResponse` (so the existing frontend types work unchanged).
2. **SC-2** — State mapping in the response: v2 `completed` → `"completed"`, v2 `cancelled` or `dead_letter` → `"cancelled"`, v2 `failed` → `"failed"`.
3. **SC-3** — Each row populates `pr_number`, `enqueued_by`, `repo`, `story_id`, `title`, `scope` from `dispatch_jobs`; `claimed_by` from the most-recent `leased_by` in the event log; `completed_at` / `cancelled_at` from the terminal event's `created_at` timestamp.
4. **SC-4** — `?status=completed|cancelled|failed|dead_letter` filters correctly. Default (no filter) returns all terminal states.
5. **SC-5** — Pagination works: `limit`, `offset`, `total` returned correctly. `limit` capped at 200; `offset` defaults to 0.
6. **SC-6** — Frontend `useDispatchHistory` hook calls `/api/dispatch/v2/history` instead of `/api/dispatch/history`. Existing pagination + status filter + History tab rendering work unchanged.
7. **SC-7** — Response includes both v2 terminal rows and v1 history rows whose `updated_at < <v2_cutover_ts>`. The cutover timestamp is read from a config value (`V2_HISTORY_CUTOVER_TS`, ISO-8601, default `'2026-05-03T00:00:00Z'`). Older completions (≤ May 2) remain visible without requiring a separate query.
8. **SC-8** — The 19 v2 completions present in the terminal lane today appear in the History tab after deploy, ordered by `completed_at DESC` with PR numbers visible.
9. **SC-9** — Playwright test: open the History tab, assert at least one row with `status=completed` and a non-empty PR cell renders without "NaN" or empty timestamps. Screenshot attached to PR.
10. **SC-10** — Zero regressions: existing dispatch v2 tests + frontend snapshot tests pass.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/ops_console/test_dispatch_v2_history.py::test_returns_history_response_shape -v` | PASSED |
| SC-2 | `pytest tests/ops_console/test_dispatch_v2_history.py::test_state_mapping -v` | PASSED — completed/cancelled/failed/dead_letter all map correctly |
| SC-3 | `pytest tests/ops_console/test_dispatch_v2_history.py::test_field_population -v` | PASSED — pr_number, claimed_by, completed_at all populated |
| SC-4 | `pytest tests/ops_console/test_dispatch_v2_history.py::test_status_filter -v` | PASSED — each status filter returns only matching rows |
| SC-5 | `pytest tests/ops_console/test_dispatch_v2_history.py::test_pagination -v` | PASSED — limit/offset/total correct; limit capped at 200 |
| SC-6 | `pnpm --filter frontend test src/hooks/useDispatchQueue.test.ts` | PASSED — hook calls v2 path |
| SC-7 | `pytest tests/ops_console/test_dispatch_v2_history.py::test_v1_v2_union_at_cutover -v` | PASSED — pre-cutover v1 + post-cutover v2, no overlap |
| SC-8 | `curl -H "X-API-Key: $KEY" "$URL/api/dispatch/v2/history?status=completed&limit=25" \| jq '.items \| length'` | ≥ 19 |
| SC-9 | `pnpm --filter frontend playwright test e2e/dispatch-history-v2.spec.ts` | PASSED, screenshot saved |
| SC-10 | `pytest tests/ -q --ignore=tests/e2e && pnpm --filter frontend test` | All green, zero regressions |

## Test Criteria (Phase 7 must include)

- **Empty case** — terminal lane has zero rows. `GET /api/dispatch/v2/history` returns `{items: [], total: 0, limit, offset}`.
- **State mapping** — fixture rows for each terminal state (completed, cancelled, dead_letter, failed); response maps each correctly.
- **Field population** — fixture with `pr_number=311`, `enqueued_by='dan'`, `leased_by='derrick'`, terminal event timestamp; response surfaces all of those.
- **Status filter** — 3 fixtures (one each: completed, failed, cancelled). `?status=completed` returns 1; `?status=failed` returns 1; no filter returns all 3.
- **Pagination** — 25 fixture rows; `?limit=10&offset=10` returns rows 11–20, `total=25`.
- **Limit cap** — `?limit=500` is silently capped at 200.
- **v1 union** — fixture with v1 row dated 2026-05-02 + v2 row dated 2026-05-04; default response includes both, ordered by their respective updated_at DESC. v1 row dated 2026-05-04 (post-cutover) is excluded.
- **claimed_by derivation** — multi-event fixture (leased→submitted→accepted with different actors); claimed_by reflects the agent who actually held the lease (most-recent `leased_by` from event log), not the manager who emitted accepted.
- **Frontend hook test** — assert `useDispatchHistory` issues a GET to `/api/dispatch/v2/history` (mock `api.get`).
- **Playwright test** — load `/`, click History tab, assert ≥1 completed row with non-empty PR number and timestamp.

## Validation

| Step | Command | Pass |
|------|---------|------|
| 1 | Phase 7 tests RED before implementation | All listed tests fail meaningfully (404 / undefined hook URL / etc.) |
| 2 | Phase 8 backend tests GREEN | All `tests/ops_console/test_dispatch_v2_history.py` pass |
| 3 | Phase 8 frontend tests GREEN | `useDispatchHistory.test.ts` + Playwright spec pass |
| 4 | Manual probe against live ops-console | `curl .../api/dispatch/v2/history?status=completed` returns ≥19 rows after deploy |
| 5 | Dashboard screenshot attached to PR | History tab shows today's completions with PRs visible |
| 6 | `pytest tests/ -q --ignore=tests/e2e` | Zero regressions |

## Acceptance Criteria

- [ ] AC-1: New route `GET /api/dispatch/v2/history` registered under `/api/dispatch/v2/` prefix; query params `limit: int = 50` (capped at 200), `offset: int = 0`, `status: str | None` (regex `^(completed|cancelled|failed|dead_letter)$`).
- [ ] AC-2: Service method `DispatchV2Service.list_history(*, limit, offset, status_filter)` reads from `dispatch_state_current` JOIN `dispatch_jobs` WHERE `lane='terminal'`, ordered by `updated_at DESC`. Optional WHERE `state=$status_filter`.
- [ ] AC-3: For each row, derive `claimed_by` by querying `dispatch_v2_events` for the most-recent `leased` event and reading `event_data->>actor` (or fall back to `dispatch_state_current.leased_by` if event is gone).
- [ ] AC-4: Derive `completed_at` / `cancelled_at` from the terminal event's `created_at` timestamp (the `accepted` / `cancelled` / `dead_lettered` event row in `dispatch_v2_events`).
- [ ] AC-5: Response shape matches `DispatchHistoryResponse`: `{items: [DispatchItem], total: int, limit: int, offset: int, fetched_at: ISO-8601}`. Each `DispatchItem` has the existing v1 fields populated.
- [ ] AC-6: Endpoint unions with v1 `dispatch_items` rows where `updated_at < V2_HISTORY_CUTOVER_TS` (read from env, default `'2026-05-03T00:00:00Z'`). Both sources contribute to `total`. Pagination operates on the unified result.
- [ ] AC-7: Frontend `useDispatchHistory` hook in `frontend/src/hooks/useDispatchQueue.ts` updates its URL from `/api/dispatch/history` to `/api/dispatch/v2/history`. No other call sites changed.
- [ ] AC-8: Backend test file `tests/ops_console/test_dispatch_v2_history.py` covers the 9 cases listed in Test Criteria.
- [ ] AC-9: Frontend test `frontend/src/hooks/__tests__/useDispatchQueue.test.ts` (or extension of existing) asserts the v2 URL is called.
- [ ] AC-10: Playwright spec `frontend/e2e/dispatch-history-v2.spec.ts` opens the History tab, asserts ≥1 completed row, captures a screenshot.
- [ ] AC-11: Logging — endpoint logs `[V2-HISTORY] limit=N offset=M status=X total=T elapsed=Eᵐˢ` at INFO. Service logs WARN if v1+v2 union takes > 500ms (perf canary).
- [ ] AC-12: Error AC — if v1 dispatch_items query fails (table missing, etc.), the endpoint MUST still return v2 rows (degrades gracefully). Failure mode logged at ERROR with traceback. Never returns 5xx solely because v1 history is unavailable.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — 1 service method + 1 route + 1 frontend hook line + tests |
| Timeline | half-day; ship before Mark loses confidence in completed counts |
| Tech | Python 3.12 + FastAPI + asyncpg (existing stack); React + tanstack-query + Playwright |
| Schema | no changes — read-only over existing tables |
| Auth | inherits `require_auth` from the v2 router (any authenticated session/key) |

## Performance Requirements
| Metric | Target |
|--------|--------|
| API response time (p95) | < 200ms for limit=50 |
| Database queries per request | ≤ 3 (v2 select + v1 select + count) |
| Bundle size delta | 0 KB (URL change only) |

## Security Constraints
- [ ] Endpoint inherits existing `require_auth` — no new auth path invented.
- [ ] No user input concatenated into SQL — use parameterized queries throughout.
- [ ] Status query param validated with regex pattern (FastAPI `Query(pattern=...)`).
- [ ] No PR numbers, story IDs, or agent names treated as secrets — current behavior preserved.
- [ ] No new logs surface secrets; `[V2-HISTORY]` line shows counts only.

## Operational Lifecycle
- **Configuration:** `V2_HISTORY_CUTOVER_TS` env var (default `'2026-05-03T00:00:00Z'`). Tunable without redeploy if the cutover date moves.
- **Monitoring:** Loki picks up `[V2-HISTORY]` log line; spike in `elapsed > 500ms` warnings indicates union path is slow (probably needs an index).
- **Rollback:** If v2 history endpoint misbehaves, revert frontend hook URL to `/api/dispatch/history` (one-line revert) — backend stays.
- **Sunset of v1 history endpoint:** out of scope for this story; tracked as future work once the cutover backfill is solid.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Read terminal lane via the existing `dispatch_state_current` join (don't invent a new table) | Whether to materialize a view for performance if the union gets slow | Modify v1 dispatch_items schema or its history endpoint |
| Match `DispatchHistoryResponse` shape exactly so the frontend types work unchanged | Whether to deprecate `/api/dispatch/history` in this story | Auto-cancel or auto-mutate v2 rows from this endpoint (read-only) |
| Use parameterized queries for all WHERE clauses | Whether the cutover should be per-row (each row decides v1 vs v2) instead of timestamp-based | Bypass `require_auth` |
| Cap `limit` at 200 to match v1 behavior | Whether `claimed_by` should aggregate across multiple lease cycles | Trigger writes to dispatch_jobs / dispatch_state_current |
| Update `frontend/src/hooks/useDispatchQueue.ts` URL only — no broader frontend refactor | Whether to also expose `lineage` data on history rows (separate concern) | Touch the v2 poller, the deploy pipeline, or any other route |
| Include Playwright test + screenshot for the frontend change | Whether to backport the union to v1's `/history` endpoint instead | Skip the Playwright spec (memory: frontend stories require Playwright) |

## Files to Modify

- `tech_dev_agents/ops_console/routes/dispatch_v2.py` — add `@router.get("/history")` handler and `HistoryQuery` model.
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py` — add `list_history(*, limit, offset, status_filter)` method.
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — add a small helper for the v1 pre-cutover read (or reuse `history()` with a date filter).
- `frontend/src/hooks/useDispatchQueue.ts` — change `useDispatchHistory` URL from `/api/dispatch/history` to `/api/dispatch/v2/history`.
- `tests/ops_console/test_dispatch_v2_history.py` — **new**, ≥9 backend tests.
- `frontend/src/hooks/__tests__/useDispatchQueue.test.ts` — extend or **new**, assert v2 URL.
- `frontend/e2e/dispatch-history-v2.spec.ts` — **new** Playwright spec.
- `features/story-871-dashboard-history-v2-source/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md`, `development-tasks.md` — tracking updates.

## Files to NOT Modify

- `tech_dev_agents/ops_console/routes/dispatch.py` — v1 `/history` endpoint stays exactly as it is. No deprecation, no shape changes.
- `dispatch_items` schema — read-only access; no migrations.
- `dispatch_jobs` / `dispatch_state_current` / `dispatch_v2_events` schemas — read-only access.
- `deployment/hermes/dispatch_poller_v2.py` — out of scope; agents already write the right events.
- Other frontend components (DispatchQueue.tsx layout, NeedsInfoAnswerModal, etc.) — single-line hook URL change only.

## Out of Scope

- **Auto-emit `accepted` event when a PR merges on GitHub.** Tracked as a separate story in EPIC-Queue-v2.1. Without it, completions only land in History when someone calls `/review-outcome` (today's reconciler proved that path works manually).
- Sunset of v1 `/api/dispatch/history` endpoint — separate cleanup, not blocking.
- Dashboard-side filters (date range, agent, repo) beyond status — current History tab doesn't have them; not adding here.
- Real-time push (WebSocket) of completions — polling stays.
- Cross-linking from History row → lineage view — separate UX story.

## Decisions to Make in Phase 7/8 (Small scope, no Phase 4)

1. **`claimed_by` derivation source.** Option A: most-recent `leased` event's actor. Option B: `dispatch_state_current.leased_by` (last claim before termination). Lean A — accurate if a story changed hands.
2. **v1 union mechanism.** Two queries + Python merge, vs one SQL UNION? Two queries is simpler and v1 will likely be empty soon. Default: two queries.
3. **Cutover timestamp default.** `'2026-05-03T00:00:00Z'` matches when v2 became authoritative for dispatch. Confirm in Phase 7 by spot-checking that no v1 row has `status='completed'` after that.

## Done Looks Like

```bash
$ curl -sS -H "X-API-Key: $KEY" \
    "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/history?status=completed&limit=5" \
  | jq '{total, items: [.items[] | {story_id, repo, pr_number, completed_at, claimed_by}]}'
{
  "total": 19,
  "items": [
    {
      "story_id": "STORY-094",
      "repo": "advertising-amazon",
      "pr_number": 315,
      "completed_at": "2026-05-04T20:33:08.412Z",
      "claimed_by": "dan"
    },
    {
      "story_id": "STORY-807",
      "repo": "tech-gc-knowledgebase",
      "pr_number": 64,
      "completed_at": "2026-05-04T20:33:05.118Z",
      "claimed_by": "dan"
    },
    {
      "story_id": "STORY-640",
      "repo": "advertising-amazon",
      "pr_number": 319,
      "completed_at": "2026-05-04T20:33:02.901Z",
      "claimed_by": "dan"
    },
    {
      "story_id": "STORY-831",
      "repo": "tech-dev-agents",
      "pr_number": 261,
      "completed_at": "2026-05-04T20:32:59.640Z",
      "claimed_by": "dan"
    },
    {
      "story_id": "STORY-830",
      "repo": "advertising-amazon",
      "pr_number": 330,
      "completed_at": "2026-05-04T20:32:56.180Z",
      "claimed_by": "dan"
    }
  ]
}
```

Plus: open the dashboard, click the **History** tab, and the same five rows render with PR badges and human-readable timestamps (no "NaN ago", no empty cells).

## Escalation Contract

Default contract — escalate to Mark via Teams DM and stop work if any of these conditions appear:

- Migration or schema change required (this story explicitly forbids both — escalate before introducing).
- Auth pattern needs to differ from existing v2 router's `require_auth` (e.g., new role check).
- v1 `/api/dispatch/history` endpoint must change shape or behavior (out of scope; surfaces a real conflict).
- Frontend change touches anything beyond `useDispatchQueue.ts`'s `useDispatchHistory` hook URL.
- Performance constraint (p95 < 200ms) cannot be met without adding an index or materialized view.
- Playwright spec cannot be authored because the dev frontend can't load (escalate fast — likely a deeper environment issue).

Otherwise: proceed autonomously through Phase 7 → Phase 8 → PR.

---

## Suggested Dispatch

**Dan** — full context on dashboard work (STORY-857/858/859) and just shipped queue-trust fixes today. Estimated half-day.
