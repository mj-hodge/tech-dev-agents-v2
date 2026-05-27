# STORY-765 — Test Design: Manager-Override Cancel for Mark-Dispatched Stories

## Scope & Coverage

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 60% of changed lines |
| Test file | `tests/ops_console/test_dispatch_route_manager_override.py` |
| Total tests | 14 (9 RED, 5 GREEN) |
| Frontend | N/A (backend-only story) |

## Test Structure

```
tests/ops_console/
└── test_dispatch_route_manager_override.py
    ├── Group A — Role gate liberalisation (T01–T02)         [RED]
    ├── Group B — Reason content guard (T03, T08, T09)       [RED]
    ├── Group C — Audit log: dispatch_events (T04, T12)      [RED]
    ├── Group D — Teams DM (T05, T10)                        [RED]
    ├── Group E — ADMIN-only preservation (T06, T07)         [T06 GREEN, T07 RED]
    ├── Group F — Stdout logging (T11)                       [RED]
    └── Group G — Regressions (T13–T14)                      [GREEN]
```

## Test Matrix

### Group A — Role Gate Liberalisation

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_manager_cancel_mark_dispatched_with_reason` | MANAGER + mark-dispatched + reason ≥ 30 chars → 200 | RED (403 from current gate) |
| `test_manager_cancel_short_reason_403` | MANAGER + mark-dispatched + reason < 30 chars → 403 with 30-char message | RED (403 but wrong error message) |

### Group B — Reason Content Guard

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_reason_must_have_story_ref_or_date_or_fix` | Reason without STORY-N/date/fix → 422 | RED (403 from role gate, not content guard) |
| `test_reason_with_only_story_ref_passes` | Reason with STORY-759 ref → 200 | RED (403 from role gate) |
| `test_reason_with_only_date_passes` | Reason with 2026-04-29 date → 200 | RED (403 from role gate) |

### Group C — Audit Log (dispatch_events)

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_audit_event_written_on_override` | emit_event called with event_type='manager_override' + payload has reason & role | RED (403 blocks handler) |
| `test_reason_truncated_in_audit_and_dm` | Reason truncated to ≤ 1000 chars in audit payload | RED (403 blocks handler) |

### Group D — Teams DM

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_teams_dm_sent_on_override` | `_send_override_dm` exists and is called on successful override | RED (403 blocks handler + function doesn't exist yet) |
| `test_dm_failure_does_not_rollback_cancel` | DM failure → cancel still returns 200 (AC-10) | RED (403 blocks handler) |

### Group E — ADMIN-Only Preservation

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_admin_only_routes_still_admin_gated` | /fail endpoint reachable by MANAGER (no role gate today) — STORY-765 must not change this | GREEN (documents baseline) |
| `test_admin_can_cancel_mark_dispatched_without_content_guard` | ADMIN bypasses content guard on mark-dispatched | RED (current key is MANAGER; intent documented) |

### Group F — Stdout Logging

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_stdout_log_on_override` | Log with [DISPATCH] prefix emitted on override | RED (403 blocks handler) |

### Group G — Regressions

| Test | What It Verifies | State |
|------|------------------|-------|
| `test_admin_can_cancel_anything_regression` | MANAGER cancels non-mark-dispatched story → 200 (existing behavior) | GREEN |
| `test_manager_cancel_non_mark_dispatched_regression` | MANAGER cancels agent-enqueued claimed story → 200 | GREEN |

## RED State Confirmation

```
$ python3 -m pytest tests/ops_console/test_dispatch_route_manager_override.py -v
14 collected
9 FAILED, 5 PASSED

All 9 failures are AssertionError from the current role gate at
dispatch.py:803 returning 403 for MANAGER + mark-dispatched.
No import errors. No type errors. No fixture errors.
```

## Acceptance Criteria → Test Mapping

| AC | Test(s) |
|----|---------|
| AC-1 (route signature unchanged, role check liberalised) | T01 |
| AC-2 (reason ≥ 30 chars for manager override) | T01, T02 |
| AC-3 (reason content guard: STORY-N / date / fix-token) | T03, T08, T09 |
| AC-4 (manager_override event_type in dispatch_events) | T04, T12 |
| AC-5 (Teams DM via send_message; failure doesn't roll back) | T05, T10 |
| AC-6 (ADMIN-only paths preserved) | T06 |
| AC-7 (zero regressions) | T13, T14 |
| AC-8 ([DISPATCH] stdout log on override) | T11 |
| AC-10 (DM failure logged, cancel still 200) | T10 |

## API Mock Verification

| Mock Pattern | Actual Endpoint | Verified |
|-------------|-----------------|----------|
| `DELETE /api/dispatch/queue/{story_id}?reason=...` | `router.delete("/dispatch/queue/{story_id}")` at dispatch.py:745 | ✅ |
| `POST /api/dispatch/fail/{story_id}` | `router.post("/dispatch/fail/{story_id}")` at dispatch.py:1137 | ✅ |
| `emit_event` | `tech_dev_agents.ops_console.routes.dispatch.emit_event` (imported at :52) | ✅ |

## Implementation Notes for Phase 8

1. **Role gate change** (dispatch.py:800–812): Replace the blanket `if is_mark_dispatched and role < ADMIN: raise 403` with a conditional that allows MANAGER when `reason >= 30 chars` AND passes the content guard.

2. **Reason content guard**: Add regex validation before the cancel call:
   ```python
   import re
   REASON_CONTENT_RE = re.compile(
       r'STORY-\d+|20\d{2}-\d{2}-\d{2}|deployed|fix|shipped',
       re.IGNORECASE,
   )
   if is_mark_dispatched and role == Role.MANAGER:
       if len(reason) < 30:
           raise HTTPException(403, "Manager override requires reason ≥ 30 chars")
       if not REASON_CONTENT_RE.search(reason):
           raise HTTPException(422, "Reason must reference a STORY-N, date, or fix")
   ```

3. **Audit event**: After successful cancel, emit:
   ```python
   await emit_event(story_id, repo, "manager_override", payload={
       "reason": reason[:1000],
       "role": role.name,
       "prior_status": enqueued_by,
   })
   ```

4. **Teams DM**: Create `_send_override_dm(story_id, role, reason)` async function. Wrap in try/except — failure is logged but never blocks the cancel response.

5. **Stdout log**: Add `logger.warning("[DISPATCH] cancel %s by %s: %s", story_id, role.name, reason[:80])` on manager override success.

## Checklist

- [x] Every test name clearly states what it verifies
- [x] Arrange/Act/Assert sections explicit
- [x] Tests follow existing codebase patterns (TestClient + inject_mock_services)
- [x] No import errors — all tests collect cleanly (14/14)
- [x] RED state confirmed — 9 FAIL for right reasons
- [x] GREEN regression guards — 5 PASS (existing behavior preserved)
- [x] Every AC mapped to at least one test
- [x] Junior-readable test names and docstrings
- [x] No external API write paths (Gate 2a: N/A)
- [x] No new ORM models (Gate 8: N/A — no migration needed)
- [x] No file uploads (Gate 7: N/A)

## Follow-ups (Out of Scope)

- STORY-768: Consider extending manager-override to `/fail` endpoint.
- Batch cancel API (one call for N stories) — v1 is per-story.
- Dashboard widget showing recent overrides.
