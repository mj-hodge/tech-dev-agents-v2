# Test Design — STORY-917: needs_info question_text guard

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-917 |
| Phase | 7 — Test Design (RED state) |
| Scope | small |
| Test file | `tests/test_story917_needs_info_question_text_guard.py` |
| Test count | 12 (T01–T12) |
| DB required | Yes (Groups B–I require PostgreSQL) |
| RED reason | Service guard absent (T11–T12 pass without raising); migration file missing (T01–T08 fail on FileNotFoundError) |

## Test Groups

### Group A — Service-layer guard (T09–T12)

**Description:** `dispatch_v2_service.transition()` must reject `needs_info` events that have neither `question_text` nor `needs_info_path` in `event_data`. Mirrors the `submitted → in_review` PR-link guard (lines 579–617).

**Fixture:** Uses `raw_conn` + `svc` fixtures from Q2 pattern (migrations 050+051+056 applied; migration 060 NOT applied — tests service layer in isolation).

**Setup per test:** Insert job → emit `enqueued` + `leased` events → insert dispatch_leases row.

| ID | Test | Expected |
|----|------|----------|
| T09 | `needs_info` with `question_text` only | Returns event_id (int) — succeeds |
| T10 | `needs_info` with `needs_info_path` only | Returns event_id (int) — succeeds |
| T11 | `needs_info` with neither key | `InvalidEventDataError` raised |
| T12 | `needs_info` with empty-string `question_text`, no `needs_info_path` | `InvalidEventDataError` raised |

**RED state:** T09/T10 are GREEN (valid inputs succeed with or without guard). T11/T12 are RED — no guard exists, transition succeeds instead of raising.

---

### Group B — Migration 060: clean schema (T01)

**Description:** Apply migration 060 to a clean DB with no zombie rows. Verify:
- Migration applies without error
- Trigger `needs_info_question_text_trg` is installed on `dispatch_state_current`
- Zero `cancelled` events emitted with `actor='migration_060'`

**RED state:** Migration file `scripts/migrations/060_needs_info_question_text_guard.sql` does not exist — `FileNotFoundError`.

---

### Group C — Migration 060: single zombie (T02)

**Description:** Insert one zombie row (job in `needs_info` state, originating event has no `question_text` or `needs_info_path`). Apply migration 060. Verify:
- Exactly one `cancelled` event inserted with `actor='migration_060'`
- `dispatch_state_current.state` for that job_id = `'cancelled'`
- Event `event_data` contains `{"reason": "zombie_needs_info_cleanup", "source": "migration_060"}`

**RED state:** Migration file missing.

---

### Group D — Migration 060: multiple zombies (T03)

**Description:** Insert three zombie rows. Apply migration 060. Verify:
- Three `cancelled` events emitted (one per zombie)
- All three jobs transition to `cancelled` state

**RED state:** Migration file missing.

---

### Group E — Post-migration trigger blocks bad INSERT (T04)

**Description:** After migration 060 is applied, attempt to INSERT a `needs_info` event with no `question_text` or `needs_info_path` via `dispatch_v2_events`. The `dispatch_state_apply` trigger fires → INSERTs into `dispatch_state_current` → our BEFORE trigger fires → raises exception. Verify asyncpg surfaces a `PostgresError` (SQLSTATE P0001).

**Setup:** Job in leased state (enqueued → leased event + lease row).

**RED state:** Migration file missing / trigger not installed.

---

### Group F — Post-migration trigger blocks UPDATE to needs_info (T05)

**Description:** After migration 060, directly UPDATE `dispatch_state_current` to set `state='needs_info'` for a job whose latest event in `dispatch_v2_events` lacks both keys. The BEFORE UPDATE trigger fires and raises exception.

**RED state:** Migration file missing / trigger not installed.

---

### Group G — Legitimate needs_info passes trigger (T06)

**Description:** After migration 060, INSERT a `needs_info` event with `question_text` present. The trigger looks up the event → finds `question_text` → allows INSERT to proceed. State transitions to `needs_info` successfully.

**RED state:** Migration file missing.

---

### Group H — Idempotency (T07)

**Description:** Apply migration 060 twice (call the SQL script twice on the same connection). Verify:
- No error raised on second application
- Trigger count in `pg_trigger` is exactly 1 (not doubled)
- Zombie cleanup emits no extra events on second run

**RED state:** Migration file missing.

---

### Group I — Q2 regression (T08)

**Description:** Apply migration 060 on top of the standard Q2 fixture (050+051+056). Run a basic claim-next cycle (enqueue → claim → transition to submitted) to verify migration 060 does not break existing operations.

**RED state:** Migration file missing.

---

## Failure Mode Map

| Test ID | Failure when Phase 8 absent | Failure signal |
|---------|----------------------------|----------------|
| T09 | PASS (valid path always works) | — |
| T10 | PASS (valid path always works) | — |
| T11 | FAIL — no exception raised | `pytest.raises` context exits without exception |
| T12 | FAIL — no exception raised | `pytest.raises` context exits without exception |
| T01–T08 | FAIL — migration file not found | `FileNotFoundError` on `Path.read_text()` |

## Acceptance Criteria Coverage

| AC | Test(s) |
|----|---------|
| SC-1: Service guard rejects missing question_text/needs_info_path | T11, T12 |
| SC-2: Guard placement — existing callers unaffected | T09, T10 |
| SC-3: Migration cleanup emits cancelled events | T02, T03 |
| SC-4: DB trigger blocks bad INSERT/UPDATE | T04, T05 |
| SC-5: Migration idempotent | T07 |
| SC-6: Service guard test matrix | T09, T10, T11, T12 |
| SC-7: Migration test matrix | T01–T08 |
| SC-8: Zombie count validated | T02 (single), T03 (multi) |

## Conventions

- `@_pg_skip` marks all DB tests — skipped cleanly when PostgreSQL is unreachable
- Migration fixture (`raw_conn_060`) applies 050 → 051 → 056 → 059 → then 060 per test
- Service-layer fixture (`raw_conn`) applies 050 → 051 → 056 (no 060) to isolate guard test
- All DB tests are `@pytest.mark.asyncio`
- RED/GREEN state documented per test; T09/T10 start GREEN intentionally
