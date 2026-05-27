# Test Design — STORY-700: `dispatch_events` Append-Only Log Table

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-700 |
| Scope | Medium |
| Coverage Target | 60% |
| Frontend | No |
| Test File | `tests/ops_console/test_dispatch_events.py` |
| Total Tests | 18 |
| Groups | 8 (A–H) |

---

## Test Matrix

| Group | ID | Test Name | Type | What It Verifies |
|-------|----|-----------|------|------------------|
| A: Migration | A1 | `test_migration_creates_table_on_fresh_db` | PG integration | Table + columns exist after migration |
| A: Migration | A2 | `test_migration_idempotent_rerun` | PG integration | No error on second execution |
| A: Migration | A3 | `test_migration_creates_all_three_indexes` | PG integration | All 3 named indexes in pg_indexes |
| B: emit() fields | B1 | `test_emit_all_fields_populated_writes_row` | Unit (mock pool) | INSERT with correct parameters |
| B: emit() fields | B2 | `test_emit_agent_none_phase_none_accepted` | Unit (mock pool) | NULL optional columns pass through |
| B: emit() fields | B3 | `test_emit_payload_none_defaults_to_empty_jsonb` | Unit (mock pool) | COALESCE($6, '{}') path |
| B: emit() fields | B4 | `test_emit_payload_with_nested_dict_serializes` | Unit (mock pool) | JSONB serialization with nested structure |
| C: emit() safety | C1 | `test_emit_db_error_does_not_raise_to_caller` | Unit (mock pool) | try/except catches DB exception |
| C: emit() safety | C2 | `test_emit_pool_none_logs_warning_no_raise` | Unit | Pre-init safety path |
| C: emit() safety | C3 | `test_emit_db_error_logs_exception_with_context` | Unit (mock pool) | logger.exception includes story_id, repo, event_type |
| D: Route events | D1 | `test_enqueue_emits_enqueued_event` | Unit (mock emit) | Correct event_type + payload fields |
| D: Route events | D2 | `test_claim_emits_claimed_event` | Unit (mock emit) | Agent name passed through |
| D: Route events | D3 | `test_complete_emits_completed_event` | Unit (mock emit) | commit_sha + pr_number in payload |
| D: Route events | D4 | `test_fail_emits_failed_event` | Unit (mock emit) | exit_code in payload |
| E: Phase runner | E1 | `test_phase_loop_emits_start_and_end_per_phase` | Unit (mock subprocess) | 4 events emitted for 2 phases |
| F: Poller | F1 | `test_retry_path_emits_retry_enqueued_event` | Unit (mock HTTP) | attempt count in payload |
| G: Indexes | G1 | `test_sample_queries_on_seeded_data` | PG integration | Queries on idx_de_story_repo_ts + idx_de_event_type_ts return results |
| H: Retention | H1 | `test_retention_deletes_only_old_rows` | PG integration | Rows < 120d deleted, recent kept |

---

## Group Details

### Group A: Migration (PG integration)

Tests require PostgreSQL `ops_console_test` with migration 010 applied.
Skip automatically when PG not reachable (same pattern as STORY-531).

**A1 — `test_migration_creates_table_on_fresh_db`**
- Arrange: Execute `010_dispatch_events.sql` on test DB
- Act: Query `information_schema.tables` for `dispatch_events`
- Assert: Table exists with expected columns (id, story_id, repo, event_type, agent, phase_num, payload, ts)

**A2 — `test_migration_idempotent_rerun`**
- Arrange: Execute migration once
- Act: Execute migration a second time
- Assert: No exception raised

**A3 — `test_migration_creates_all_three_indexes`**
- Arrange: Migration applied
- Act: Query `pg_indexes WHERE tablename = 'dispatch_events'`
- Assert: `idx_de_story_repo_ts`, `idx_de_event_type_ts`, `idx_de_agent_ts` all present

### Group B: emit() Field Handling (Unit, mock pool)

Mock asyncpg pool — verify SQL parameters passed to `conn.execute()`.

**B1 — `test_emit_all_fields_populated_writes_row`**
- Arrange: Mock pool + conn; call `emit("STORY-1", "repo-a", "claimed", agent="hermes", phase_num=7, payload={"k": "v"})`
- Assert: `conn.execute` called with all 6 positional args matching

**B2 — `test_emit_agent_none_phase_none_accepted`**
- Arrange: Call `emit("STORY-1", "repo-a", "enqueued")` — no optional kwargs
- Assert: `conn.execute` called with agent=None, phase_num=None, payload param is None

**B3 — `test_emit_payload_none_defaults_to_empty_jsonb`**
- Arrange: Call `emit(... payload=None)`
- Assert: 6th parameter to `conn.execute` is None (server-side COALESCE handles default)

**B4 — `test_emit_payload_with_nested_dict_serializes`**
- Arrange: Call `emit(... payload={"a": {"b": 1}, "ts": "2026-01-01"})`
- Assert: 6th parameter is a JSON string containing nested structure

### Group C: emit() Safety (Unit, mock pool)

**C1 — `test_emit_db_error_does_not_raise_to_caller`**
- Arrange: Mock `conn.execute` to raise `asyncpg.PostgresError`
- Act: `await emit(...)`
- Assert: No exception propagated to caller

**C2 — `test_emit_pool_none_logs_warning_no_raise`**
- Arrange: Set module `_pool = None`
- Act: `await emit(...)`
- Assert: `logger.warning` called with "called before init()" message, no exception

**C3 — `test_emit_db_error_logs_exception_with_context`**
- Arrange: Mock `conn.execute` to raise `Exception("connection lost")`
- Act: `await emit(...)`
- Assert: `logger.exception` called; log message contains story_id, repo, event_type

### Group D: Route Event Emission (Unit, mock emit)

Patch `tech_dev_agents.ops_console.routes.dispatch.emit_event` (the import alias).
Use the existing `client` fixture from conftest.py with mock dispatch_db_service.

**D1 — `test_enqueue_emits_enqueued_event`**
- Arrange: Mock db_svc.enqueue to return a row
- Act: POST /api/dispatch
- Assert: emit_event called with event_type="enqueued", payload contains scope + title

**D2 — `test_claim_emits_claimed_event`**
- Arrange: Mock db_svc.claim to return a row
- Act: POST /api/dispatch/claim/{story_id}
- Assert: emit_event called with event_type="claimed", agent matches request body

**D3 — `test_complete_emits_completed_event`**
- Arrange: Mock db_svc.complete, github commit check
- Act: POST /api/dispatch/complete/{story_id}
- Assert: emit_event called with event_type="completed", payload has commit_sha + pr_number

**D4 — `test_fail_emits_failed_event`**
- Arrange: Mock db_svc.fail to return a row
- Act: POST /api/dispatch/fail/{story_id}
- Assert: emit_event called with event_type="failed", payload has exit_code

### Group E: Phase Runner Events (Unit, mock subprocess)

**E1 — `test_phase_loop_emits_start_and_end_per_phase`**
- Arrange: Mock `_run_phase_sdk` to return (0, "output") for 2 phases; mock `_emit_event`
- Act: Call `run_sdlc_phases()` with a 2-phase story
- Assert: `_emit_event` called 4+ times — at least phase_started + phase_ended per phase

### Group F: Poller Events (Unit, mock HTTP)

**F1 — `test_retry_path_emits_retry_enqueued_event`**
- Arrange: Mock `session.post` for both /fail and /dispatch (re-enqueue); mock `_emit_event`
- Act: Call `_report_fail()` with valid retry conditions
- Assert: `_emit_event` called with event_type="retry_enqueued", payload has attempt

### Group G: Index Validation (PG integration)

**G1 — `test_sample_queries_on_seeded_data`**
- Arrange: Seed 20+ events with varied story_id, repo, event_type, agent, ts values
- Act: Run 3 queries: by (story_id, repo, ts), by (event_type, ts), by (agent, ts)
- Assert: All 3 queries return non-empty results

### Group H: Retention (PG integration)

**H1 — `test_retention_deletes_only_old_rows`**
- Arrange: Insert rows with ts = now() and ts = now() - 130 days
- Act: Execute retention DELETE loop (same SQL as retention script)
- Assert: Old rows deleted, recent rows preserved

---

## Output-Variance Tests

**B1 + B2 together** serve as the output-variance pair for `emit()`: B1 supplies all fields populated; B2 supplies agent=None/phase_num=None — the SQL parameters differ.

**D1 + D4** serve as the output-variance pair for route events: enqueue produces payload with `scope`+`title`; fail produces payload with `exit_code` — different inputs produce different event payloads.

---

## Error Observability (Gate 10)

- C3 explicitly verifies `logger.exception()` is called on DB errors with identifying context
- C2 verifies `logger.warning()` on pre-init calls

---

## Defensive Gates

### Gate 1: Null/None Boundary
- B2: agent=None, phase_num=None
- B3: payload=None
- C2: _pool=None

### Gate 9: Failure Recovery
- C1: DB error → emit() returns cleanly, caller unaffected
- C2: Pool not initialized → warning logged, no crash

### Gate 10: Error Observability
- C3: DB exception → logger.exception with full context

---

## API Mock Verification

No Playwright tests (backend-only story). Route tests mock `emit_event` at the import site in `routes/dispatch.py`. The internal `/api/internal/dispatch-event` endpoint is tested via the phase runner's `_emit_event` helper (Group E/F use mock HTTP, not route-level tests).

---

## RED State Expectations

| Group | Expected RED Reason |
|-------|---------------------|
| A (Migration) | PG skip (no PG) OR migration file doesn't exist yet |
| B (emit fields) | `dispatch_events` module doesn't exist → ImportError handled via defensive import |
| C (emit safety) | Same — module doesn't exist |
| D (Route events) | `emit_event` not imported in routes yet |
| E (Phase runner) | `_emit_event` helper not defined in phase runner |
| F (Poller) | `_emit_event` helper not defined in poller |
| G (Indexes) | PG skip OR no migration applied |
| H (Retention) | PG skip OR no migration applied |

---

## Checklist

- [x] Every endpoint/method that transforms input has an output-variance test
- [x] Output-variance tests assert on computed fields, not just status codes
- [x] Two meaningfully different inputs are used
- [x] At least 2 boundary condition tests (B2, B3 for emit; C1, C2 for error paths)
- [x] At least 1 empty/null input test per optional param (B2, B3, C2)
- [x] Response schema validation for internal endpoint (via route mock assertions)
- [x] Every broad except block has error observability test (C3)
- [x] No test uses `pytest.raises(ImportError)` as passing condition
- [x] Tests call real functions under test (mocking only DB/HTTP, not the function itself)
- [x] Per-test timeout via pyproject.toml `timeout = 15`
