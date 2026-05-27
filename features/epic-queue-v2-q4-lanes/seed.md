# Seed — Epic-Queue-v2 Story Q4: Lane Derivation + Dashboard Adapter

**Story:** Q4 — Lane Derivation in Projection + Dashboard Adapter
**Epic:** Epic-Queue-v2 (Unified Queue Reliability)
**Scope:** Small (0.5 day)
**Phase:** 7 — Test Design (RED state)
**Date:** 2026-05-02

---

## Problem

The v2 dispatch schema (Q1) stores job state as a `lane` column in `dispatch_state_current`. The existing dashboard frontend expects a `DispatchQueueResponse` shape with bucketed lists (`pending`, `in_progress`, `in_review`, `paused`, `needs_info`). Q4 bridges this gap without requiring any frontend rewrite.

## What Q4 Delivers

1. **GET /api/dispatch/v2/queue** endpoint (in `routes/dispatch_v2.py`) — already delivered by Q2.
   - With `?lane=<string>`: returns `{ lane, items: [...] }` filtered from `dispatch_state_current.lane`.
   - Without lane param: returns the bucketed `DispatchQueueResponse`-compatible shape.

2. **TypeScript dashboard adapter** (`frontend/src/api/dispatch.ts`) — new file that wraps the v2 endpoint and translates its output to the existing `DispatchQueueResponse` type consumed by `useDispatchQueue.ts`.

## Lane Mapping (v2 → DispatchQueueResponse)

| v2 lane               | DispatchQueueResponse bucket |
|-----------------------|------------------------------|
| `work_queue`          | `pending`                    |
| `in_progress`         | `in_progress`                |
| `in_review`           | `in_review`                  |
| `human_queue`         | `needs_info`                 |
| `attention_queue`     | `attention` / `paused`       |
| `quarantined`         | `paused`                     |
| `terminal`            | omitted                      |

`needs_info_kind = 'question'` → `human_queue` → `needs_info` bucket.
`needs_info_kind = 'attention'` → `attention_queue` → `paused` bucket.

## Acceptance Criteria (from spec)

- **AC1:** Adapter output shape == today's `DispatchQueueResponse` shape (snapshot test).
- **AC2:** Adding a new lane value (e.g., `canary_queue`) requires no code changes — verified via dynamic lane query test.
- **AC3:** Frontend renders queue without code changes (TypeScript type test or snapshot).

## Dependencies

- Q1 must be merged (schema + `dispatch_state_current` table must exist).
- Q2 must be merged (`/api/dispatch/v2/queue` endpoint + `list_queue` service method must exist).
- Q4 owns **only** the dashboard adapter layer in `frontend/src/api/dispatch.ts`.
- No route changes are needed if Q2's `/queue` endpoint shape satisfies the adapter contract.

## Files Affected

- `tech_dev_agents/ops_console/routes/dispatch_v2.py` — GET /queue already exists (Q2 delivered)
- `frontend/src/api/dispatch.ts` — new adapter (Q4 delivers)
- `tests/test_epic_queue_v2_q4.py` — RED tests (this phase)
