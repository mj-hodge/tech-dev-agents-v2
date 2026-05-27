# Phase 8b: Code Review — Dispatch Queue Database Persistence & History

**Story:** STORY-028
**Phase:** 8b (Code Review)
**Date:** 2026-04-12
**Reviewer:** Sonnet

---

## Review Summary

**Verdict: APPROVED**

The implementation faithfully follows the feature specification. All 64 tests pass (21 service + 21 route + 13 poller + 9 legacy service). The migration from JSON to PostgreSQL is clean, backward compatible, and well-tested.

---

## Files Reviewed

| File | Lines Changed | Verdict |
|------|:---:|:---:|
| `services/dispatch_db_service.py` | +250 | PASS |
| `routes/dispatch.py` | +230 (rewrite) | PASS |
| `models/responses.py` | +35 | PASS |
| `config.py` | +3 | PASS |
| `main.py` | +10/-5 | PASS |
| `scripts/migrations/001_dispatch_queue.sql` | +48 | PASS |
| `deployment/hermes/dispatch_poller.py` | +40/-5 | PASS |
| `frontend/src/components/DispatchQueue.tsx` | +200 (rewrite) | PASS |
| `frontend/src/hooks/useDispatchQueue.ts` | +15 | PASS |
| `frontend/src/types/api.ts` | +15 | PASS |
| `tests/ops_console/test_dispatch_db_service.py` | +250 | PASS |
| `tests/ops_console/test_routes_dispatch.py` | +280 (rewrite) | PASS |
| `tests/deployment/test_dispatch_poller.py` | +3 | PASS |

---

## Findings

### Finding 1 — Info: Database credentials in test file

**File:** `tests/ops_console/test_dispatch_db_service.py:21`
**Severity:** Info
**Description:** `TEST_DATABASE_URL` contains password in plain text (`ops_console:ops_console`). Acceptable for local test database but should not be used in production.
**Impact:** None — test-only, local database.
**Action:** No change needed.

### Finding 2 — Low: No connection retry on pool creation

**File:** `tech_dev_agents/ops_console/main.py`
**Severity:** Low
**Description:** `asyncpg.create_pool()` in the lifespan does not retry on connection failure. If PostgreSQL is temporarily unavailable at startup, the app will crash.
**Impact:** Low — PostgreSQL runs on the same VM and is systemd-managed. Restart will succeed.
**Action:** Deferred — add retry logic if multi-VM deployment is needed.

### Finding 3 — Low: `DispatchQueueService` import retained

**File:** `tech_dev_agents/ops_console/main.py`
**Severity:** Low
**Description:** The old `DispatchQueueService` is still imported but no longer used in the lifespan.
**Impact:** None — unused import, no runtime effect.
**Action:** Optional cleanup. The JSON service is retained for fallback reference.

### Finding 4 — Info: `xmax = 0` PostgreSQL trick

**File:** `services/dispatch_db_service.py:register_agent()`
**Severity:** Info
**Description:** Uses `(xmax = 0) AS is_new` to detect INSERT vs UPDATE in an upsert. This is a PostgreSQL-specific internal trick.
**Impact:** Works correctly. Well-tested (T16, T17 both pass). Standard pattern in asyncpg projects.
**Action:** No change needed. Comment explains the purpose.

### Finding 5 — Info: History tab key uniqueness

**File:** `frontend/src/components/DispatchQueue.tsx`
**Severity:** Info
**Description:** History row key uses `${item.story_id}-${item.completed_at || item.cancelled_at}` which is unique because the same story can only have one terminal record with a given timestamp.
**Impact:** None — keys are unique in practice.
**Action:** No change needed.

---

## Test Coverage Assessment

| Area | Tests | Coverage |
|------|:---:|:---:|
| Service (enqueue, claim, cancel, complete) | 11 | Full |
| Service (history, stale recovery, agent reg) | 7 | Full |
| Service (migration idempotency) | 1 | Full |
| Service (pending count) | 1 | Full |
| Route (existing STORY-026 endpoints) | 14 | Full |
| Route (new STORY-028 endpoints) | 5 | Full |
| Route (auth required, all endpoints) | 1 | Full |
| Poller (idle, poll, claim, start) | 13 | Full |
| Legacy JSON service | 9 | Full (retained) |
| **Total** | **64** | |

---

## Backward Compatibility Verification

All 14 original STORY-026 route tests pass unchanged (same test names, same assertions). The API response contracts are preserved:
- `DispatchItemResponse` — same shape, new optional fields (`completed_at`, `cancelled_at`)
- `DispatchQueueResponse` — same shape
- `ClaimResponse` — same shape
- `CancelResponse` — same shape
- HTTP status codes — identical (201, 200, 204, 409, 404, 422, 401)

---

## Conclusion

Clean implementation with excellent test coverage. No blocking findings. The 3 low/info findings are documented for future consideration.

**APPROVED — safe to proceed to Phase 11 (Pre-Deploy Gate).**
