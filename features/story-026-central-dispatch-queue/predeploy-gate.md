# Pre-Deploy Gate: Central Dispatch Queue

| Field         | Value                                |
|---------------|--------------------------------------|
| **Story**     | STORY-026                            |
| **Phase**     | 11 — Pre-Deploy Gate                 |
| **Date**      | 2026-04-08                           |
| **Reviewer**  | Bot Derrick (automated)              |
| **Branch**    | `story-025/queue-visibility`         |
| **Commit**    | `daa58e5`                            |

---

## 1. Scope of Changes

### Backend
- **`tech_dev_agents/ops_console/services/dispatch_service.py`** — `DispatchQueueService` class: JSON file persistence with `fcntl` advisory locking, atomic writes via `tempfile.mkstemp` + `os.replace`, stale-claim recovery (5-minute threshold).
- **`tech_dev_agents/ops_console/routes/dispatch.py`** — 5 FastAPI endpoints on `APIRouter(dependencies=[Depends(require_auth)])`:
  - `POST /api/dispatch` — enqueue (201, 409 duplicate, 422 full/validation)
  - `GET  /api/dispatch/queue` — list pending + claimed
  - `GET  /api/dispatch/next` — peek oldest pending (204 if empty)
  - `POST /api/dispatch/claim/{story_id}` — claim (409 already claimed, 404 not found)
  - `DELETE /api/dispatch/queue/{story_id}` — cancel pending (409 if claimed, 404 not found)
- **`tech_dev_agents/ops_console/models/responses.py`** — 8 new Pydantic models (`DispatchStatusEnum`, `DispatchRequest`, `DispatchItem`, `DispatchItemResponse`, `DispatchQueueResponse`, `ClaimRequest`, `ClaimResponse`, `CancelResponse`) with Field-level validation (regex patterns, min/max length).

### Frontend
- **`frontend/src/components/DispatchQueue.tsx`** — collapsible table panel (pending + claimed rows, scope badges, cancel action, time-ago display).
- **`frontend/src/hooks/useDispatchQueue.ts`** — React Query hook (`useDispatchQueue`, `useCancelDispatch`).
- **`frontend/src/types/api.ts`** — `DispatchItem` and `DispatchQueueResponse` TypeScript interfaces.

### Tests
- **`tests/ops_console/test_dispatch_service.py`** — 9 unit tests (init, load/save roundtrip, atomic write, corrupt JSON, stale-claim recovery).
- **`tests/ops_console/test_routes_dispatch.py`** — 16 integration tests (enqueue success/duplicate/full/validation, list empty/items/FIFO, next/empty, claim success/double/404, cancel success/claimed/404, auth on all 5 endpoints).

---

## 2. Gate Checklist

### Functional
- [x] **Tests pass (25/25)** — 9 service unit + 16 route integration, all green.
- [x] **No regressions** — 171 existing tests continue to pass.
- [x] **FIFO ordering verified** — T12 asserts enqueue order equals list order.
- [x] **Duplicate protection** — T02 and T16 verify 409 on duplicate enqueue and double-claim.
- [x] **Queue cap enforced** — T03 fills 50 items, confirms 422 on item 51.
- [x] **Stale claim recovery** — T30-T32 verify expiry threshold (300s), fresh retention, and empty-queue no-op.

### TypeScript / Frontend
- [x] **TypeScript clean** — `tsc --noEmit` passes with zero errors.
- [x] **Component handles loading, error, and empty states** — explicit branches in `DispatchQueue.tsx`.
- [x] **Cancel only on pending items** — button conditionally rendered via `item.status === 'pending'`.

### Security
- [x] **Auth on all endpoints** — Router-level `dependencies=[Depends(require_auth)]` applies Entra ID SSO + API key fallback to every dispatch route. T06 explicitly asserts 401 on all 5 endpoints without credentials.
- [x] **No secrets in code** — grep for `password`, `secret`, `token`, `api_key`, `API_KEY` across dispatch service and routes returns zero matches. All credentials sourced from `app.state.settings`.
- [x] **Input validation on all request models** — `DispatchRequest.story_id` regex `^STORY-\d+$`, `repo` min/max length, `scope` regex `^(small|medium|large)$`, `prompt` min/max length, `ClaimRequest.agent_name` regex `^[a-zA-Z0-9_-]+$`. T04 confirms 422 on invalid `story_id`.

### Dependencies & Infrastructure
- [x] **No new dependencies added** — uses only stdlib (`fcntl`, `json`, `tempfile`, `os`) and existing FastAPI/Pydantic. No changes to `pyproject.toml` or `package.json`.
- [x] **File permissions appropriate** — queue file created via `tempfile.mkstemp` (0600 default) + `os.replace` (atomic). Parent directory created with `mkdir(parents=True, exist_ok=True)`. No world-writable paths.
- [x] **Error handling adequate** — `load()` catches `JSONDecodeError` and `OSError`, returns empty queue. `_atomic_write()` cleans up temp files and file descriptors on exception. Stale recovery handles invalid timestamps gracefully.

### Operational Readiness
- [x] **Logging** — INFO on enqueue/claim/cancel, WARNING on stale claim recovery, ERROR on load failure.
- [x] **Queue size bounded** — `MAX_PENDING = 50` hard cap prevents unbounded growth.
- [x] **Concurrency safe** — `fcntl.LOCK_SH` for reads, `fcntl.LOCK_EX` for writes, atomic rename via `os.replace`.
- [x] **No background tasks or cron** — stale recovery is callable but not auto-scheduled (appropriate for Phase 1 scope).

---

## 3. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| `fcntl` not available on Windows | Low | Production target is Linux (Azure VM). Development uses WSL/Linux containers. |
| JSON file I/O under high concurrency | Low | Advisory locking + atomic rename. Queue capped at 50 items. Single-digit concurrent agents expected. |
| No persistence backup / WAL | Low | JSON file is non-critical operational data. Stale-claim recovery provides self-healing. Acceptable for Phase 1. |

---

## 4. Verdict

**PASS**

All 25 tests green, zero regressions on 171 existing tests, TypeScript clean, auth enforced on all endpoints, input validation on all request models, no secrets, no new dependencies, bounded queue, atomic file operations with proper cleanup. Ready to deploy.
