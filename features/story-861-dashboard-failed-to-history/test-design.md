# STORY-861: Test Design — Dashboard cleanup: failed stories → History

**Phase:** 7 — Test Design (RED state)
**Scope:** Small — frontend-only
**Date:** 2026-05-04

---

## Summary

Three source files and two test files cover all 10 acceptance criteria.
No backend API changes; the backend already separates `attention` from queue lanes.

---

## Root Cause Recap

`adaptV2QueueResponse` in `frontend/src/api/dispatch.ts` (lines 62–65) currently merges
the `attention` bucket into `paused`, hiding the distinction between "will resume" and
"story is dead". The v2 API response already separates lanes correctly.

---

## Test Files

| File | Tests | Covers |
|------|-------|--------|
| `frontend/src/__tests__/dispatchV2Adapter.test.ts` | 3 updated (AC-7, AC-8) | Adapter no longer merges attention into paused; `result.attention` populated |
| `frontend/src/__tests__/DispatchQueue.failed_history.test.tsx` | 7 new (AC-1 thru AC-6, AC-9) | Queue tab excludes failed/attention; History tab shows them; callout badge; regression |
| `e2e/dashboard-failed-history.spec.ts` | 3 new (AC-10) | Playwright: failed row absent from queue, present in history |

---

## Test Groups

### Group A — Adapter: attention stays separate (AC-7, AC-8)

| ID | Name | Expected RED reason |
|----|------|---------------------|
| A-1 | `attention items do NOT appear in paused bucket` | FAIL — current code merges attention into paused; `result.paused.length` will equal 2 not 1 |
| A-2 | `result.attention is populated when raw.attention is non-empty` | FAIL — current return value has no `attention` key |
| A-3 | `result.paused contains only items from raw.paused` | FAIL — current code includes attention items in paused |

### Group B — DispatchQueue: queue tab excludes attention (AC-1, AC-2)

| ID | Name | Expected RED reason |
|----|------|---------------------|
| B-1 | `queue tab renders zero rows when only item has status=failed` | FAIL — `totalItems` currently counts paused (which includes merged attention/failed items) |
| B-2 | `queue tab renders zero rows when only item is in attention bucket` | FAIL — same: attention merged into paused, rendered in queue tab |

### Group C — DispatchQueue: history tab shows failed/attention (AC-3, AC-4)

| ID | Name | Expected RED reason |
|----|------|---------------------|
| C-1 | `history tab renders row for failed-state item` | FAIL — History tab uses `useDispatchHistory` hook (endpoint-based), does not yet include `attention` items from queue response |
| C-2 | `history tab renders row for attention-bucket item` | FAIL — attention bucket not passed to HistoryTab |

### Group D — DispatchQueue: attention callout badge (AC-5, AC-6)

| ID | Name | Expected RED reason |
|----|------|---------------------|
| D-1 | `header shows attention-callout badge when attention.length > 0` | FAIL — `data-testid="attention-callout"` doesn't exist in current DispatchQueue |
| D-2 | `attention callout badge is absent when attention.length === 0` | FAIL — testid absent; test will find it null (PASS-like, but D-1 failing keeps group RED) |
| D-3 | `clicking attention callout switches activeTab to history` | FAIL — no callout click handler, no tab switch |

### Group E — Regression (AC-9)

| ID | Name | Expected RED reason |
|----|------|---------------------|
| E-1 | `existing needs_info badge still renders` | PASS — regression guard; must stay GREEN in Phase 8 |

### Group F — E2E Playwright (AC-10)

| ID | Name | Expected RED reason |
|----|------|---------------------|
| F-1 | `failed story absent from queue tab rows` | FAIL — Playwright can't connect until frontend rebuilt with Phase 8 changes; structural RED |
| F-2 | `failed story visible in history tab rows` | FAIL — same |
| F-3 | `switching to history tab reveals failed row` | FAIL — same |

---

## Mock Shape

Tests mock `useDispatchQueue` to return a `DispatchQueueResponse` that includes the new
`attention?: DispatchItem[]` field (from the updated type). Tests for Groups B–D pass the
`attention` bucket directly in the mock so they fail for the right reason (component
doesn't read the field) rather than for type reasons.

```ts
const mockQueueWithAttention = {
  pending: [],
  in_progress: [],
  claimed: [],
  in_review: [],
  paused: [],
  needs_info: [],
  attention: [{ story_id: 'STORY-FAIL', status: 'failed', ... }],
  total_pending: 0,
  total_claimed: 0,
  fetched_at: '2026-05-04T00:00:00Z',
};
```

---

## RED State Verification

```
cd frontend && npm test -- --run
```

Expected: Groups A–D, F → FAIL; Group E → PASS.

---

## Coverage Targets

- `dispatch.ts` adapter: 100% — all 3 AC-7/8 tests cover the merge logic
- `DispatchQueue.tsx` queue tab render path: 3 tests (B-1, B-2, existing queue tests)
- `DispatchQueue.tsx` header callout: 3 tests (D-1 thru D-3)
- `DispatchQueue.tsx` history tab with attention: 2 tests (C-1, C-2)
- Regression: E-1 (existing needs_info badge)

---

## Follow-ups (Out of Scope)

- Bulk-action UI (retry-all, clear-attention) — STORY-862
- Per-failure-class breakdown — STORY-808
