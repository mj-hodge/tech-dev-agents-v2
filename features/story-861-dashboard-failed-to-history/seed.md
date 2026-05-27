# STORY-861: Dashboard cleanup — failed stories belong in History, not Queue

**Frontend:** true
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Date:** 2026-05-04

---

## Problem Statement

The Dispatch Queue panel renders failed stories (lane=`attention_queue`, state=`failed`) alongside
actionable work (pending, in_progress, in_review, needs_info, claimed). After a 39-story failure
cluster the queue became unreadable noise — operators had to scroll past dozens of red rows to find
the one story that actually needed their input. The root cause is in `adaptV2QueueResponse`
(`frontend/src/api/dispatch.ts`): the adapter merges `attention` bucket items into `paused` instead
of keeping them separate, so the frontend cannot distinguish "agent paused and will resume" from
"story is dead and requires triage". The `/api/dispatch/v2/queue` response already separates lanes
correctly; the bug is purely a frontend bucketing decision.

---

## Scope

**Small** — frontend-only change across three components + one adapter function. No backend API
changes required; the data is already structured correctly. Single component boundary crossed
(DispatchQueue → dispatch.ts adapter).

---

## Desired Outcome

1. **Queue panel** shows only actionable lanes: `pending`, `in_progress`, `in_review`, `needs_info`,
   `claimed` (alias for `in_progress`), `paused`.
2. **History panel** (enhanced existing History tab in `DispatchQueue.tsx`) shows terminal + failed
   lanes: `attention_queue`, `completed`, `dead_letter`, `cancelled`. Default filter: last 7 days.
3. **Header callout** in Queue panel: "N stories in attention — view history" link when
   `attention.length > 0`, clicking switches to History tab.

---

## Root Cause (codebase-confirmed)

`frontend/src/api/dispatch.ts` line 51–54:
```ts
const paused: DispatchItem[] = [
  ...(raw.paused ?? []),
  ...(raw.attention ?? []),   // <-- WRONG: attention items pollute the queue panel
];
```
Fix: stop merging `attention` into `paused`; surface it as a first-class bucket in
`DispatchQueueResponse`.

---

## Files to Touch

| File | Change |
|------|--------|
| `frontend/src/api/dispatch.ts` | Keep `attention` separate; pass through in return value |
| `frontend/src/types/api.ts` | Add `attention?: DispatchItem[]` to `DispatchQueueResponse` |
| `frontend/src/components/DispatchQueue.tsx` | Queue tab: exclude attention; header callout; History tab: add attention + 7d filter |
| `frontend/src/components/DashboardLayout.tsx` | Add `DispatchHistory` section below `<Outlet />` (standalone History panel import) if History is promoted to top-level layout; otherwise label this as minor — see note |
| `frontend/src/__tests__/DispatchQueue.failed_history.test.tsx` | New: AC-1/AC-2/AC-3 unit tests |
| `frontend/src/__tests__/dispatchV2Adapter.test.ts` | Update: assert attention NOT merged into paused |
| `e2e/dashboard.spec.ts` | New: Playwright assertions for both panels |

> **DashboardLayout.tsx note:** The existing `DispatchQueue` is rendered inside `AgentGrid.tsx`,
> not in `DashboardLayout`. If Phase 8 finds `DashboardLayout` doesn't need changes (because the
> History section lives inside `DispatchQueue`'s existing tab), this file may be a no-op. The
> Acceptance Diff lists it as optional; the verifier will pass on file-presence only.

---

## Test Criteria

1. `DispatchQueue` queue tab renders zero rows when the only item has `status='failed'` (or lane=`attention_queue`).
2. `DispatchQueue` queue tab renders zero rows when the only item appears in the `attention` bucket of the API response.
3. `DispatchQueue` History tab renders a row for a `failed`-state item.
4. `DispatchQueue` History tab renders a row for an `attention`-bucket item.
5. Header shows "N stories in attention" callout badge when `attention.length > 0`; badge is absent when `attention.length === 0`.
6. Clicking the attention callout switches `activeTab` to `'history'`.
7. `adaptV2QueueResponse` no longer merges `attention` into `paused`; `result.paused` contains only items from `raw.paused`; `result.attention` contains items from `raw.attention`.
8. `adaptV2QueueResponse` result has `attention` key populated when `raw.attention` is non-empty.
9. Existing `FleetOverviewBar` + `AgentCard` + `needs_info` badge tests still pass (zero regressions).
10. Playwright: a story with `status='failed'` does NOT appear in queue rows; it DOES appear in History tab rows.

---

## Validation

After the PR merges, verify on the live dashboard:
1. Load `/` (Fleet tab) with the dispatch queue visible.
2. If any `attention_queue` stories exist, confirm they are absent from the Queue tab rows.
3. Switch to History tab — confirm the attention stories appear there.
4. If `attention.length > 0`, confirm the header callout "N stories in attention" is visible.
5. Re-run `npm test -- --run` in `frontend/` — all tests GREEN, no regressions.
6. Run `npx playwright test e2e/dashboard.spec.ts` — all assertions pass.

---

## Acceptance Criteria

- **AC-1:** Queue panel renders zero rows with `status=failed` / `lane=attention_queue` in any test scenario.
- **AC-2:** History panel renders failed/completed rows; default shows last 7 days.
- **AC-3:** Header shows "N stories in attention" link if N > 0.
- **AC-4:** Existing `FleetOverviewBar` + `AgentCard` tests still pass.
- **AC-5:** Playwright spec asserts both lanes visually.
- **AC-6:** `frontend: true` declared in this seed (post-2026-04-26 contract). ✓

---

## Acceptance Diff

The PR for this story MUST include changes to these files. Phase 8 will
fail if any are missing from `git diff origin/main --name-only`:

- `frontend/src/api/dispatch.ts` must-contain `attention` must-contain `result.attention` — attention bucket no longer merged into paused
- `frontend/src/types/api.ts` must-contain `attention?: DispatchItem[]` — new optional field on DispatchQueueResponse
- `frontend/src/components/DispatchQueue.tsx` must-contain `attention` must-contain `attention-callout` — queue excludes attention, header callout rendered
- `frontend/src/__tests__/DispatchQueue.failed_history.test.tsx` — new test file (presence check only)
- `frontend/src/__tests__/dispatchV2Adapter.test.ts` must-contain `attention` — updated adapter test asserts attention stays separate
- `e2e/dashboard.spec.ts` — new Playwright spec (presence check only)

---

## Out of Scope

- Backend API changes — data already structured correctly
- Bulk-action UI (retry-all, clear-attention) — STORY-862
- Per-failure-class breakdown — STORY-808
- `DashboardLayout.tsx` structural changes — only needed if History is promoted out of `DispatchQueue`
