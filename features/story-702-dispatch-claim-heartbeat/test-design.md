# Test Design — STORY-702: Claim Heartbeat + Auto-Release

**Phase Path:** 7 → 8 → Done  
**Scope:** Small  
**Test file:** `tests/deployment/test_dispatch_claim_heartbeat_702.py`

---

## Test Criteria

| ID | Criterion | Type |
|----|-----------|------|
| TC-1 | Migration SQL is idempotent (`IF NOT EXISTS`, `DEFAULT 0`) | Static / unit |
| TC-2 | `POST /dispatch/heartbeat/{id}` updates `claim_heartbeat_at` | Unit (mock DB) |
| TC-3 | Heartbeat endpoint is idempotent — calling 3× raises no error | Unit (mock DB) |
| TC-4 | Phase runner thread emits heartbeats (mock urllib + time) | Unit |
| TC-5 | Poller emits heartbeat when local claim is active | Unit (mock) |
| TC-6 | Reconcile: stale claim (heartbeat 16min ago, count=0) → pending, count=1 | Unit (mock DB) |
| TC-7 | Reconcile: after 3 stale releases → failed (`failure_reason` via SQL escalation) | Unit (mock DB) |
| TC-8 | Reconcile: healthy claim (heartbeat 5min ago) NOT released | Unit (mock DB) |
| TC-9 | Events emitted: heartbeat, stale_release, stale_failed (SQL audit) | Unit (mock DB) |

---

## Design Notes

- All tests are unit-level (no PostgreSQL required). Async tests use `pytest-asyncio`.
- DB layer tested via mock `asyncpg.Pool` / connection with `AsyncMock.fetch`.
- Phase runner heartbeat thread tested by patching `urllib.request.urlopen` and `threading.Event.wait`.
- Poller heartbeat tested by patching `WorkQueue.resume` and `requests.Session.post`.
- Migration idempotency verified by inspecting the SQL string for `IF NOT EXISTS` and `DEFAULT 0`.

---

## RED State

Tests are written before implementation and must FAIL when the feature code does not exist, then PASS after implementation (Phase 8).
