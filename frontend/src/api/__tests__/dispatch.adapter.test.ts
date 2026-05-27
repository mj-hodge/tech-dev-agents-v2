/**
 * story-899: v2 adapter field-rename tests.
 *
 * normalizeLane must map v2 wire fields → v1 DispatchItem field names:
 *   leased_by  → claimed_by
 *   leased_at  → claimed_at
 *   created_at → enqueued_at
 *
 * v1-shape rows (already have claimed_by / claimed_at / enqueued_at) must pass
 * through unchanged so backward-compat is preserved.
 */

import { describe, it, expect } from 'vitest';
import { adaptV2QueueResponse, type V2QueueBuckets } from '../dispatch';
import type { DispatchItem } from '../../types/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Minimal v2-wire row — only has the leased_* / created_at names, NOT claimed_* */
function makeV2WireItem(
  overrides: Partial<DispatchItem & {
    leased_by?: string | null;
    leased_at?: string | null;
    created_at?: string | null;
  }> = {},
): DispatchItem & { leased_by?: string | null; leased_at?: string | null; created_at?: string | null } {
  return {
    story_id: 'STORY-899',
    repo: 'tech-dev-agents',
    scope: 'small',
    prompt: 'test',
    enqueued_at: '',          // empty — will be overridden by created_at mapping
    enqueued_by: 'mark',
    status: 'pending',
    claimed_by: null,         // not set in v2 wire shape
    claimed_at: null,         // not set in v2 wire shape
    completed_at: null,
    cancelled_at: null,
    ...overrides,
  };
}

/** Minimal v1-shape row — already has claimed_by / claimed_at / enqueued_at */
function makeV1Item(overrides: Partial<DispatchItem> = {}): DispatchItem {
  return {
    story_id: 'STORY-899-V1',
    repo: 'tech-dev-agents',
    scope: 'small',
    prompt: 'test v1',
    enqueued_at: '2026-05-01T08:00:00Z',
    enqueued_by: 'mark',
    status: 'pending',
    claimed_by: 'dan',
    claimed_at: '2026-05-01T09:00:00Z',
    completed_at: null,
    cancelled_at: null,
    ...overrides,
  };
}

function emptyBuckets(): V2QueueBuckets {
  return {
    pending: [],
    in_progress: [],
    in_review: [],
    paused: [],
    needs_info: [],
  };
}

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

describe('story-899: normalizeLane field-rename (v2 → v1 DispatchItem field names)', () => {

  // -------------------------------------------------------------------------
  // Case 1: v2-shape input — leased_by/leased_at/created_at → claimed_*/enqueued_at
  // -------------------------------------------------------------------------
  describe('case 1: v2-shape input maps leased_* → claimed_* and created_at → enqueued_at', () => {
    it('maps leased_by to claimed_by', () => {
      const raw = makeV2WireItem({
        claimed_by: null,
        leased_by: 'dan',
        leased_at: '2026-05-05T10:00:00Z',
        created_at: '2026-05-05T09:00:00Z',
      });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), in_progress: [raw as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress[0].claimed_by).toBe('dan');
    });

    it('maps leased_at to claimed_at', () => {
      const raw = makeV2WireItem({
        claimed_by: null,
        claimed_at: null,
        leased_by: 'dan',
        leased_at: '2026-05-05T10:00:00Z',
        created_at: '2026-05-05T09:00:00Z',
      });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), in_progress: [raw as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress[0].claimed_at).toBe('2026-05-05T10:00:00Z');
    });

    it('maps created_at to enqueued_at', () => {
      const raw = makeV2WireItem({
        claimed_by: null,
        claimed_at: null,
        leased_by: 'dan',
        leased_at: '2026-05-05T10:00:00Z',
        created_at: '2026-05-05T09:00:00Z',
        enqueued_at: '',  // v2 wire row doesn't have this populated
      });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), pending: [raw as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.pending[0].enqueued_at).toBe('2026-05-05T09:00:00Z');
    });
  });

  // -------------------------------------------------------------------------
  // Case 2: v1-shape input — claimed_*/enqueued_at preserved unchanged
  // -------------------------------------------------------------------------
  describe('case 2: v1-shape input preserves claimed_by / claimed_at / enqueued_at', () => {
    it('preserves claimed_by when already set', () => {
      const item = makeV1Item({ claimed_by: 'derrick', claimed_at: '2026-05-04T12:00:00Z' });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), in_progress: [item] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress[0].claimed_by).toBe('derrick');
    });

    it('preserves claimed_at when already set', () => {
      const item = makeV1Item({ claimed_at: '2026-05-04T12:00:00Z' });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), in_progress: [item] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress[0].claimed_at).toBe('2026-05-04T12:00:00Z');
    });

    it('preserves enqueued_at when already set', () => {
      const item = makeV1Item({ enqueued_at: '2026-05-01T08:00:00Z' });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), pending: [item] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.pending[0].enqueued_at).toBe('2026-05-01T08:00:00Z');
    });
  });

  // -------------------------------------------------------------------------
  // Case 3: mixed-shape — v1 field wins over v2 field (backward-compat)
  // -------------------------------------------------------------------------
  describe('case 3: mixed-shape input — v1 fields win over v2 fields', () => {
    it('claimed_by from v1 wins over leased_by from v2', () => {
      const mixed = {
        ...makeV1Item({ claimed_by: 'v1-winner' }),
        leased_by: 'v2-loser',
        leased_at: '2026-05-05T11:00:00Z',
        created_at: '2026-05-05T08:00:00Z',
      };
      const buckets: V2QueueBuckets = { ...emptyBuckets(), in_progress: [mixed as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress[0].claimed_by).toBe('v1-winner');
    });

    it('enqueued_at from v1 wins over created_at from v2', () => {
      const mixed = {
        ...makeV1Item({ enqueued_at: '2026-05-01T08:00:00Z' }),
        created_at: '2026-05-05T08:00:00Z',
      };
      const buckets: V2QueueBuckets = { ...emptyBuckets(), pending: [mixed as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.pending[0].enqueued_at).toBe('2026-05-01T08:00:00Z');
    });
  });

  // -------------------------------------------------------------------------
  // Case 4: null leased_by (pending/unclaimed row) — no crash
  // -------------------------------------------------------------------------
  describe('case 4: null leased_by does not crash and yields null claimed_by', () => {
    it('pending row with null leased_by produces claimed_by: null', () => {
      const raw = makeV2WireItem({
        claimed_by: null,
        claimed_at: null,
        leased_by: null,
        leased_at: null,
        created_at: '2026-05-05T09:00:00Z',
      });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), pending: [raw as unknown as DispatchItem] };
      expect(() => adaptV2QueueResponse(buckets)).not.toThrow();
      const result = adaptV2QueueResponse(buckets);
      expect(result.pending[0].claimed_by).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Case 5: status comes from bucket, not from item.state
  // -------------------------------------------------------------------------
  describe('case 5: status is always the bucket-derived value', () => {
    it('in_progress bucket gives status "claimed" regardless of item.status or .state', () => {
      const raw = makeV2WireItem({
        status: 'pending',   // wrong — should be overridden
        leased_by: 'dan',
        created_at: '2026-05-05T09:00:00Z',
      }) as DispatchItem & { state?: string };
      raw.state = 'in_progress';  // v2 wire field
      const buckets: V2QueueBuckets = { ...emptyBuckets(), in_progress: [raw as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress[0].status).toBe('claimed');
    });

    it('pending bucket gives status "pending" regardless of item.status', () => {
      const raw = makeV2WireItem({ status: 'claimed' });
      const buckets: V2QueueBuckets = { ...emptyBuckets(), pending: [raw as unknown as DispatchItem] };
      const result = adaptV2QueueResponse(buckets);
      expect(result.pending[0].status).toBe('pending');
    });
  });

  // -------------------------------------------------------------------------
  // Case 6: attention items still merge into paused with status 'paused'
  //         (existing AC3-T2 behavior preserved — regression guard)
  // -------------------------------------------------------------------------
  describe('case 6: attention items appear in paused bucket with status paused (regression guard)', () => {
    it('attention item is present in paused bucket', () => {
      const attentionItem = makeV2WireItem({
        story_id: 'STORY-899-ATTN',
        leased_by: null,
        created_at: '2026-05-05T07:00:00Z',
      });
      const buckets: V2QueueBuckets = {
        ...emptyBuckets(),
        attention: [attentionItem as unknown as DispatchItem],
      };
      const result = adaptV2QueueResponse(buckets);
      const pausedIds = (result.paused ?? []).map((i) => i.story_id);
      expect(pausedIds).toContain('STORY-899-ATTN');
    });

    it('attention item in paused bucket has status "paused"', () => {
      const attentionItem = makeV2WireItem({
        story_id: 'STORY-899-ATTN2',
        status: 'needs_info',   // raw status — should be overridden to 'paused'
        leased_by: null,
        created_at: '2026-05-05T07:00:00Z',
      });
      const buckets: V2QueueBuckets = {
        ...emptyBuckets(),
        attention: [attentionItem as unknown as DispatchItem],
      };
      const result = adaptV2QueueResponse(buckets);
      const attentionInPaused = (result.paused ?? []).find((i) => i.story_id === 'STORY-899-ATTN2');
      expect(attentionInPaused?.status).toBe('paused');
    });
  });

  describe('failure_class → failure_reason mapping (paused/failed items)', () => {
    it('maps v2 failure_class to failure_reason when failure_reason is absent', () => {
      const item = makeV2WireItem({
        story_id: 'STORY-899-FAIL',
        leased_by: 'devon',
        leased_at: '2026-05-05T10:00:00Z',
        created_at: '2026-05-05T09:00:00Z',
      }) as DispatchItem & { failure_class?: string | null };
      item.failure_class = 'sdk_invocation_failed';
      const buckets: V2QueueBuckets = {
        ...emptyBuckets(),
        paused: [item as unknown as DispatchItem],
      };
      const result = adaptV2QueueResponse(buckets);
      expect(result.paused?.[0]?.failure_reason).toBe('sdk_invocation_failed');
    });

    it('preserves existing failure_reason when both fields are present (v1 wins)', () => {
      const item = makeV2WireItem({
        story_id: 'STORY-899-FAIL2',
      }) as DispatchItem & { failure_class?: string | null };
      item.failure_reason = 'explicit_v1_reason';
      item.failure_class = 'should_be_ignored';
      const buckets: V2QueueBuckets = {
        ...emptyBuckets(),
        paused: [item as unknown as DispatchItem],
      };
      const result = adaptV2QueueResponse(buckets);
      expect(result.paused?.[0]?.failure_reason).toBe('explicit_v1_reason');
    });

    it('leaves failure_reason null when neither field is set (active items)', () => {
      const item = makeV2WireItem({
        story_id: 'STORY-899-OK',
        leased_by: 'dan',
      });
      const buckets: V2QueueBuckets = {
        ...emptyBuckets(),
        in_progress: [item as unknown as DispatchItem],
      };
      const result = adaptV2QueueResponse(buckets);
      expect(result.in_progress?.[0]?.failure_reason ?? null).toBe(null);
    });
  });
});
