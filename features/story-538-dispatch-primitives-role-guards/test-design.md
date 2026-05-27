# Test Design — STORY-538: Dispatch Primitives + Role Guards

**Scope:** small
**Coverage target:** 50%
**RED state:** 36 FAIL / 14 structural PASS (0 import errors)

## Test Files

```
tests/
├── ops_console/
│   ├── test_dispatch_release.py          # Fix #1: Release primitive
│   └── test_dispatch_next_role_guard.py  # Fix #2: Role-aware /dispatch/next
└── deployment/
    └── test_poller_rate_limit_recovery.py # Fixes #3–6: Probe removal, 429 recovery, session cap
```

## Test Summary

| File | Group | Tests | Fail | Pass | Description |
|------|-------|-------|------|------|-------------|
| test_dispatch_release.py | A (service) | 5 | 5 | 0 | DispatchDBService.release() |
| test_dispatch_release.py | B (route) | 7 | 6 | 1 | POST /dispatch/release/{story_id} |
| test_dispatch_release.py | C (boundary) | 4 | 0 | 4 | Edge cases / invariants |
| test_dispatch_next_role_guard.py | A (registration) | 2 | 2 | 0 | register_agent(role=) |
| test_dispatch_next_role_guard.py | B (next guard) | 4 | 2 | 2 | /dispatch/next role filtering |
| test_dispatch_next_role_guard.py | C (target_role) | 3 | 3 | 0 | DispatchItem.target_role |
| test_dispatch_next_role_guard.py | D (boundary) | 5 | 3 | 2 | ManagerClaimForbiddenError, claimed_by_role |
| test_poller_rate_limit_recovery.py | A (probe removal) | 3 | 3 | 0 | ccusage + claude -p probe deleted |
| test_poller_rate_limit_recovery.py | B (429 recovery) | 5 | 2 | 3 | Runtime rate-limit handling |
| test_poller_rate_limit_recovery.py | C (env failure) | 3 | 2 | 1 | Release-and-idle (Devon's bug) |
| test_poller_rate_limit_recovery.py | D (Morris) | 3 | 3 | 0 | AGENT_ROLE env, service file |
| test_poller_rate_limit_recovery.py | E (session cap) | 4 | 3 | 1 | _DAILY_SESSION_CAP removal |
| test_poller_rate_limit_recovery.py | F (systemctl) | 2 | 2 | 0 | No systemctl disable |
| **Total** | | **50** | **36** | **14** | |

## Acceptance Diff Coverage

| Must-contain token | File | Test |
|--------------------|------|------|
| `def test_release_claimed_story_moves_to_pending` | test_dispatch_release.py | B1 |
| `def test_release_preserves_enqueue_metadata` | test_dispatch_release.py | B2 |
| `def test_manager_role_never_receives_developer_story` | test_dispatch_next_role_guard.py | B1 |
| `def test_developer_role_still_receives_developer_story` | test_dispatch_next_role_guard.py | B2 |
| `def test_poller_survives_429_and_recovers_on_reset` | test_poller_rate_limit_recovery.py | B1 |
| `def test_poller_never_calls_systemctl_disable` | test_poller_rate_limit_recovery.py | C2 |

## Fix-to-Test Mapping

### Fix #1 — Release primitive
- **A1–A5:** Service layer `release()` method: claimed→pending, InvalidTransitionError for pending/completed/cancelled, NotFoundError for missing, SQL structure
- **B1–B7:** Route POST /dispatch/release/{story_id}: 200/409/404 status codes, metadata preservation, no role restriction
- **C1–C4:** Terminal state invariants, no retry tag, claimed_at cleared

### Fix #2 — Role-aware /dispatch/next
- **A1–A2:** register_agent() accepts role param, defaults to 'developer'
- **B1–B4:** Manager gets 204 for developer stories, developer gets 200, X-Agent-Role header, server-side role authoritative
- **C1–C3:** DispatchItem.target_role field, default='developer', DispatchRequest accepts it
- **D1–D5:** ManagerClaimForbiddenError, claimed_by_role reference, empty queue edge cases

### Fix #3 — Delete pre-claim probe
- **A1–A3:** ccusage not in source, claude -p probe not in source, no residual comments

### Fix #4 — Release-and-idle on environmental failure
- **C1–C3:** Calls /release not /fail on 429, no systemctl disable, poller stays alive

### Fix #5 — Morris poller disabled by default
- **D1–D3:** AGENT_ROLE env in service file, poller reads it, sends X-Agent-Role header

### Fix #6 — Session cap removal
- **E1–E4:** _DAILY_SESSION_CAP gone, _daily_session_count gone, cap gate gone, "Session ceiling" string gone

## LLM Error-Prone Coverage

| Category | Tests |
|----------|-------|
| Boundary conditions | A2 (pending→release=409), A3 (completed→release=409), A4 (missing=404) |
| Edge cases | C1–C4 (terminal states, retry tag, timestamp clearing) |
| Output format | B2 (metadata preservation after release) |
| Security | B7 (release has no role restriction), D3–D5 (ManagerClaimForbiddenError) |

## Defensive Gates

### Gate 1: Null/None Boundary — covered by A4 (missing story_id)
### Gate 4: Input Validation — covered by A2–A3 (invalid state transitions)

## Notes for Phase 8

1. Add `release()` method to `DispatchDBService` — SQL: `UPDATE dispatch_items SET status='pending', claimed_by=NULL, claimed_at=NULL, updated_at=now() WHERE story_id=$1 AND status='claimed' RETURNING *`
2. Add `POST /dispatch/release/{story_id}` route — no role restriction (no `Depends(require_role(...))`)
3. Add `ManagerClaimForbiddenError` to dispatch_db_service.py
4. Add `role` param to `register_agent()`, add `claimed_by_role` to dispatch routes
5. Add `target_role` field to DispatchItem + DispatchRequest models (default='developer')
6. Delete ccusage block and claude -p probe from poll_once()
7. Delete systemctl disable call from poll_once()
8. Add `AGENT_ROLE` env var reading + X-Agent-Role header sending to dispatch_poller.py
9. Add `/dispatch/release/` call path for 429 recovery in dispatch_poller.py
10. Delete `_DAILY_SESSION_CAP`, `_daily_session_count`, and session cap gate from sdlc_phase_runner.py
11. Add `Environment=AGENT_ROLE=` to dispatch-poller.service
