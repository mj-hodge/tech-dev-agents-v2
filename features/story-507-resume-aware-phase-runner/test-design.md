# Test Design: Resume-Aware Phase Runner + Dispatch Observability

> Phase 7 — STORY-507
> Date: 2026-04-21
> Scope: Large
> State: RED (tests written before implementation)

---

## Overview

This document covers the test design for STORY-507. Tests are split between two files:

| File | Purpose |
|------|---------|
| `tests/test_507_lifecycle.py` | DB-service unit tests (real PG), phase-runner unit tests (mocked subprocess), file-existence checks |
| `tests/ops_console/test_507_paused_routes.py` | API route integration tests (FastAPI test client, mocked DB service) |

All tests are written in RED state. They will fail until Phase 8 implementation is complete.

---

## Test Groups

### Group A — Paused Status Enum & Model (AC-5)

**File:** `tests/test_507_lifecycle.py` — `TestPausedStatusEnum`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| A-01 | `test_paused_enum_value` | `DispatchStatusEnum.PAUSED == "paused"` | Enum value not defined |
| A-02 | `test_paused_enum_is_str` | `DispatchStatusEnum.PAUSED` is a `str` subclass | Enum value not defined |
| A-03 | `test_dispatch_item_has_paused_at_field` | `DispatchItem` model has `paused_at: str \| None` | Field not in model |
| A-04 | `test_dispatch_item_has_current_phase_field` | `DispatchItem` model has `current_phase: int \| None` | Field not in model |
| A-05 | `test_dispatch_item_has_phase_started_at_field` | `DispatchItem` model has `phase_started_at: str \| None` | Field not in model |

---

### Group B — DB Service: `pause()` Method (AC-5, AC-6)

**File:** `tests/test_507_lifecycle.py` — `TestDispatchDBPause`

Requires: PostgreSQL `ops_console_test` with migrations 001–004 applied.

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| B-01 | `test_pause_transitions_claimed_to_paused` | `pause(story_id, agent)` updates status to `paused` | `pause()` method missing |
| B-02 | `test_pause_sets_paused_at_timestamp` | `pause()` sets non-null `paused_at` within 5s of now | `pause()` method missing |
| B-03 | `test_pause_stores_current_phase` | `pause(story_id, agent, current_phase=6)` persists phase number | `pause()` method missing |
| B-04 | `test_pause_rejects_pending_items` | `pause()` on a pending item raises `ValueError` | `pause()` method missing |
| B-05 | `test_pause_raises_not_found_for_unknown` | `pause()` on non-existent story raises `NotFoundError` | `pause()` method missing |
| B-06 | `test_pause_rejects_wrong_agent` | `pause()` with wrong agent name (not the claimer) raises `ValueError` | `pause()` method missing |

---

### Group C — DB Service: `next_pending()` and `claim()` With Paused (AC-6)

**File:** `tests/test_507_lifecycle.py` — `TestNextPendingWithPaused`, `TestClaimPausedItem`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| C-01 | `test_next_pending_returns_paused_items` | `next_pending()` returns a paused story when queue has only paused items | Current query only returns `pending` |
| C-02 | `test_next_pending_preserves_fifo_order` | Paused item enqueued before pending item is returned first | Current query only returns `pending` |
| C-03 | `test_next_pending_returns_pending_over_paused_same_time` | FIFO order by `enqueued_at`, not by status | Current query only returns `pending` |
| C-04 | `test_claim_accepts_paused_to_claimed` | `claim(story_id, agent)` transitions `paused` → `claimed` | WHERE clause only accepts `pending` |
| C-05 | `test_claim_paused_updates_claimed_at` | Re-claim of paused item sets new `claimed_at`, preserves `paused_at` | WHERE clause only accepts `pending` |

---

### Group D — DB Service: `recover_stale_claims()` Excludes Paused (AC-6)

**File:** `tests/test_507_lifecycle.py` — `TestRecoverStaleClaimsExcludesPaused`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| D-01 | `test_recover_stale_claims_skips_paused` | Paused items are not reverted to pending by stale-claim recovery | Behavior not explicitly guarded (currently passes by coincidence only if query is `status='claimed'`) |
| D-02 | `test_list_queue_includes_paused_items` | `list_queue()` returns items with status in `('pending', 'claimed', 'paused')` | Current query only returns `pending` + `claimed` |

---

### Group E — API Routes: Pause Endpoint and Queue Visibility (AC-5, AC-6)

**File:** `tests/ops_console/test_507_paused_routes.py`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| E-01 | `test_pause_endpoint_returns_200` | `POST /api/dispatch/pause/{story_id}` returns 200 with paused item | Endpoint not defined |
| E-02 | `test_pause_endpoint_response_schema` | Response has `status="paused"`, `paused_at` non-null, `current_phase` correct | Endpoint not defined |
| E-03 | `test_pause_endpoint_404_on_missing_story` | Returns 404 when story_id not in active queue | Endpoint not defined |
| E-04 | `test_pause_endpoint_409_on_wrong_state` | Returns 409 when story is pending (not claimed) | Endpoint not defined |
| E-05 | `test_queue_endpoint_includes_paused_items` | `GET /api/dispatch/queue` response includes items with `status="paused"` | Queue query excludes paused |
| E-06 | `test_next_endpoint_returns_paused_story` | `GET /api/dispatch/next` returns a paused story (no pending items present) | next_pending() excludes paused |

---

### Group F — Phase Runner: Branch Resume (AC-1, AC-2)

**File:** `tests/test_507_lifecycle.py` — `TestPhaseRunnerBranchResume`

Unit tests using mocked subprocess calls.

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| F-01 | `test_ensure_branch_calls_ls_remote` | `_ensure_branch()` calls `git ls-remote --heads origin story-507/*` | Branch resume logic not in `_ensure_branch()` |
| F-02 | `test_ensure_branch_fetches_remote_when_found` | If `ls-remote` returns a branch ref, `git fetch origin <branch>` is called | Branch resume logic not present |
| F-03 | `test_ensure_branch_checkouts_remote_branch` | After fetch, `git checkout -b <branch> origin/<branch>` is attempted | Branch resume logic not present |
| F-04 | `test_ensure_branch_creates_new_on_no_remote` | If `ls-remote` returns empty, creates new branch from main | Greenfield path still works; verify via emit_event check |
| F-05 | `test_ensure_branch_emits_branch_resume_event` | After resume checkout, emits `branch_resume` structured event | `_emit_event` not present; event not emitted |

---

### Group G — Phase Runner: SIGTERM Graceful Shutdown (AC-4)

**File:** `tests/test_507_lifecycle.py` — `TestPhaseRunnerSIGTERM`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| G-01 | `test_graceful_shutdown_function_exists` | `_graceful_shutdown` is defined in `sdlc_phase_runner` | Function not defined |
| G-02 | `test_shutdown_event_exists` | `_shutdown_requested` threading.Event is defined | Not defined |
| G-03 | `test_graceful_shutdown_calls_save_partial_work` | Handler calls `_save_partial_work` before exiting | Handler not defined |
| G-04 | `test_graceful_shutdown_calls_pause_api` | Handler POSTs to `/dispatch/pause/{story_id}` | Handler not defined |
| G-05 | `test_graceful_shutdown_exit_code_143` | `sys.exit(143)` is called by handler | Handler not defined |

---

### Group H — Phase Runner: Per-File Commits (AC-3)

**File:** `tests/test_507_lifecycle.py` — `TestPhaseRunnerPerFileCommits`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| H-01 | `test_commit_file_function_exists` | `_commit_file` is defined in `sdlc_phase_runner` | Function not defined |
| H-02 | `test_commit_file_stages_and_commits` | `_commit_file(workdir, filepath, story_id)` runs `git add` + `git commit` | Function not defined |
| H-03 | `test_commit_file_returns_true_on_success` | Returns `True` when commit succeeds | Function not defined |
| H-04 | `test_commit_file_returns_false_on_failure` | Returns `False` when commit fails (e.g., nothing to commit) | Function not defined |
| H-05 | `test_phase8_commit_cadence_env_var_defined` | `PHASE8_COMMIT_CADENCE` module-level constant exists, defaults to `"per_file"` | Constant not defined |

---

### Group I — Phase Runner: Structured Log Events (AC-8, AC-10)

**File:** `tests/test_507_lifecycle.py` — `TestStructuredLogEvents`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| I-01 | `test_emit_event_function_exists` | `_emit_event` is defined in `sdlc_phase_runner` | Function not defined |
| I-02 | `test_emit_event_outputs_json_to_stdout` | Calling `_emit_event(type, story_id=...)` writes valid JSON to stdout | Function not defined |
| I-03 | `test_emit_event_required_fields` | Every event has: `event`, `story_id`, `agent`, `timestamp` | Function not defined |
| I-04 | `test_emit_event_phase_start_has_phase` | `_emit_event("phase_start", ..., phase=4)` includes `phase` in JSON | Function not defined |
| I-05 | `test_emit_event_phase_end_has_duration_and_status` | `_emit_event("phase_end", ..., duration_s=120, status="success")` includes both fields | Function not defined |
| I-06 | `test_emit_event_phase_skip_has_reason` | `_emit_event("phase_skip", ..., details={"reason": "deliverable_exists"})` includes `reason` | Function not defined |
| I-07 | `test_emit_event_sigterm_has_phase` | `_emit_event("sigterm_received", ..., phase=8)` includes `phase` | Function not defined |
| I-08 | `test_emit_event_timestamp_is_utc_iso8601` | `timestamp` field is parseable as ISO 8601 UTC | Function not defined |

---

### Group J — Dispatch Poller: Rate-Limit Budget Check (AC-7)

**File:** `tests/test_507_lifecycle.py` — `TestRateLimitBudget`

Unit tests using mocked `ccusage` output.

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| J-01 | `test_low_budget_under_threshold_returns_busy` | `poll_once()` returns `"busy"` when `resets_at - now < 900s` | Threshold check not implemented |
| J-02 | `test_budget_exactly_at_threshold_returns_busy` | Returns `"busy"` when exactly 900s remain (boundary: inclusive) | Threshold check not implemented |
| J-03 | `test_sufficient_budget_allows_poll` | Returns something other than `"busy"` when `resets_at - now > 900s` | Threshold check not implemented |
| J-04 | `test_missing_resets_at_does_not_block` | Missing or malformed `ccusage` output → does not block poll cycle | Threshold check not implemented |

---

### Group K — Observability: Grafana Alert Config (AC-9)

**File:** `tests/test_507_lifecycle.py` — `TestGrafanaAlerts`

File-existence and schema validation tests.

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| K-01 | `test_grafana_alerts_file_exists` | `deployment/observability/grafana-alerts.yml` exists | File not created |
| K-02 | `test_grafana_alerts_parseable_yaml` | File is valid YAML with expected top-level keys | File not created |
| K-03 | `test_phase_duration_warning_alert_rule` | Rule `phase-duration-warning` with threshold 1800 exists | File not created |
| K-04 | `test_phase_duration_critical_alert_rule` | Rule `phase-duration-critical` with threshold 3600 exists | File not created |
| K-05 | `test_failure_rate_alert_rule` | Rule `dispatch-failure-rate` with threshold 0.2 exists | File not created |
| K-06 | `test_partial_pr_alert_rule` | Rule `partial-pr-rate` exists | File not created |

---

### Group L — Migration Files: SQL Existence and Content (AC-5, AC-13)

**File:** `tests/test_507_lifecycle.py` — `TestMigrationFiles`

| ID | Test Name | What It Verifies | RED Reason |
|----|-----------|-----------------|------------|
| L-01 | `test_migration_004_file_exists` | `scripts/migrations/004_paused_status.sql` exists | File not created |
| L-02 | `test_migration_004_adds_paused_at_column` | SQL contains `ADD COLUMN` + `paused_at` | File not created |
| L-03 | `test_migration_004_adds_current_phase_column` | SQL contains `ADD COLUMN` + `current_phase` | File not created |
| L-04 | `test_migration_004_updates_check_constraint` | SQL contains `'paused'` in CHECK constraint definition | File not created |
| L-05 | `test_migration_004_updates_unique_index` | SQL recreates `uq_story_active_idx` to include `paused` | File not created |
| L-06 | `test_migration_005_file_exists` | `scripts/migrations/005_cleanup_contaminated_completed.sql` exists | File not created |
| L-07 | `test_migration_005_has_dry_run_select` | SQL file contains a `SELECT` statement targeting `completed` rows | File not created |
| L-08 | `test_migration_005_update_is_commented` | The destructive `UPDATE` statement is commented out by default | File not created |

---

## Test Infrastructure

### Database Tests (Groups B, C, D)

```
PostgreSQL: ops_console_test database
Prerequisites: migrations 001–004 applied
Skip condition: if DB not reachable (same pattern as test_dispatch_db_service.py)
```

These tests require migration 004 to be applied before Phase 8 testing. Apply with:
```bash
psql -d ops_console_test -f scripts/migrations/004_paused_status.sql
```

### Phase Runner Tests (Groups F, G, H, I)

```
Import strategy: sys.path.insert(0, "deployment/hermes/")
                 import sdlc_phase_runner as _runner_module
                 # Functions tested via getattr() to avoid module-level ImportError
Subprocess: mocked with unittest.mock.patch("subprocess.run")
API calls:  mocked with unittest.mock.patch("urllib.request.urlopen")
Signal:     tested via direct handler invocation (not signal.raise_signal)
```

### Poller Tests (Group J)

```
Import strategy: sys.path.insert(0, "deployment/hermes/")
                 import dispatch_poller as _poller_module
ccusage:    mocked via patch("subprocess.run") returning fixture JSON
```

### Route Tests (Group E — `tests/ops_console/test_507_paused_routes.py`)

```
Framework: httpx.AsyncClient via ASGITransport(app=create_app(...))
DB:        mock DispatchDBService injected via app.state
Auth:      TEST_API_KEY header (from ops_console conftest)
```

---

## Fixtures Summary

| Fixture | Location | Purpose |
|---------|----------|---------|
| `db_pool` | `test_507_lifecycle.py` | asyncpg pool to test PG (reuse pattern from test_dispatch_db_service.py) |
| `svc` | `test_507_lifecycle.py` | DispatchDBService backed by `db_pool` |
| `runner_module` | `test_507_lifecycle.py` | `sdlc_phase_runner` module loaded via sys.path |
| `poller_module` | `test_507_lifecycle.py` | `dispatch_poller` module loaded via sys.path |
| `tmp_git_repo` | `test_507_lifecycle.py` | Initialized git repo in `tmp_path` |
| `mock_pause_api` | `test_507_lifecycle.py` | Patches urllib.request.urlopen for pause API call |
| `test_client` | `test_507_paused_routes.py` | httpx AsyncClient with ASGITransport |
| `mock_dispatch_db` | `test_507_paused_routes.py` | AsyncMock of DispatchDBService |

---

## Acceptance Criteria → Test Mapping

| AC | Groups | Tests |
|----|--------|-------|
| AC-1 (Branch resume) | F | F-01, F-02, F-03, F-04, F-05 |
| AC-2 (Deliverable skip) | F | F-04 (skip verified via phase skip event) |
| AC-3 (Per-file commits) | H | H-01 through H-05 |
| AC-4 (SIGTERM handler) | G | G-01 through G-05 |
| AC-5 (Paused status) | A, B, E, L | A-01–05, B-01–06, E-01–04, L-01–08 |
| AC-6 (Paused re-claim) | C, D, E | C-01–05, D-01–02, E-05–06 |
| AC-7 (Rate-limit budget) | J | J-01 through J-04 |
| AC-8 (Prometheus/Loki metrics) | I | I-01 through I-08 |
| AC-9 (Grafana alerts) | K | K-01 through K-06 |
| AC-10 (Structured log format) | I | I-02 through I-08 |
| AC-11 (All tests GREEN) | All | All tests above |
| AC-12 (Staging smoke test) | Manual | Not automated; see post-deploy runbook |
| AC-13 (Contaminated row migration) | L | L-06, L-07, L-08 |

---

## Non-Automated Tests (AC-12)

**Staging smoke test procedure (run manually post-deploy):**

1. Dispatch a `small`-scope test story to staging queue
2. Confirm agent claims and runs phases to completion
3. Verify PR is created with all required deliverables (seed.md, test-design.md, implementation)
4. Verify structured log events appear in Loki: `{job="dispatch"} | json | event="phase_end"`
5. Confirm Grafana alert rules appear under Dispatch folder (import grafana-alerts.yml)
6. Verify `paused` status badge appears in DispatchQueue.tsx Paused tab

---

## Out of Scope

- **Frontend DispatchQueue.tsx tests:** The paused tab and phase-progress column are verified by manual review (see AC-12 staging smoke test). No automated Jest/Cypress tests are written in this story.
- **Grafana LogQL recording rules:** Validated by checking structured log format (Group I) + confirming Loki receives events post-deploy.
- **Production contaminated row migration:** Verified by dry-run on staging; confirmed with Mark before prod execution (AC-13).
- **SIGTERM real-signal integration test:** The full SIGTERM→exit-143 integration test (spawning a real phase runner process and sending OS-level SIGTERM) is deferred to staging smoke test; Group G covers the handler logic via direct invocation.
