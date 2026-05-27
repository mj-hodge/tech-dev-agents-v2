/**
 * Epic-Queue-v2 Story Q4 — Dashboard adapter.
 *
 * adaptV2QueueResponse maps a raw v2 bucketed response into a
 * DispatchQueueResponse-compatible object with all required fields synthesized:
 *   - attention + dead_letter items exposed as attention_queue (STORY-858: NOT merged into paused)
 *   - attention_count = len(attention_queue) for queue header callout
 *   - claimed  = in_progress (deprecated alias)
 *   - total_pending / total_claimed computed
 *   - fetched_at synthesized as current UTC ISO string
 *
 * STORY-858: attention_queue items are terminal/failed, not actionable.
 * They belong in the History panel, not the Queue panel. The adapter no longer
 * merges them into `paused`; they are exposed separately as `attention_queue`.
 */

import type { DispatchQueueResponse, DispatchItem } from '../types/api';

/**
 * Raw v2 bucketed shape returned by GET /api/dispatch/v2/queue (no lane param).
 *
 * Contains extra keys (`attention`, `dead_letter`) that are NOT part of
 * DispatchQueueResponse. May also be missing total_pending / total_claimed /
 * fetched_at / claimed.
 */
export interface V2QueueBuckets {
  pending: DispatchItem[];
  in_progress: DispatchItem[];
  in_review: DispatchItem[];
  paused: DispatchItem[];
  needs_info: DispatchItem[];
  /** Failed stories awaiting operator attention. */
  attention?: DispatchItem[];
  /** Stories that exhausted all retries — permanently failed. */
  dead_letter?: DispatchItem[];
  claimed?: DispatchItem[];
  total_pending?: number;
  total_claimed?: number;
  fetched_at?: string;
}

function normalizeLane(
  items: DispatchItem[] | undefined,
  status: DispatchItem['status'],
): DispatchItem[] {
  const rows = items ?? [];
  return rows.map((item) => {
    const raw = item as DispatchItem & {
      leased_by?: string | null;
      leased_at?: string | null;
      created_at?: string | null;
      failure_class?: string | null;
    };
    return {
      ...item,
      status,
      claimed_by: raw.claimed_by ?? raw.leased_by ?? null,
      claimed_at: raw.claimed_at ?? raw.leased_at ?? null,
      enqueued_at: raw.enqueued_at || raw.created_at || '',
      failure_reason: raw.failure_reason ?? raw.failure_class ?? null,
    };
  });
}

/**
 * Adapt a raw v2 bucketed queue response to a DispatchQueueResponse shape.
 *
 * Changes applied (STORY-858):
 *  1. `attention` + `dead_letter` items are exposed as `attention_queue`
 *     (NOT merged into paused). These are terminal/failed items — they render
 *     in the History panel, not the Queue panel.
 *  2. `attention_count` = len(attention_queue) for the queue header callout badge.
 *  3. `claimed` is set to the contents of `in_progress` (deprecated alias).
 *  4. `total_pending` = len(pending).
 *  5. `total_claimed` = len(in_progress).
 *  6. `fetched_at` = current UTC ISO string (override any existing value for freshness).
 */
export function adaptV2QueueResponse(raw: V2QueueBuckets): DispatchQueueResponse {
  // CRITICAL: every bucket MUST go through normalizeLane so each row gets:
  //   1. `status` set from the bucket key (the v2 wire payload only has `state`,
  //      not `status` — without normalizeLane every status badge renders blank)
  //   2. v2-shaped fields (leased_by/leased_at/created_at/failure_class) mapped
  //      to v1 names (claimed_by/claimed_at/enqueued_at/failure_reason)
  // Bypassing normalizeLane (as STORY-858 inadvertently did) drops both
  // mappings — the dashboard becomes data-blind.
  const pending: DispatchItem[] = normalizeLane(raw.pending, 'pending');
  const in_progress: DispatchItem[] = normalizeLane(raw.in_progress, 'claimed');
  const in_review: DispatchItem[] = normalizeLane(raw.in_review, 'in_review');
  const needs_info: DispatchItem[] = normalizeLane(raw.needs_info, 'needs_info');
  const paused: DispatchItem[] = normalizeLane(raw.paused, 'paused');
  // STORY-858: attention + dead_letter items are failed/terminal — expose
  // separately, normalized as 'failed' so the History panel can render them.
  const attention_queue: DispatchItem[] = [
    ...normalizeLane(raw.attention, 'failed'),
    ...normalizeLane(raw.dead_letter, 'failed'),
  ];

  return {
    pending,
    in_progress,
    in_review,
    paused,
    needs_info,
    attention_queue,
    attention_count: attention_queue.length,
    claimed: in_progress,          // deprecated alias — same array reference
    total_pending: pending.length,
    total_claimed: in_progress.length,
    fetched_at: new Date().toISOString(),
  };
}
