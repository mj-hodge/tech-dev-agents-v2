# STORY-858 Test Design

## Phase 7 — RED state tests

### Unit Tests

#### 1. `dispatchV2Adapter.test.ts` — update attention_queue assertions (AC-1)
Update existing `AC3-T2` suite to assert:
- `attention` items go to `attention_queue` bucket (NOT `paused`)
- `paused` bucket contains only the original paused items
- `attention_queue` key IS present in the adapter output

#### 2. `DispatchQueue.queue-history.test.tsx` — new test (AC-1, AC-2, AC-3)
Tests:
- Queue panel renders NO rows with `status=failed`
- Queue panel renders NO rows sourced from `attention_queue`
- History panel renders failed/attention_queue rows
- Attention callout shows count and is visible when `attention_queue` count > 0
- Attention callout is hidden when `attention_queue` is empty
- Queue panel "no stories" message shown when only failed items exist (not actionable)

#### 3. `DispatchQueue.needs_info.test.tsx` — add `useDispatchHistory` mock
Add `useDispatchHistory` to mock so HistoryPanel doesn't call unmocked hooks.

### E2E Tests

#### `e2e/dashboard-queue-history-split.spec.ts` — Playwright (AC-5)
- Mock `/api/dispatch/v2/queue` with `attention: [failedItem]`
- Mock `/api/dispatch/history` with empty items
- Assert `STORY-FAILED` NOT visible in queue panel
- Assert `STORY-FAILED` IS visible in history panel
- Assert attention callout "1 in attention" visible in queue header
