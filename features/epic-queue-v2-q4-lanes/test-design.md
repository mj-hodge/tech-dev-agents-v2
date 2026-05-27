# Test Design — Epic-Queue-v2 Story Q4: Lane Derivation + Dashboard Adapter

**Phase:** 7 — Test Design (RED state)
**Story:** Q4 — Lane Derivation in Projection + Dashboard Adapter
**Date:** 2026-05-02
**Test file:** `tests/test_epic_queue_v2_q4.py`

---

## Summary

Q4 has three acceptance criteria. The `/queue` endpoint already exists from Q2. This
phase writes RED tests covering all three ACs. Most tests will be GREEN once Q2 is
merged; the TypeScript adapter test is RED until `frontend/src/api/dispatch.ts` is
created in Phase 8.

---

## Gap Analysis: What Q2 Delivered vs What Q4 Needs

### Q2 deliverables (already in `dispatch_v2.py`):

- `GET /api/dispatch/v2/queue` — route exists, delegates to `svc.list_queue(lane, limit)`.
- `list_queue(lane=None)` — returns bucketed dict with keys `pending`, `in_progress`,
  `in_review`, `paused`, `needs_info`, `attention`.
- `list_queue(lane="some_lane")` — returns `{"lane": "some_lane", "items": [...]}`.

### What Q4 adds:

- Dashboard adapter TypeScript file (`frontend/src/api/dispatch.ts`) that:
  - Calls `/api/dispatch/v2/queue` (no lane param).
  - Translates the v2 bucketed response into a `DispatchQueueResponse`-shaped object
    (adds `total_pending`, `total_claimed`, `fetched_at`, `claimed` deprecated alias).
- The server-side bucketing (lane → DispatchQueueResponse) was already implemented in Q2.

### Shape delta: v2 vs DispatchQueueResponse

Q2's `list_queue()` returns:
```python
{
  "pending": [...],
  "in_progress": [...],
  "in_review": [...],
  "paused": [...],
  "needs_info": [...],
  "attention": [...],      # extra bucket — not in DispatchQueueResponse
}
```

`DispatchQueueResponse` requires:
```python
{
  "pending": [...],
  "in_progress": [...],
  "in_review": [...],
  "paused": [...],         # attention_queue items collapse into paused
  "needs_info": [...],
  "claimed": [...],        # deprecated alias for in_progress
  "total_pending": int,    # len(pending)
  "total_claimed": int,    # len(in_progress)
  "fetched_at": str,       # ISO timestamp
}
```

**Delta**: the adapter must merge `attention` into `paused`, add `claimed` alias, and
synthesize `total_pending`, `total_claimed`, `fetched_at`.

---

## Test Cases

### AC1 — Adapter output matches DispatchQueueResponse shape

**Test ID:** `TestQ4AC1AdapterShape`
**Strategy:** Mock `DispatchV2Service.list_queue()` to return a controlled v2 response.
  Assert the adapter output passes Pydantic `DispatchQueueResponse` validation
  (shape test, not a file snapshot — avoids brittle timestamp churn).

| Case | Scenario | Expected |
|------|----------|----------|
| AC1-T1 | Empty queue | All buckets empty lists, total_pending=0, total_claimed=0, fetched_at present |
| AC1-T2 | One pending, one in_progress | pending=[1 item], in_progress=[1 item], total_pending=1, total_claimed=1 |
| AC1-T3 | attention item | attention item mapped into `paused` bucket |
| AC1-T4 | needs_info item | stays in needs_info bucket |
| AC1-T5 | deprecated `claimed` alias | claimed == in_progress contents |

**RED condition:** Will fail until `frontend/src/api/dispatch.ts` adapter exists AND the
  Python route's bucketed response is augmented with `total_pending`, `total_claimed`,
  `fetched_at` (currently missing from Q2's `list_queue()` return).

**Note:** The Python-side test (AC1-T1 through AC1-T5) tests the GET /queue route
  directly via TestClient with a mocked service. The TypeScript type test (AC1-TS1)
  is a separate vitest test.

---

### AC2 — Dynamic lane query requires no code changes

**Test ID:** `TestQ4AC2DynamicLane`
**Strategy:** Call `GET /api/dispatch/v2/queue?lane=canary_queue` with a mocked service
  that returns one item for that lane. Assert the route accepts the request (no 400/422)
  and returns `{"lane": "canary_queue", "items": [...]}`. This proves the lane is treated
  as a pure query parameter with no server-side validation/enum.

| Case | Scenario | Expected |
|------|----------|----------|
| AC2-T1 | lane=canary_queue (unknown lane) | 200, `{"lane": "canary_queue", "items": [item]}` |
| AC2-T2 | lane=work_queue | 200, `{"lane": "work_queue", "items": [...]}` |
| AC2-T3 | lane=human_queue | 200, `{"lane": "human_queue", "items": [...]}` |
| AC2-T4 | lane="" (empty string) | 200 (treated as lane param present, empty lane) OR 204/empty — accept either |

**RED condition:** GREEN — Q2 already implemented the unvalidated `lane: str | None = None`
  query param. These tests should pass once the route module is importable from the
  worktree. Tests are marked `@pytest.mark.xfail(strict=False)` until Q2 merges.

---

### AC3 — Frontend renders without code changes (TypeScript)

**Test ID:** `TestQ4AC3TypeScriptAdapter`
**Strategy:** Vitest test in `frontend/src/__tests__/dispatchV2Adapter.test.ts`.
  - Imports `adaptV2QueueResponse` from `frontend/src/api/dispatch.ts` (does not exist yet → RED).
  - Asserts the adapter function maps a sample v2 payload to a valid `DispatchQueueResponse` object.
  - Asserts `useDispatchQueue` hook (existing) would accept the adapter output without type errors.

| Case | Scenario | Expected |
|------|----------|----------|
| AC3-T1 | v2 payload → adapter | Returns object matching DispatchQueueResponse interface |
| AC3-T2 | attention items | Mapped to paused bucket |
| AC3-T3 | fetched_at synthesized | ISO string present |
| AC3-T4 | claimed alias | Equal to in_progress |

**RED condition:** RED until `frontend/src/api/dispatch.ts` is created in Phase 8.
  The test file itself is written in Phase 7 and will error at import.

---

## Test File Structure

```
tests/
  test_epic_queue_v2_q4.py          ← Python: AC1 + AC2 (HTTP-level, mocked service)
frontend/
  src/__tests__/
    dispatchV2Adapter.test.ts       ← TypeScript: AC3 (adapter function + type safety)
```

---

## Expected Initial State (After Phase 7)

| Test Group | Expected State | Reason |
|-----------|---------------|--------|
| AC1 — shape (Python) | PARTIAL GREEN | Q2 /queue endpoint exists; missing total_pending/total_claimed/fetched_at in response |
| AC2 — dynamic lane | GREEN | Q2 already accepts any string as lane param |
| AC3 — TS adapter | RED | `frontend/src/api/dispatch.ts` does not exist yet |

---

## Implementation Gate (Phase 8)

Phase 8 must:
1. Add `total_pending`, `total_claimed`, `fetched_at` to the bucketed response in `list_queue()`.
2. Create `frontend/src/api/dispatch.ts` with `adaptV2QueueResponse()` function.
3. Update `useDispatchQueue.ts` to optionally call v2 endpoint (guarded by feature flag or config).
4. All 3 ACs should be GREEN.
