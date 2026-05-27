# Decisions — Epic-Queue-v2 Story Q4: Lane Derivation + Dashboard Adapter

**Story:** Q4 — Lane Derivation in Projection + Dashboard Adapter
**Date:** 2026-05-02

---

## Decision Log

### D1 — Implementation gated on Q2 merge

Implementation is gated on Q2 merge. Q4 owns only the dashboard adapter layer in
`frontend/src/api/dispatch.ts` — no route changes are needed if Q2's /queue endpoint
shape satisfies the adapter contract.

The Python route (`GET /api/dispatch/v2/queue`) already exists in Q2's
`tech_dev_agents/ops_console/routes/dispatch_v2.py`. Q4 does not modify the route.

**Rationale:** Q2 already delivers the lane-as-query endpoint with the bucketed response
structure. Q4 adds the frontend glue and ensures the Python response is augmented with
the `total_pending`, `total_claimed`, and `fetched_at` fields expected by the existing
`DispatchQueueResponse` TypeScript type.

---

### D2 — DispatchQueueResponse shape delta: `attention` bucket merge

Q2's `list_queue()` returns an `attention` bucket that has no direct counterpart in the
existing `DispatchQueueResponse`. The adapter must merge `attention` items into `paused`.

This matches the spec: `needs_info_kind = 'attention'` → `attention_queue` → `paused`
in the adapter shape, consistent with v1's path-prefix re-bucketing behavior.

---

### D3 — TypeScript adapter function strategy

The adapter (`frontend/src/api/dispatch.ts`) will export a pure function
`adaptV2QueueResponse(v2: V2QueueBuckets): DispatchQueueResponse` that:
- Merges `attention` into `paused`.
- Adds `claimed` as an alias for `in_progress`.
- Synthesizes `total_pending = pending.length`.
- Synthesizes `total_claimed = in_progress.length`.
- Synthesizes `fetched_at = new Date().toISOString()`.

The `useDispatchQueue` hook will NOT be changed in Q4 Phase 8 — it continues to call
the v1 endpoint. A separate story (post-Q6 cutover) will switch the hook's endpoint.
This prevents frontend breakage before v2 is fully in production.

---

### D4 — Python response augmentation location

The `total_pending`, `total_claimed`, `fetched_at` fields must be added to
`DispatchV2Service.list_queue()` (not the route). This keeps the route thin and the
service self-contained. The service will inject `fetched_at` using `datetime.utcnow()`.

---

### D5 — No lane enum validation at the route layer

The `lane` query parameter remains a free-form string (`str | None`). This is the core
of AC2: adding `canary_queue` or any other lane requires no server code changes. The
DB query simply passes the string to `WHERE s.lane = $1` and returns whatever matches.

---

### D6 — Test scope: Python tests use mocked service, no DB required

AC1 and AC2 Python tests mock `DispatchV2Service.list_queue()` via `unittest.mock.AsyncMock`.
This means they run in CI without a PostgreSQL instance. The real DB-level lane query
behavior is covered by Q1 and Q2's integration tests.

---

## Open Questions

None at Phase 7 close. Implementation can proceed once Q2 merges.
