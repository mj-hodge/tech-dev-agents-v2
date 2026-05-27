# STORY-858: Dashboard Cleanup — Failed Stories Belong in History, Not Queue

## Problem Statement

The dashboard Queue panel currently renders failed stories (state=failed,
lane=attention_queue) alongside active work (pending, in_progress, in_review,
needs_info, claimed). The root cause is in `adaptV2QueueResponse` (dispatch.ts):
the `attention` bucket from the v2 API was being merged into `paused`, which
the Queue panel rendered without discrimination. After a 39-story failure cluster,
the queue view became pure noise — operators had to mentally filter dead rows to
see what was actually flowing.

This is a frontend-only fix. The /api/dispatch/v2/queue response already
separates lanes correctly — the consumer just needs to re-bucket attention and
dead_letter items out of the queue and into a separate history view.

PR #298 (STORY-857) implemented the right logic but included unrelated backend
changes (deployment scripts, Morris vigilance tests, STORY-100 smoke tests).
STORY-858 is a clean restart: identical frontend changes, zero backend touches.

## Overview

| Field | Value |
|-------|-------|
| Scope | small |
| Frontend | true |
| Phase Path | 1 → 7 → 8 → Done |

**Small** — frontend-only, isolated to 5 files (adapter, types, component,
tests, E2E spec). No backend changes, no DB schema, no new routes.

## Test Criteria

1. `adaptV2QueueResponse` with `attention=[failedItem]` returns
   `attention_queue=[failedItem]` and `paused=[]` (attention NOT merged into paused)
2. `adaptV2QueueResponse` with `dead_letter=[dlItem]` returns
   `attention_queue=[dlItem]`
3. `adaptV2QueueResponse` returns `attention_count` equal to `attention_queue.length`
4. `DispatchQueue` renders failed item in History panel, not Queue panel
5. `DispatchQueue` renders zero failed rows in Queue panel when attention_queue has items
6. Attention callout badge renders with correct count when `attention_queue.length > 0`
7. No callout when `attention_queue` is empty
8. Queue total excludes attention_queue items from count
9. Playwright E2E: failed story NOT in `[data-testid="queue-panel"]`
10. Playwright E2E: failed story IS in `[data-testid="history-panel"]`

## Validation

After merge to main:
1. Deploy frontend (`npm run build`) — zero TypeScript errors
2. Open dashboard in browser — queue panel shows only actionable lanes
3. Run `npm test -- --testPathPattern=DispatchQueue|dispatchV2Adapter` — all GREEN
4. Run `npx playwright test e2e/dashboard-queue-history-split.spec.ts` — all PASS

## Acceptance Diff

The PR for this story MUST include changes to these files. Phase 8 will
fail if any are missing from `git diff origin/main --name-only`:

- `frontend/src/api/dispatch.ts` must-contain `attention_queue` must-contain `dead_letter` — adapter exposes attention_queue (attention + dead_letter), no longer merged into paused
- `frontend/src/types/api.ts` must-contain `attention_queue` must-contain `attention_count` — DispatchQueueResponse type extended
- `frontend/src/components/DispatchQueue.tsx` must-contain `attention_queue` must-contain `attention-callout` — queue renders callout, history panel shows failed items
- `frontend/src/__tests__/dispatchV2Adapter.test.ts` must-contain `attention_queue` — adapter tests cover new bucket
- `frontend/src/__tests__/DispatchQueue.queue-history.test.tsx` must-contain `attention_queue` — queue/history split unit tests
- `e2e/dashboard-queue-history-split.spec.ts` must-contain `attention` — Playwright E2E spec
