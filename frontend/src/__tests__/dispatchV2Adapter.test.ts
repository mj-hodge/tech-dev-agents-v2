/**
 * Epic-Queue-v2 Story Q4 — Phase 7: RED state tests for dashboard adapter.
 *
 * AC3: Frontend renders queue without code changes.
 *
 * These tests import from frontend/src/api/dispatch.ts which does NOT exist yet.
 * The import will fail until Phase 8 creates the adapter file, keeping these
 * tests in RED state as required for Phase 7.
 *
 * Expected state after Phase 8:
 *   - All tests GREEN
 *   - adaptV2QueueResponse() maps v2 bucketed response → DispatchQueueResponse
 *   - attention items merged into paused
 *   - total_pending, total_claimed, fetched_at synthesized
 *   - claimed deprecated alias == in_progress
 */

import { describe, it, expect } from 'vitest';

// ---------------------------------------------------------------------------
// RED import — this module does not exist until Phase 8
// ---------------------------------------------------------------------------
// @ts-ignore — intentionally importing a file that doesn't exist yet (RED state)
import { adaptV2QueueResponse } from '../api/dispatch';

import type { DispatchQueueResponse, DispatchItem } from '../types/api';

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeV2Item(overrides: Partial<DispatchItem & { lane: string }> = {}): DispatchItem & { lane: string } {
  return {
    story_id: 'STORY-Q4',
    repo: 'tech-dev-agents',
    scope: 'small',
    prompt: 'test prompt',
    enqueued_at: '2026-05-02T10:00:00Z',
    enqueued_by: 'mark',
    status: 'pending',
    claimed_by: null,
    claimed_at: null,
    completed_at: null,
    cancelled_at: null,
    lane: 'work_queue',
    ...overrides,
  };
}

// V2 bucketed response shape (what GET /api/dispatch/v2/queue returns today after Q2)
interface V2QueueBuckets {
  pending: DispatchItem[];
  in_progress: DispatchItem[];
  in_review: DispatchItem[];
  paused: DispatchItem[];
  needs_info: DispatchItem[];
  attention: DispatchItem[];  // extra bucket — NOT in DispatchQueueResponse
}

function makeEmptyV2Response(): V2QueueBuckets {
  return {
    pending: [],
    in_progress: [],
    in_review: [],
    paused: [],
    needs_info: [],
    attention: [],
  };
}

function makeFullV2Response(): V2QueueBuckets {
  return {
    pending: [makeV2Item({ status: 'pending', lane: 'work_queue' })],
    in_progress: [makeV2Item({
      story_id: 'STORY-Q4-B',
      status: 'claimed',
      claimed_by: 'dan',
      lane: 'in_progress',
    })],
    in_review: [makeV2Item({
      story_id: 'STORY-Q4-C',
      status: 'in_review',
      lane: 'in_review',
    })],
    paused: [makeV2Item({
      story_id: 'STORY-Q4-D',
      status: 'paused',
      lane: 'quarantined',
    })],
    needs_info: [makeV2Item({
      story_id: 'STORY-Q4-E',
      status: 'needs_info',
      lane: 'human_queue',
    })],
    attention: [makeV2Item({
      story_id: 'STORY-Q4-F',
      status: 'needs_info',
      lane: 'attention_queue',
    })],
  };
}

// ---------------------------------------------------------------------------
// AC3-T1: Adapter maps v2 payload → DispatchQueueResponse
// ---------------------------------------------------------------------------

describe('adaptV2QueueResponse — AC3 Phase 7 RED tests', () => {
  describe('AC3-T1: empty payload produces valid DispatchQueueResponse shape', () => {
    it('returns all required DispatchQueueResponse keys', () => {
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());

      expect(result).toHaveProperty('pending');
      expect(result).toHaveProperty('in_progress');
      expect(result).toHaveProperty('in_review');
      expect(result).toHaveProperty('paused');
      expect(result).toHaveProperty('needs_info');
      expect(result).toHaveProperty('claimed');
      expect(result).toHaveProperty('total_pending');
      expect(result).toHaveProperty('total_claimed');
      expect(result).toHaveProperty('fetched_at');
    });

    it('empty buckets are arrays, not null/undefined', () => {
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());

      expect(Array.isArray(result.pending)).toBe(true);
      expect(Array.isArray(result.in_progress)).toBe(true);
      expect(Array.isArray(result.in_review)).toBe(true);
      expect(Array.isArray(result.paused)).toBe(true);
      expect(Array.isArray(result.needs_info)).toBe(true);
      expect(Array.isArray(result.claimed)).toBe(true);
    });

    it('totals are zero for empty response', () => {
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());

      expect(result.total_pending).toBe(0);
      expect(result.total_claimed).toBe(0);
    });

    it('fetched_at is a non-empty string', () => {
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());

      expect(typeof result.fetched_at).toBe('string');
      expect(result.fetched_at.length).toBeGreaterThan(0);
    });
  });

  // ---------------------------------------------------------------------------
  // AC3-T2: attention items exposed as attention_queue (STORY-858)
  //
  // Old behaviour (pre-858): attention items were merged into paused.
  // New behaviour: attention items are terminal/failed and go to attention_queue
  // so the History panel can render them separately from the Queue panel.
  // ---------------------------------------------------------------------------

  describe('AC3-T2: attention_queue items exposed as attention_queue (not merged into paused)', () => {
    it('attention items appear in attention_queue bucket', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      expect((result.attention_queue ?? []).length).toBe(1);
    });

    it('attention items do NOT appear in paused bucket', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      // paused bucket should only contain the original paused item (STORY-Q4-D),
      // NOT the attention_queue item (STORY-Q4-F).
      const pausedIds = (result.paused ?? []).map((item) => item.story_id);
      expect(pausedIds).not.toContain('STORY-Q4-F');
      expect((result.paused ?? []).length).toBe(1);
    });

    it('attention key is absent from adapter output', () => {
      const v2 = makeFullV2Response();
      const result = adaptV2QueueResponse(v2) as unknown as Record<string, unknown>;

      expect(result).not.toHaveProperty('attention');
    });

    it('attention item story_id appears in attention_queue bucket', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      const attentionIds = (result.attention_queue ?? []).map((item) => item.story_id);
      expect(attentionIds).toContain('STORY-Q4-F');
    });
  });

  // ---------------------------------------------------------------------------
  // AC3-T3: fetched_at synthesized
  // ---------------------------------------------------------------------------

  describe('AC3-T3: fetched_at is synthesized correctly', () => {
    it('fetched_at parses as valid ISO datetime', () => {
      const before = Date.now();
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());
      const after = Date.now();

      const parsed = new Date(result.fetched_at).getTime();
      expect(parsed).toBeGreaterThanOrEqual(before - 1000);
      expect(parsed).toBeLessThanOrEqual(after + 1000);
    });
  });

  // ---------------------------------------------------------------------------
  // AC3-T4: claimed alias
  // ---------------------------------------------------------------------------

  describe('AC3-T4: claimed deprecated alias equals in_progress', () => {
    it('claimed array is referentially equal to in_progress', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      expect(result.claimed).toEqual(result.in_progress);
    });

    it('claimed is empty when in_progress is empty', () => {
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());

      expect(result.claimed).toEqual([]);
      expect(result.in_progress).toEqual([]);
    });
  });

  // ---------------------------------------------------------------------------
  // AC3 — DispatchQueueResponse type compatibility
  // ---------------------------------------------------------------------------

  describe('AC3 — TypeScript type compatibility', () => {
    it('adapter output is assignable to DispatchQueueResponse', () => {
      // TypeScript type-check: if this compiles, the type is compatible.
      const result: DispatchQueueResponse = adaptV2QueueResponse(makeEmptyV2Response());
      // Runtime smoke: shape is usable
      expect(result.pending.length).toBe(0);
    });

    it('total_pending matches pending array length', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      expect(result.total_pending).toBe(result.pending.length);
    });

    it('total_claimed matches in_progress array length', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      expect(result.total_claimed).toBe(result.in_progress.length);
    });
  });
});

// ---------------------------------------------------------------------------
// STORY-861: Phase 7 RED tests
// attention bucket must stay SEPARATE from paused (not merged in)
// ---------------------------------------------------------------------------

describe('STORY-861: adaptV2QueueResponse — attention stays separate', () => {
  // AC-7: paused contains ONLY items from raw.paused (not attention)
  describe('AC-7: attention items do NOT appear in paused bucket', () => {
    it('paused bucket contains only items from raw.paused', () => {
      const v2 = makeFullV2Response(); // has 1 paused + 1 attention item
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      // After STORY-861 fix: only the 1 raw paused item is here, NOT the attention item.
      // RED: current code merges attention into paused → length is 2, not 1.
      expect(result.paused!.length).toBe(1);
    });

    it('attention item story_id is NOT in the paused bucket', () => {
      const v2 = makeFullV2Response();
      const result: DispatchQueueResponse = adaptV2QueueResponse(v2);

      const pausedIds = (result.paused ?? []).map((item) => item.story_id);
      // RED: current code puts STORY-Q4-F (attention item) into paused.
      expect(pausedIds).not.toContain('STORY-Q4-F');
    });
  });

  // AC-8: result.attention is populated when raw.attention is non-empty
  describe('AC-8: result.attention is populated from raw.attention', () => {
    it('result has attention key when raw.attention is non-empty', () => {
      const v2 = makeFullV2Response();
      const result = adaptV2QueueResponse(v2) as unknown as Record<string, unknown>;

      // RED: current adapter drops the attention key entirely.
      expect(result).toHaveProperty('attention');
    });

    it('result.attention contains the attention item', () => {
      const v2 = makeFullV2Response();
      const result = adaptV2QueueResponse(v2) as unknown as Record<string, unknown>;
      const attention = result['attention'] as DispatchItem[] | undefined;

      // RED: current adapter returns no attention key.
      expect(Array.isArray(attention)).toBe(true);
      expect((attention ?? []).map((i) => i.story_id)).toContain('STORY-Q4-F');
    });

    it('result.attention is empty array when raw.attention is absent', () => {
      const v2 = makeEmptyV2Response();
      const result = adaptV2QueueResponse(v2) as unknown as Record<string, unknown>;
      const attention = result['attention'] as DispatchItem[] | undefined;

      // After fix: empty array (not undefined/missing).
      // RED: current adapter returns no attention key.
      expect(Array.isArray(attention)).toBe(true);
      expect((attention ?? []).length).toBe(0);
    });
  });
});
