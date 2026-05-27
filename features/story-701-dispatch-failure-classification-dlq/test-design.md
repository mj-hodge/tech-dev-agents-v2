# Test Design: STORY-701 — Failure classification + Morris DLQ triage

## Phase 7 deliverable

| Field | Value |
|-------|-------|
| Story | STORY-701 |
| Phase | 7 (Test Design) |
| Status | Complete — 37 tests, RED→GREEN in Phase 8 |
| Test file | `tests/deployment/test_dispatch_failure_classification_701.py` |

---

## Test criteria (from seed §6)

| # | Criterion | Test group | Count |
|---|-----------|-----------|-------|
| 1 | Migration is idempotent | `TestMigration011` | 5 |
| 2 | `_classify_failure_reason` table-driven (all categories) | `TestClassifyFailureReason` | 8 |
| 3 | Classifier with unknown/None inputs → 'unknown', no exception | `TestClassifyFailureReasonUnknownInputs` | 4 |
| 4 | `_report_fail` passes `failure_reason` in POST body | `TestReportFailIncludesFailureReason` | 5 |
| 5 | `/dispatch/fail` route accepts and persists `failure_reason` | `TestFailRouteAcceptsFailureReason` | 4 |
| 6 | Morris DLQ triage returns correct shape | `TestDlqTriageQuery` | 8 |
| 7 | Triage cron is idempotent | `TestDlqTriageIdempotent` | 3 |
| | **Total** | | **37** |

---

## Test approach

### Group 1 — Migration idempotency
Parse `scripts/migrations/011_dispatch_failure_reason.sql` to verify:
- File exists
- Uses `IF NOT EXISTS` (idempotency)
- Targets `dispatch_items`
- Adds `failure_reason` column
- Column type is `VARCHAR(40)`

No live DB required. Pure file-system + text inspection.

### Group 2 — `_classify_failure_reason` table-driven
Import `dispatch_poller._classify_failure_reason(exit_code, error_text, retry_count)` directly.

Covers all 6 output categories:
- `rate_limit_exhausted` — exit_code=429 + retry_count≥MAX
- `branch_setup_failed` — "branch_setup_failed" in error_text
- `cross_story_validation_missing` — exit_code=422 + "Prompt references" in error_text
- `gate_rejection` — exit_code=422 (no Prompt references)
- `excessive_retries` — retry_count≥MAX (no special exit_code)
- `unknown` — no pattern matched

Also verifies rate_limit_exhausted requires retry exhaustion (not just exit 429).

### Group 3 — Classifier edge cases
- `None` error_text → 'unknown', no exception
- Empty string → 'unknown'
- exit_code=0 → 'unknown'
- Brute-force: calls classifier with `(None, None, None)`, `(999, None, -1)`, `(422, None, 0)` — none raise

### Group 4 — `_report_fail` POST body
Mock `requests.Session`, call `_report_fail(...)`, inspect the `json=` kwarg on the `/api/dispatch/fail/{story_id}` POST call. Verifies:
- `failure_reason` key is present
- Value is a non-None string
- Specific categories encoded correctly (gate_rejection for exit 422)
- `exit_code` still present (regression check)

### Group 5 — Route and DB service signature
- Inspect `DispatchDBService.fail` signature via `inspect.signature` — must have `failure_reason` kwarg defaulting to `None`
- Parse route source to verify `body.get("failure_reason")` extraction
- Verify `failure_reason=failure_reason` is passed to `db_svc.fail()`

### Group 6 — DLQ triage shape
Mock `asyncpg.connect` / `conn.fetch` with fixture data. Verify:
- Script exists and defines `async def main`
- SQL selects all required columns
- SQL filters `failure_reason IS NOT NULL` and `status = 'failed'`
- SQL orders `completed_at DESC`
- Output JSON has correct keys
- Datetimes converted to ISO strings

### Group 7 — Idempotency
- Run `main()` twice with same mock data — outputs must be identical
- Empty DB produces `[]` (valid JSON, no crash)
- `conn.close()` called on each run (no connection leak)

---

## Implementation notes

The existing `_classify_failure` function in `dispatch_poller.py` (added in STORY-641) classifies by `error_message: str` for the *retry-decision gate*. The new `_classify_failure_reason(exit_code, error_text, retry_count)` is a separate function for the *DB storage label* — different signature, different taxonomy, different purpose. Both coexist without collision.

The `failure_reason` taxonomy is VARCHAR-stored (no enum), so categories can be added or renamed without a schema migration.
