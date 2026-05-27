# STORY-765 — Allow MANAGER Role to Cancel/Re-Enqueue Mark-Dispatched Stories With Structured Reason

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Manager-override cancel for Mark-dispatched stories |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Unblocks | STORY-766 (Morris fleet-vigilance post-merge deploy + re-enqueue sweep) |

## Problem Statement

`tech_dev_agents/ops_console/routes/dispatch.py:777-793` returns HTTP 403 to MANAGER role on `DELETE /api/dispatch/queue/{story_id}` when the story was enqueued by `mark`:

```
"detail": "STORY-N was enqueued by 'mark' — only ADMIN role can cancel
Mark-dispatched stories. MANAGER role can cancel agent/retry-wrapper
stories only."
```

This was a safety guardrail (date unknown — looks pre-STORY-639). It now blocks Morris from being the autonomous shepherd. Concrete impact 2026-04-29:
- 11 api-retail-target stories failed at retry-cap due to STORY-759's hardcoded-`main` bug.
- Morris correctly classified them as systemic and proposed re-enqueue after the fix shipped.
- API rejected him: 403, manager can't cancel Mark-dispatched.
- Mark had to hand-do it. Same exposure exists on every Mark-dispatched story batch failure.

The guardrail was correct *when Morris was unsupervised early*; it's wrong now. Morris has structured cron, dispatch_events audit trail, Teams DM transparency, and a defined orchestrator role (STORY-724). The blocker is the wrong default.

## Target User / Use Case

**User:** Morris (MANAGER role) running fleet-vigilance heartbeat skill or `requeue-failed` skill autonomously.
**Today:** any cancel/re-enqueue of Mark-dispatched failures requires Mark to act manually, even when the corrective action is mechanical.
**After this story:** MANAGER can cancel + re-enqueue Mark-dispatched stories IF the request includes a structured `reason` ≥ 30 chars naming the blocking issue. Every override is logged to `dispatch_events` as `manager_override` with the reason. Mark gets a Teams DM summary on every override. ADMIN-only paths (e.g., bulk delete, force-claim by another agent, role mutation) remain ADMIN-only.

## Success Criteria

1. **SC-1 — MANAGER can cancel Mark-dispatched with reason ≥ 30 chars.** `DELETE /api/dispatch/queue/{story_id}?reason=...` accepts MANAGER role when `len(reason) >= 30`. Returns 403 if reason < 30 chars OR missing.
2. **SC-2 — Reason content quality.** Reason MUST contain a STORY-N reference (`STORY-\d+`) OR a specific date (e.g., `2026-04-29`) OR an explicit fix reference (e.g., `STORY-759 deployed`). Reject single-word reasons (e.g., `"stale"`) — already covered by min 30 chars but document the rule for clarity.
3. **SC-3 — Audit log on override.** Every successful manager-override cancel writes a row to `dispatch_events` with `event_type='manager_override'`, the canceller's role, the structured reason, and the prior story state.
4. **SC-4 — Teams DM on override.** When a manager-override cancel succeeds, post a Teams DM to Mark via the existing `mcp__agent-ops__send_message` plumbing: `[MANAGER OVERRIDE] STORY-N cancelled by <role> — reason: <truncated 200 chars>`. Single-message-per-cancel; if Morris cancels a batch of 11, send 11 messages (or one summary message if all share a reason — implementer's call documented in design).
5. **SC-5 — ADMIN-only paths preserved.** Anything beyond cancel-with-reason (force-fail, bulk delete, role mutation) still requires ADMIN. Specifically: `/dispatch/{story_id}/fail` (destructive) and any new admin-gated routes stay ADMIN.
6. **SC-6 — Re-enqueue path is implicitly enabled.** After cancel, Morris can POST a fresh `/api/dispatch` for the same story_id (the unique-active index allows it because the row is now in terminal `cancelled` state). No additional API change needed for re-enqueue — only cancel.
7. **SC-7 — Zero regressions.** Existing role tests pass. ADMIN can still do everything they could.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/ops_console/test_dispatch_route_manager_override.py::test_manager_cancel_mark_dispatched_with_reason -v` | PASSED |
| SC-1 | `pytest tests/ops_console/test_dispatch_route_manager_override.py::test_manager_cancel_short_reason_403 -v` | PASSED |
| SC-2 | `pytest tests/ops_console/test_dispatch_route_manager_override.py::test_reason_must_have_story_ref_or_date_or_fix -v` | PASSED |
| SC-3 | `pytest tests/ops_console/test_dispatch_route_manager_override.py::test_audit_event_written_on_override -v` | PASSED |
| SC-4 | `pytest tests/ops_console/test_dispatch_route_manager_override.py::test_teams_dm_sent_on_override -v` (mock the messenger) | PASSED |
| SC-5 | `pytest tests/ops_console/test_dispatch_route_manager_override.py::test_admin_only_routes_still_admin_gated -v` | PASSED |
| SC-7 | `pytest tests/ -x --ignore=tests/e2e -q` | All pass; zero regressions |

## Test Criteria

- **Tests use FastAPI TestClient** with role-injected request fixtures (existing pattern in `tests/ops_console/`).
- **Audit-event test** asserts a row was inserted into `dispatch_events` with the expected fields.
- **Teams DM test** mocks the messenger and asserts the call was made with the expected payload.
- **Negative tests**: short reason → 403; missing reason → 403; ADMIN endpoints with MANAGER role → 403.
- All tests deterministic, < 2 seconds total.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/ops_console/test_dispatch_route_manager_override.py -v` | All ≥ 6 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN |
| 3 | After deploy: Morris autonomously cancels a stuck Mark-dispatched failed story via `requeue-failed` skill; audit log has the override event; Teams DM arrives | Documented in PR body — observed cancel → DM round trip |
| 4 | Negative-case demo: try cancel with `reason=stale` (5 chars) → returns 403 | Documented in PR body |

## Acceptance Criteria

- [ ] AC-1: Cancel route signature unchanged (still accepts `reason` query param), only the role check inside is liberalised.
- [ ] AC-2: Reason ≥ 30 chars enforced (existing `min_length=10` becomes `min_length=30` for the manager-override path; ADMIN can still pass shorter for ad-hoc admin reasons — implementer decides; document the choice).
- [ ] AC-3: Reason content guard: regex-match a STORY-N OR date OR fix-token. Reject otherwise with a clear 422 message.
- [ ] AC-4: New `dispatch_events.event_type='manager_override'` enum value (or string literal — match existing pattern).
- [ ] AC-5: Teams DM uses `mcp__agent-ops__send_message` to Mark's chat. Failure to send DM does NOT roll back the cancel (DM is informational; cancel succeeded).
- [ ] AC-6: ADMIN-only paths (e.g., `/dispatch/{id}/fail`) remain ADMIN-gated. Test asserts MANAGER hits them with 403.
- [ ] AC-7: Existing tests pass; zero regressions.
- [ ] AC-8: Logging — every cancel logs to stdout: `[DISPATCH] cancel STORY-N by <role>: <reason truncated>` so visible in Loki.
- [ ] AC-9: PR description includes a specific example of an override request the future requeue-failed skill would make.
- [ ] AC-10: Error/logging AC — when DM send fails, the failure is logged but the cancel response is still 200 (do not penalise the user for an infra outage).

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal — single route auth change + tests + audit hook |
| Timeline | URGENT — directly unblocks STORY-766 fleet-vigilance |
| Tech | Python 3.12, FastAPI, asyncpg; no new deps |

## Security Constraints
- [ ] **The manager-override is itself audited.** Every override writes to `dispatch_events`. Mark can revoke MANAGER credentials and inspect the trail.
- [ ] No new endpoints; no auth surface widened to anonymous.
- [ ] Reason field MUST NOT contain credentials, tokens, or PII (truncated to 500 chars in DM and 1000 chars in audit log).
- [ ] Rate limit consideration: bulk cancel storms — out of scope for v1, but log structured rate so operators can detect via Loki.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Enforce reason ≥ 30 chars + content guard for MANAGER | Whether to also accept manager-override on `/fail` (current scope: cancel only) | Allow MANAGER to bypass the reason check |
| Write `manager_override` event to `dispatch_events` | Whether DM should be batched when N stories cancelled in same call (current scope: per-story DM is fine for v1) | Skip the audit log even if DM send fails |
| Preserve all ADMIN-only routes as ADMIN-only | Whether to widen permissions further (e.g., re-claim for another agent) — out of scope, file follow-up | Hardcode role names; use the existing role enum |
| Log every override to stdout (Loki visibility) | Whether to expose a list of recent overrides on the dashboard | Strip the reason from logs (it's the audit trail) |

## Files to Modify

- `tech_dev_agents/ops_console/routes/dispatch.py` — line 774-793 cancel route auth; add reason content guard.
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — extend `cancel()` (or wrap caller) to write `manager_override` audit event.
- `tech_dev_agents/ops_console/services/dispatch_events.py` — add `manager_override` event_type constant.
- `tests/ops_console/test_dispatch_route_manager_override.py` — **new file**, ≥ 6 tests.
- `features/story-765-manager-cancel-mark-dispatched/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- `routes/dispatch.py` ADMIN-only routes (force-fail, bulk delete) — preserve.
- `tech_dev_agents/ops_console/auth/` (role middleware) — out of scope.
- Frontend / dashboard — out of scope.
- Database migrations for new event_type — use string literal if no enum migration needed (check existing pattern).

## Done Looks Like

```
$ pytest tests/ops_console/test_dispatch_route_manager_override.py -v
============================= test session starts ==============================
test_manager_cancel_mark_dispatched_with_reason PASSED
test_manager_cancel_short_reason_403 PASSED
test_reason_must_have_story_ref_or_date_or_fix PASSED
test_audit_event_written_on_override PASSED
test_teams_dm_sent_on_override PASSED
test_admin_only_routes_still_admin_gated PASSED
============================== 6 passed in 0.52s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# Operationally:
$ curl -X DELETE \
  "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue/STORY-007?reason=STORY-759%20deployed%202026-04-29%2C%20re-enqueueing%2011%20stuck%20stories" \
  -H "X-API-Key: <morris-key>"
{"story_id":"STORY-007","status":"cancelled","manager_override":true}

# Audit trail:
$ psql -c "SELECT story_id, event_type, payload FROM dispatch_events WHERE event_type='manager_override' ORDER BY ts DESC LIMIT 5;"
# Shows the override row.

# Teams DM:
[MANAGER OVERRIDE] STORY-007 cancelled by morris — reason: STORY-759 deployed 2026-04-29, re-enqueueing 11 stuck stories
```

## Escalation Contract

1. **Reason content guard is too strict** (e.g., legitimate cancels rejected) → that's a tuning issue; loosen with care, document examples.
2. **Teams DM throughput becomes a problem** in mass-cancel scenarios → batch into one summary DM per N cancels OR rate-limit. Out of scope for v1; file follow-up.
3. **MANAGER attempts to use override for stories already in terminal state** (cancelled/completed/failed) → return 409 same as today.
4. **Audit event write fails** (DB hiccup) → roll back the cancel, return 500, log the failure. Don't silently succeed.
5. **DM send fails** → still succeed the cancel, log the DM failure to stderr.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `routes/dispatch.py`, `services/dispatch_db_service.py`, `services/dispatch_events.py` |
| Reference incident | 2026-04-29 11-story batch — Mark had to hand-cancel because Morris's `requeue-failed` got 403 |
| Architecture | FastAPI route + role middleware + asyncpg + dispatch_events append-only log + Teams MCP messenger |
| Test pattern | Existing `tests/ops_console/test_dispatch_*.py` files use TestClient + injected role fixtures |

## Out of Scope

- Widening permissions for `/fail` or other destructive routes (file STORY-768 if motivated).
- Bulk-cancel API (one-call cancels N stories) — v1 is per-story.
- Dashboard widget showing recent overrides.

## Notes for Implementer

- Existing reason validation: `min_length=10`. Change to 30 for MANAGER. ADMIN can keep 10 (or adopt 30 — implementer decides; document choice).
- The 2026-04-22 Mark precedent: `routes/dispatch.py` already has a comment about audit-able cancel reasons. This story is the logical extension — same audit discipline, lower role barrier.
- STORY-639 / STORY-639 work introduced cancel for `claimed/in_review/needs_info/paused` source states. This story's MANAGER override applies to all of them.
