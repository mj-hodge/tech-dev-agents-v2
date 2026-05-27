# Test Design — STORY-901: PR-merge → v2 completed transition

## Phase 7 — RED state

Tests live in `tests/test_story901_pr_merge_completes_v2.py`.

## Test Strategy

Unit tests only (no DB required). Patch `asyncio.create_subprocess_exec` to control `gh pr view` output and mock `record_event` to assert calls.

The sweeper's `_pr_merge_sweeper_tick()` function takes explicit dependencies (pool, gh_caller, record_event) — this makes it injectable and eliminates subprocess setup complexity in tests.

## Test Cases

### TC-1: emits accepted for merged PR
**Given:** A v2 row in `in_review` with `pr_number=309`, `repo="hpi-gorillacommerce/tech-dev-agents"`
**When:** `gh pr view 309` returns `{"state": "MERGED", "mergedAt": "2026-05-05T17:50:00Z"}`
**Then:** `record_event` is called with `event_type="accepted"`, `event_data={"actor": "pr-merge-sweeper", "pr_number": 309, "repo": "..."}`

### TC-2: idempotent — does not double-emit for terminal rows
**Given:** A row that was already `accepted` (state transitioned to `completed`)
**When:** The sweeper queries `in_review` rows — the completed row does NOT appear (it's in the `terminal` lane)
**Then:** `record_event` is never called for that job_id

### TC-3: does NOT emit for open PRs
**Given:** A v2 row in `in_review` with `pr_number=310`
**When:** `gh pr view 310` returns `{"state": "OPEN", "mergedAt": null}`
**Then:** `record_event` is NOT called

### TC-4: does NOT emit for PRs closed without merge
**Given:** A v2 row in `in_review` with `pr_number=311`
**When:** `gh pr view 311` returns `{"state": "CLOSED", "mergedAt": null}`
**Then:** `record_event` is NOT called

### TC-5: skips rows with pr_number NULL
**Given:** A v2 row in `in_review` with `pr_number=None` (agent hasn't linked a PR yet)
**When:** The sweeper runs
**Then:** `gh pr view` is never called and `record_event` is NOT called

## Test Infrastructure

All tests are synchronous (using `asyncio.run()`). The `gh` CLI and DB pool are fully mocked. Tests do NOT require a live PostgreSQL connection.
