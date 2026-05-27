# STORY-765 — Phase 8 Implementation Report

## Summary

| Field | Value |
|-------|-------|
| Story | STORY-765 — Allow MANAGER Role to Cancel/Re-Enqueue Mark-Dispatched Stories |
| Scope | Small |
| Tests | 14/14 GREEN (0.89s) |
| PR | #223 — `feat(STORY-765): allow MANAGER role to cancel mark-dispatched stories with structured reason` |
| Branch | `story-765/story-765` |

## Changes Made

### `tech_dev_agents/ops_console/routes/dispatch.py`

- Added `_REASON_CONTENT_RE` regex constant (matches `STORY-\d+`, ISO date, or word-boundary `deployed|fix|shipped|requeue` tokens — word boundaries prevent false positives like "prefix"→"fix")
- Added `_send_override_dm()` async helper — fire-and-forget Teams DM to Mark; failure logged but never blocks cancel
- Modified cancel-queue role gate (previously line 800–812): MANAGER + reason ≥ 30 chars + content-guard pass → allowed; MANAGER with short/absent reason → 403; MANAGER with bad content → 422
- After `db_svc.cancel()` commits: emits `manager_override` audit event to `dispatch_events` via `emit_event()` (route-level, not db-service-level — see Design Decisions); emit is wrapped in try/except so a DB hiccup on the audit write logs but does not surface as a 500 after the cancel already committed
- Added `[DISPATCH] cancel STORY-N by <role>: <reason>` stdout log on manager override (Loki visibility, AC-8)
- ADMIN bypass: ADMIN callers skip the content guard entirely (can pass any reason ≥ 10 chars — unchanged from existing behaviour)

### `tech_dev_agents/ops_console/services/dispatch_db_service.py`

- **Not modified.** Audit event is emitted at the route layer (see above), not inside `cancel()`. This matches the existing `cancelled` event pattern (also emitted from the route) and avoids threading extra params through the service layer.

### `tests/ops_console/test_dispatch_route_manager_override.py` (new file)

14 tests across 7 groups — see `test-design.md` for full matrix.

Post-review improvements (STORY-791 follow-up dispatch):
- **T05** strengthened: now patches and asserts `_send_override_dm` called once with correct `story_id` and `reason` args (previously only checked `hasattr`)
- **T07** strengthened: injects real ADMIN key via `admin_role_api_key` in Settings; asserts 200 (ADMIN bypasses content guard), not the previous catch-all `in (200, 403, 422)`
- **T12** corrected: truncation cap aligned to 500 (matching route `max_length=500`); test sends and asserts on a 500-char reason instead of the previous unreachable 1000-char ceiling
- **Regex** fixed: `\b(?:deployed|fix|shipped|requeue)\b` with word boundaries + `requeue` added (Morris review HIGH findings)

## Acceptance Criteria Status

| AC | Status | Notes |
|----|--------|-------|
| AC-1 Route signature unchanged, role check liberalised | ✅ | `?reason=` param preserved; gate widened for MANAGER |
| AC-2 Reason ≥ 30 chars for MANAGER override | ✅ | T02 asserts 403 on short reason |
| AC-3 Content guard: STORY-N / date / fix-token | ✅ | T03, T08, T09 cover all branches |
| AC-4 `manager_override` event_type in dispatch_events | ✅ | String literal (matches existing pattern; no enum migration needed) |
| AC-5 Teams DM via send_message; failure doesn't roll back | ✅ | T05, T10 |
| AC-6 ADMIN-only paths preserved | ✅ | T06 confirms `/fail` still ADMIN-only |
| AC-7 Zero regressions | ✅ | T13, T14 + full dispatch test area passes |
| AC-8 `[DISPATCH]` stdout log | ✅ | T11 |
| AC-9 PR body includes requeue-failed example | ✅ | PR #223 body has example curl |
| AC-10 DM failure logged, cancel still 200 | ✅ | T10 |

## Design Decisions

1. **`dispatch_events.py` not modified** — `manager_override` used as a string literal in dispatch.py. The seed allows "or string literal — match existing pattern"; no enum migration needed.

2. **Audit event at route layer, not db-service layer** — `emit_event("manager_override")` is called in the route handler after `db_svc.cancel()` returns, matching the existing `cancelled` event pattern. `dispatch_db_service.cancel()` is not modified. This keeps service-layer concerns (DB transitions) separate from audit/notification concerns (events, DMs, logs).

3. **Audit emit wrapped in try/except** — If the `dispatch_events` write fails, the cancel already committed. A 500 at this point would mislead the caller into thinking the cancel failed. The failure is logged to Loki at WARNING level so operators can detect and replay the missing audit row. This matches the `_send_override_dm` error-handling contract (AC-10).

4. **ADMIN content guard bypass** — ADMIN can cancel mark-dispatched with any reason ≥ 10 chars (unchanged from pre-story). Only MANAGER is subject to the 30-char + content guard requirement. Documented in code comment.

5. **Per-cancel DM** — 1 DM per cancel for v1. Batch summary deferred to follow-up (see seed § Boundaries).

6. **DM never blocks cancel** — `_send_override_dm` wrapped in try/except; any failure logs and the 200 response proceeds.

7. **Regex word boundaries** — `\b(?:deployed|fix|shipped|requeue)\b` prevents false matches where "fix" matches inside "prefix", "fixture", "affixed" etc. `requeue` added as Morris's primary use-case naturally writes it.

8. **Reason truncation aligned to route max_length** — audit payload uses `reason[:500]` matching the route's `max_length=500`. The previous `[:1000]` was unreachable.

## Verification

```
$ pytest tests/ops_console/test_dispatch_route_manager_override.py -v
14 passed in 0.89s

$ pytest tests/ops_console/test_dispatch_cancel.py tests/ops_console/test_dispatch_service.py tests/ops_console/test_dispatch_db_service.py -q
24 passed, 26 skipped in 0.67s
```

## Follow-ups

- STORY-766 — Morris fleet-vigilance post-merge deploy + re-enqueue sweep (unblocked by this story)
- STORY-768 — Consider extending manager-override to `/fail` endpoint (out of scope here)
- Batch cancel DM (one summary DM when N stories cancelled in one pass) — v2
