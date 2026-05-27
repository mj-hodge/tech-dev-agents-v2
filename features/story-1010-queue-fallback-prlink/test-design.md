# Test Design — STORY-1010: Ops-skill Fallback Registry + PR-link Assertion at Merge Gate

## Scope

Unit tests covering:

1. **Ops-skill fallback registry** — the in-memory `Fallback` dataclass, `register_fallback`,
   `get_fallback`, and `render_fallback` helpers in
   `tech_dev_agents/ops_console/ops_skill_fallback_registry.py`.
2. **PR-number gate** — `transition()` rejecting `in_review` for new dispatches without
   `pr_number`, and `set_pr_number()` atomicity/idempotency in
   `tech_dev_agents/ops_console/services/dispatch_v2_service.py`.
3. **Incident-replay regressions** — REPLAY-3 (skill-down fallback) and STORY-919-class
   (new dispatch blocked without pr_number) in `tests/epic_1000/test_incident_replays.py`.

Medium scope → phases 7 and 8 only. No integration tests against a live database.

---

## Test Files

| File | Tests |
|------|-------|
| `tests/ops_console/test_ops_fallback_registry.py` | Groups A–D (registry) |
| `tests/ops_console/test_dispatch_v2_pr_number_gate.py` | Groups E–G (gate + set-pr-number) |
| `tests/epic_1000/test_incident_replays.py` | Groups H–I (incident replays) |

---

## Group A — Registry: all five skills registered (SC-2)

**A01 — `test_all_five_skills_registered`**

Setup: import `REGISTRY` from `ops_skill_fallback_registry`.

Expected:
- `sorted(REGISTRY.keys()) == ["dead-letter-purge", "dispatch-recovery", "force-claim", "release-stale-claim", "requeue-failed"]`

**A02 — `test_each_entry_is_fallback_dataclass`**

Expected:
- Every value in `REGISTRY` is a `Fallback` instance.
- Each entry has non-empty `skill_name`, `sql_template`, `runbook_url`, `description`.

---

## Group B — Registry: `render_fallback` output (SC-1)

**B01 — `test_requeue_failed_fallback_renders`**

Expected:
- `render_fallback("requeue-failed", repo="my-repo")` returns a non-empty string.
- The string contains `UPDATE dispatch_state_current`.

**B02 — `test_requeue_failed_includes_runbook_url`**

Expected:
- The rendered output contains `runbook_url` as a substring.

**B03 — `test_render_fallback_completes_within_60s`**

Expected:
- `render_fallback("requeue-failed")` completes in under 60 seconds (SC-1 SLA).

**B04 — `test_render_fallback_with_params`**

Setup: Call `render_fallback("requeue-failed", repo="test-repo")`.

Expected:
- The `$repo` placeholder is substituted with `test-repo` in the output.

**B05 — `test_render_fallback_unknown_skill_returns_none`**

Expected:
- `render_fallback("nonexistent-skill")` returns `None`.

---

## Group C — Registry: `get_fallback` lookup

**C01 — `test_get_known_skill`**

Expected:
- `get_fallback("requeue-failed")` returns a `Fallback` with `skill_name == "requeue-failed"`.

**C02 — `test_get_unknown_skill_returns_none`**

Expected:
- `get_fallback("not-a-skill")` returns `None`.

---

## Group D — Registry: SQL template integrity

**D01 — `test_sql_template_contains_sql_keyword` (parametrized over all 5 skills)**

Expected:
- Each skill's `sql_template` contains at least one SQL keyword (`SELECT`, `UPDATE`,
  `DELETE`, `INSERT`).

---

## Group E — PR-number gate: new dispatch blocked (SC-3)

**E01 — `test_new_dispatch_blocked_without_pr_number`**

Setup:
- `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT = 2026-01-01T00:00:00Z` (past, gate active).
- Mock `dispatch_jobs` row: `pr_number=None`, `created_at=now()` (new dispatch).

Expected:
- `transition(job_id, "submitted")` raises `InvalidEventDataError` with message
  containing `"in_review requires pr_number"`.

**E02 — `test_new_dispatch_allowed_with_pr_number`**

Setup:
- Same rollout, same new dispatch, but `pr_number=42` on the job row.

Expected:
- No exception raised; transition proceeds normally.

---

## Group F — PR-number gate: legacy dispatch bypasses gate (SC-4)

**F01 — `test_legacy_dispatch_uses_backfill_sweeper`**

Setup:
- `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT = 2026-05-18T00:00:00Z`.
- Mock `dispatch_jobs` row: `pr_number=None`, `created_at=2026-01-01` (before rollout).

Expected:
- `transition(job_id, "submitted")` does NOT raise; legacy dispatch passes through.

---

## Group G — `set_pr_number`: atomicity and idempotency (SC-5)

**G01 — `test_set_pr_number_first_call_succeeds`**

Setup:
- Mock row: `pr_number=None`.

Expected:
- Returns `{"status": "ok", "pr_number": 123, "idempotent": False}`.
- The UPDATE is executed once with `pr_number=123`.

**G02 — `test_set_pr_number_idempotent`**

Setup:
- Mock row: `pr_number=123` (already set to the same value).

Expected:
- Returns `{"status": "ok", "pr_number": 123, "idempotent": True}`.
- No UPDATE executed.

**G03 — `test_set_pr_number_conflict_returns_409`**

Setup:
- Mock row: `pr_number=42` (different value).
- Call `set_pr_number(job_id, pr_number=99)`.

Expected:
- `PrNumberConflictError` raised (maps to HTTP 409).

**G04 — `test_set_pr_number_uses_for_update` (race-condition guard)**

Setup:
- Capture the SQL string passed to `conn.fetchrow`.

Expected:
- The SELECT query contains `FOR UPDATE` — confirming row-level locking to prevent
  the TOCTOU race (R5 review finding).

---

## Group H — Incident replay: REPLAY-3 (SC-6)

**H01 — `test_replay_3_requeue_failed_fallback_unblocks`**

Scenario: simulate `/requeue-failed` operator skill unavailable.

Setup:
- Do NOT mock the registry (it registers at import time).
- Call `get_fallback("requeue-failed")`.

Expected:
- Returns a `Fallback` with non-empty `sql_template` containing `UPDATE dispatch_state_current`.
- Simulates that operators can unblock the queue without the skill.

**H02 — `test_replay_3_all_five_skills_have_fallbacks`**

Expected:
- `len(REGISTRY) == 5`.
- All five skills are present (confirms full coverage for any skill outage).

---

## Group I — Incident replay: STORY-919 class

**I01 — `test_new_dispatch_without_pr_number_rejected_422`**

Scenario: reproduce the STORY-919 failure class — agent transitions to `in_review`
without stamping `pr_number`.

Setup:
- `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` set to the past.
- New dispatch job with `pr_number=None`, `created_at=now()`.

Expected:
- `transition(job_id, "submitted")` raises `InvalidEventDataError`.
- Error message includes `"in_review requires pr_number"`.
- Confirms the gate prevents the STORY-919 orphaned-review-row class.

---

## State Machine Coverage

```
dispatch_jobs.pr_number
  NULL + new dispatch  → transition("submitted") → InvalidEventDataError (422)
  NULL + legacy job    → transition("submitted") → allowed (backfill sweeper handles)
  NULL (unset)         → set_pr_number(123)      → {"status": "ok", idempotent: False}
  123  (same value)    → set_pr_number(123)      → {"status": "ok", idempotent: True}
  42   (conflict)      → set_pr_number(99)       → PrNumberConflictError (409)
```

---

## Locking / Concurrency

`set_pr_number` uses `SELECT … FOR UPDATE` inside an asyncpg transaction to prevent
two concurrent callers from both seeing `pr_number IS NULL` and both successfully writing.
Group G04 verifies this invariant at the SQL level.

---

## RED / GREEN

Phase 7 (this document): tests written, all RED — the gate and `set_pr_number` do not
yet exist.

Phase 8: implementation added → all tests go GREEN. The `FOR UPDATE` locking fix
(Morris review R5) is verified by G04.
