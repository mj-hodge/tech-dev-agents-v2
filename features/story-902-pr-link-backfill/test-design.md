# Test Design — STORY-902: PR Link Backfill Sweeper

## Scope

Unit tests for the `_pr_link_backfill_tick` helper and the `dispatch_pr_link_backfill_sweeper`
background loop in `tech_dev_agents/ops_console/services/self_healing.py`.

All tests live in `tests/ops_console/test_dispatch_pr_link_backfill.py`.

No integration tests required (Small scope).

## Strategy

Unit tests with mocked DB pool and mocked GitHub REST API calls. The `_pr_link_backfill_tick` helper is dependency-injected (pool param) and the GitHub HTTP call is patched at the urllib level, matching the existing test patterns in this codebase.

---

## Test Groups

### Group A — Tick: happy path (T01)

| ID | Test | Expected | AC |
|----|------|----------|-----|
| A-01 / T01 | `test_happy_path_links_pr` | GitHub returns 1 PR; pool.execute called with UPDATE pr_number, INFO logged | AC-2, AC-4, AC-5 |
| A-02 | `test_branch_inferred_from_story_id` | STORY-885 -> branch prefix `story-885/` used in API URL | AC-3 |

Setup:
- pool.fetch returns 1 row: `{job_id: UUID, repo: "tech-dev-agents", story_id: "STORY-885"}`
- gh_fetch_fn returns `[{"number": 42, "created_at": "2026-05-01T12:00:00Z"}]`

Expected:
- pool.execute called once with UPDATE setting pr_number=42
- Logger emits INFO: `pr_link_backfill: linked story=STORY-885 repo=tech-dev-agents job=<uuid> pr=42`

---

### Group B — Tick: no PR found (T02)

| ID | Test | Expected | AC |
|----|------|----------|-----|
| B-01 / T02 | `test_no_pr_found_skips_update` | GitHub returns []; pool.execute NOT called | AC-6 |

Setup:
- pool.fetch returns 1 row: `{job_id: UUID, repo: "tech-dev-agents", story_id: "STORY-900"}`
- gh_fetch_fn returns `[]`

Expected:
- pool.execute NOT called
- Logger emits DEBUG (not INFO)

---

### Group C — Tick: multiple PRs (T03)

| ID | Test | Expected | AC |
|----|------|----------|-----|
| C-01 / T03 | `test_multiple_prs_picks_most_recent` | 3 PRs returned; UPDATE uses PR with highest created_at | AC-5 |

Setup:
- pool.fetch returns 1 row: story_id="STORY-900"
- gh_fetch_fn returns:
  ```
  [
    {"number": 10, "created_at": "2026-04-01T00:00:00Z"},
    {"number": 20, "created_at": "2026-04-30T00:00:00Z"},   <- most recent
    {"number": 15, "created_at": "2026-04-15T00:00:00Z"},
  ]
  ```

Expected:
- pool.execute called with pr_number=20

---

### Group D — Tick: idempotent / already-set rows skipped (T04)

| ID | Test | Expected | AC |
|----|------|----------|-----|
| D-01 / T04 | `test_idempotent_skips_already_set` | SQL WHERE clause excludes pr_number IS NOT NULL; pool.fetch returns empty -> no updates | AC-10 |

The SQL query itself enforces idempotency. This test verifies the query string passed
to pool.fetch contains `pr_number IS NULL`.

Setup:
- pool.fetch returns `[]` (simulates no NULL rows)
- gh_fetch_fn not called

Expected:
- pool.execute not called
- No error raised
- The SQL string captured in the mock contains "pr_number IS NULL"

---

### Group E — Tick: GitHub API error (T05)

| ID | Test | Expected | AC |
|----|------|----------|-----|
| E-01 / T05 | `test_github_api_error_logged_not_crash` | urllib raises exception; loop continues, warning logged | AC-7 |
| E-02 | `test_db_error_on_update_logged_not_crash` | pool.execute raises; loop continues, error logged | AC-7 |

Setup:
- pool.fetch returns 1 row
- gh_fetch_fn raises `urllib.error.URLError("network failure")`

Expected:
- pool.execute NOT called
- WARNING logged (or exception swallowed at job level)
- tick returns normally (no re-raise)

---

### Group F — Tick: non-story branch pattern skipped (T06)

| ID | Test | Expected | AC |
|----|------|----------|-----|
| F-01 / T06 | `test_non_story_id_skipped` | story_id="CUSTOM-123" -> no API call, debug log | AC-3 |

Setup:
- pool.fetch returns 1 row with story_id="EPIC-5"
- gh_fetch_fn not called

Expected:
- pool.execute NOT called
- tick returns normally
- No error raised

---

### Group G — Background loop (T07, T08)

**T07 — CancelledError exits loop cleanly**

Setup:
- Patch asyncio.sleep to raise CancelledError on first call
- Patch `_pr_link_backfill_tick` to AsyncMock

Expected:
- CancelledError propagates (loop exits)
- tick called once before cancel

**T08 — pool=None skips tick, loop continues**

Setup:
- Call `dispatch_pr_link_backfill_sweeper(pool=None)` with mocked sleep that cancels on second iteration

Expected:
- tick not called (or called with pool=None which immediately returns)
- No error raised

---

## Coverage Matrix

| AC | Tests |
|----|-------|
| AC-2 | A-01, D-01 |
| AC-3 | A-02, F-01 |
| AC-4 | A-01, A-02 |
| AC-5 | A-01, C-01 |
| AC-6 | B-01 |
| AC-7 | E-01, E-02 |
| AC-10 | D-01 |

## State Machine

```
NULL pr_number row in dispatch_jobs (in_review)
  -> tick picks it up
  -> GitHub API returns PR(s)
    -> pr_number SET -> INFO log
    -> no PR       -> DEBUG log (row stays NULL)
  -> GitHub error  -> WARNING log (row stays NULL, retry next tick)
```

---

## Test File Location

`tests/ops_console/test_dispatch_pr_link_backfill.py`

---

## RED / GREEN

Phase 7 (this document): tests written, all RED (ImportError until Phase 8 creates the implementation).

Phase 8: implementation added -> tests go GREEN.
