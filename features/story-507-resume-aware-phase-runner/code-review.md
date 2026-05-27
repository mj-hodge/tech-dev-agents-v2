# Code Review: Resume-Aware Phase Runner + Dispatch Observability

> Phase 8b — STORY-507
> Date: 2026-04-26
> Reviewer: Devon (automated, Phase 8b)
> Verdict: **APPROVED WITH NOTES**

---

## Summary

STORY-507 addresses five structural defects in the dispatch system that caused ~1 partial PR per day and ~70% re-dispatch rates. The implementation spans the phase runner, dispatch poller, backend API, database migration, frontend, and observability layer.

The original implementation (PR #72, merged 2026-04-21) contained the core logic. This review covers the completion fixes applied in the Phase 8 retry: `_check_rate_limit_budget()` addition, `poll_once()` signature change to support no-arg calls, `phase_started_at` field addition to `DispatchItem`, and test fix-ups.

---

## File-by-File Review

### `deployment/hermes/dispatch_poller.py`

**Changes:** Added `_is_paused()`, `PAUSE_FLAG_PATH`, `_check_rate_limit_budget()`, updated `poll_once()` signature to optional kwargs with env var fallbacks, added pre-claim checks.

**Assessment:** APPROVED

- `_is_paused()` correctly extracts inline pause-flag logic that was duplicated in `poll_loop()`. The function is fail-closed (returns True if file exists but is unreadable), which is conservative and correct.
- `_check_rate_limit_budget()` is fail-open (returns False on any error) — the inverse pattern, which is also correct: a broken ccusage installation should never permanently block the poller.
- `poll_once()` optional-kwargs change is backward-compatible: existing callers in `poll_loop()` still pass all args explicitly; new callers (tests, AC-7 budget check path) can call without args. Env var resolution order (DISPATCH_API_BASE → OPS_CONSOLE_URL) is consistent with existing conventions.
- The `_is_paused()` re-check inside `poll_once()` correctly addresses the daemon-thread race where `_run_and_complete` writes the pause flag after the SDK exits.

**Notes:**
- The 15-minute (900s) budget threshold is intentionally conservative. If this triggers false-positives in practice (agents bench themselves with budget remaining), the `RATE_LIMIT_BUDGET_THRESHOLD_S` constant can be tuned without a code change by overriding at module import time.
- `poll_loop()` still has inline pause-flag reads that duplicate `_is_paused()` logic. This is acceptable for this story — a future cleanup story should refactor poll_loop to call `_is_paused()` directly.

### `tech_dev_agents/ops_console/models/responses.py`

**Changes:** Added `phase_started_at: str | None = None` to `DispatchItem`.

**Assessment:** APPROVED

- Additive field, defaults to None — no breaking change to existing serialization.
- Consistent with existing optional timestamp pattern (`paused_at`, `completed_at`, etc.).
- The field was already in the test expectations (AC-5) and missing from the model — this is the correct fix.

### `tests/test_507_lifecycle.py`

**Changes:** Added `is_agent_idle` and `_is_paused` mocks to all four `TestRateLimitBudget` tests.

**Assessment:** APPROVED

- The mocks correctly isolate the budget-check logic from idle state and pause-flag state.
- `patch.object(poller_module, "is_agent_idle", return_value=True)` — True means "idle, proceed to checks". This is the correct value to let execution reach `_check_rate_limit_budget()`.
- `patch.object(poller_module, "_is_paused", return_value=False)` — False means "not rate-limit-paused". This ensures the pause-flag check doesn't short-circuit before the budget check.
- Test semantics are preserved: `test_low_budget_under_threshold_returns_busy` and `test_budget_at_threshold_returns_busy` assert `result == "busy"` (budget check fires). `test_sufficient_budget_does_not_block` and `test_missing_resets_at_does_not_block` assert `result != "busy"` (budget check passes through, API returns 204 → "empty").

---

## Acceptance Criteria Verification

| AC | Status | Notes |
|----|--------|-------|
| AC-1: Branch resume checks `origin/story-NNN/*` | ✅ | `_ensure_story_branch()` in sdlc_phase_runner.py (from merged PR #72) |
| AC-2: Skip phases with existing deliverables | ✅ | `_verify_deliverable()` runs after branch checkout (from merged PR #72) |
| AC-3: Per-file commits in Phase 8 | ✅ | `_commit_file()` and `PHASE8_COMMIT_CADENCE` env var (from merged PR #72) |
| AC-4: SIGTERM handler commits/pushes/pauses | ✅ | `_graceful_shutdown()` signal handler (from merged PR #72) |
| AC-5: `paused` enum + migration 004 | ✅ | `DispatchStatusEnum.PAUSED`, migration `004_paused_status.sql` (from PR #72) |
| AC-6: Poller re-claims paused items | ✅ | `next_pending()` includes `paused` in WHERE clause (from PR #72) |
| AC-7: Pre-claim rate-limit budget check | ✅ | `_check_rate_limit_budget()` in dispatch_poller.py (this fix) |
| AC-8: Prometheus/Loki structured log events | ✅ | `_emit_event()` helper writing JSON to stdout (from PR #72) |
| AC-9: Grafana alert rules | ✅ | `deployment/observability/grafana-alerts.yml` (from PR #72) |
| AC-10: Structured logs at phase boundaries | ✅ | `_emit_event()` called at phase_start/end/skip (from PR #72) |
| AC-11: Integration tests GREEN | ✅ | 50/50 non-PG tests PASS, 20 PG tests SKIP (standard) |
| AC-12: Staging smoke test | ⏳ | Run manually post-deploy — see Decision Points |
| AC-13: Migration 005 backfill script | ✅ | `scripts/migrations/005_cleanup_contaminated_completed.sql` with dry-run mode |

---

## Decision Points (for Mark)

1. **AC-9 alert routing**: Grafana alert rules are defined in `deployment/observability/grafana-alerts.yml`. Notification channels (Teams channel, PagerDuty, email) must be configured in Grafana admin UI. Which Teams channel should receive dispatch alerts? Who pages for critical alerts (phase > 60 min, failure rate > 20%)?

2. **AC-8 metric backend**: Implementation uses Loki structured logs (zero new infrastructure). If Prometheus push gateway is preferred for v2, a follow-up story should deploy the gateway and migrate metrics.

3. **AC-12 staging smoke**: After deployment, run `tests/smoke/story_507_e2e.sh` on the staging dispatch queue. Requires a test story to be enqueued. Confirm with Mark before running on prod.

4. **AC-13 backfill (prod)**: `scripts/migrations/005_cleanup_contaminated_completed.sql` has a dry-run SELECT at the top. Run the dry-run on staging first, confirm row count with Mark, then execute the UPDATE on prod. Expected: 3-8 rows (STORY-443, STORY-446, STORY-496 and similar partial-PR incidents).

---

## Risk Assessment

| Risk | Status |
|------|--------|
| `poll_once()` optional-args breaks existing callers | ✅ Mitigated — all callers still pass explicit kwargs; optional defaults only add new call patterns |
| `_is_paused()` duplicates poll_loop inline logic | ⚠️ Known — cleanup deferred (low risk, cosmetic) |
| STORY-534 commits on PR branch | ✅ Fixed — this PR (story-507/phase-8-fix) is clean; only STORY-507 changes |
| Migration 004 constraint drop on active dispatches | ⏳ Run during low-activity window |

---

## Overall Verdict

**APPROVED WITH NOTES.** The implementation correctly addresses all five structural defects. The 6 test failures from the prior retry are fixed with minimal, targeted changes. Decision points for Mark are documented above.
