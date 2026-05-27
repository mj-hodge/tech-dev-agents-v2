/**
 * STORY-861: Phase 7 RED tests — failed stories belong in History, not Queue.
 *
 * AC-1: Queue tab renders zero rows when only item has status=failed/attention_queue.
 * AC-2: Queue tab renders zero rows when only item is in the attention bucket.
 * AC-3: History tab renders a row for a failed-state item.
 * AC-4: History tab renders a row for an attention-bucket item.
 * AC-5: Header shows "N stories in attention" callout badge when attention.length > 0.
 * AC-6: Clicking the attention callout switches activeTab to 'history'.
 * AC-9: Existing FleetOverviewBar + needs_info badge tests still pass (regression guard).
 *
 * RED state: all AC-1..AC-6 tests FAIL because DispatchQueue has not been updated yet.
 * AC-9 regression test passes (GREEN) — it asserts existing behavior is unchanged.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { DispatchQueue } from '../components/DispatchQueue';
import type { DispatchItem } from '../types/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeItem(overrides: Partial<DispatchItem> = {}): DispatchItem {
  return {
    story_id: 'STORY-861-TEST',
    repo: 'tech-dev-agents',
    scope: 'small',
    prompt: 'test prompt',
    enqueued_at: '2026-05-04T00:00:00Z',
    enqueued_by: 'mark',
    status: 'failed',
    claimed_by: null,
    claimed_at: null,
    completed_at: null,
    cancelled_at: null,
    failed_at: '2026-05-04T00:05:00Z',
    commit_sha: null,
    pr_number: null,
    review_started_at: null,
    paused_at: null,
    current_phase: 7,
    phase_started_at: null,
    needs_info_path: null,
    title: 'failed story for test',
    ...overrides,
  };
}

const BASE_MOCK = {
  pending: [] as DispatchItem[],
  in_progress: [] as DispatchItem[],
  claimed: [] as DispatchItem[],
  in_review: [] as DispatchItem[],
  paused: [] as DispatchItem[],
  needs_info: [] as DispatchItem[],
  attention: [] as DispatchItem[],
  completed: [] as DispatchItem[],
  failed: [] as DispatchItem[],
  total_pending: 0,
  total_claimed: 0,
  fetched_at: '2026-05-04T00:00:00Z',
};

type MockQueueData = typeof BASE_MOCK;
let currentMockData: MockQueueData = BASE_MOCK;

// Mock useDispatchQueue so we control queue data without network.
vi.mock('../hooks/useDispatchQueue', () => ({
  useDispatchQueue: () => ({
    data: currentMockData,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCancelDispatch: () => ({ mutate: vi.fn() }),
  useDispatchHistory: () => ({
    data: null,
    isLoading: false,
    isError: false,
  }),
}));

const renderWithQuery = (ui: React.ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

// ---------------------------------------------------------------------------
// AC-1: Queue tab renders zero rows when only item has status=failed
// ---------------------------------------------------------------------------

describe('STORY-861 AC-1: queue tab excludes failed items', () => {
  beforeEach(() => {
    currentMockData = {
      ...BASE_MOCK,
      // Only a failed item — should NOT appear in queue tab
      paused: [makeItem({ status: 'failed' })],
    };
  });

  it('queue tab shows "No stories" when the only item is failed (status field)', () => {
    renderWithQuery(<DispatchQueue />);
    // RED: current component renders the failed item in the paused section
    // After fix: only actionable lanes render; failed is excluded.
    expect(screen.getByText(/no stories in dispatch queue/i)).toBeTruthy();
  });

  it('queue tab renders zero data rows with a single failed item', () => {
    renderWithQuery(<DispatchQueue />);
    // No table body rows should be rendered for the queue tab.
    const rows = document.querySelectorAll('tbody tr');
    // RED: currently 1 row rendered (the failed item in paused)
    expect(rows.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// AC-2: Queue tab renders zero rows when only item is in attention bucket
// ---------------------------------------------------------------------------

describe('STORY-861 AC-2: queue tab excludes attention-bucket items', () => {
  beforeEach(() => {
    currentMockData = {
      ...BASE_MOCK,
      // attention bucket with a failed item — must NOT appear in queue
      attention: [makeItem({ story_id: 'STORY-ATTN', status: 'failed' })],
    };
  });

  it('queue tab shows "No stories" when only item is in attention bucket', () => {
    renderWithQuery(<DispatchQueue />);
    // RED: attention items don't currently go through the component as a separate bucket,
    // and current code doesn't know about `data.attention`.
    expect(screen.getByText(/no stories in dispatch queue/i)).toBeTruthy();
  });

  it('queue tab renders zero data rows with attention-only response', () => {
    renderWithQuery(<DispatchQueue />);
    const rows = document.querySelectorAll('tbody tr');
    // RED: current component would render nothing for attention (it's undefined in response),
    // but totalItems would be wrong because attention count is not excluded from totalItems calculation
    expect(rows.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// AC-3: History tab renders a row for a failed-state item
// ---------------------------------------------------------------------------

describe('STORY-861 AC-3: history tab shows failed items', () => {
  beforeEach(() => {
    currentMockData = {
      ...BASE_MOCK,
      attention: [makeItem({ story_id: 'STORY-FAILED-HIST', status: 'failed', title: 'failed story for history' })],
    };
  });

  it('history tab renders a row for a failed item from attention bucket', () => {
    renderWithQuery(<DispatchQueue />);
    // Switch to history tab
    const historyTab = screen.getByRole('button', { name: /history/i });
    fireEvent.click(historyTab);

    // RED: HistoryTab uses useDispatchHistory (API endpoint), not queue data.attention.
    // After fix: attention items from the queue response are surfaced in History tab.
    expect(screen.getByText('STORY-FAILED-HIST')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC-4: History tab renders row for attention-bucket item
// ---------------------------------------------------------------------------

describe('STORY-861 AC-4: history tab shows attention-bucket items', () => {
  beforeEach(() => {
    currentMockData = {
      ...BASE_MOCK,
      attention: [
        makeItem({ story_id: 'STORY-ATTN-HIST', status: 'failed', title: 'attention item' }),
      ],
    };
  });

  it('switching to history tab shows attention item', () => {
    renderWithQuery(<DispatchQueue />);
    const historyTab = screen.getByRole('button', { name: /history/i });
    fireEvent.click(historyTab);

    // RED: attention items not yet surfaced in History tab.
    expect(screen.getByText('STORY-ATTN-HIST')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC-5: Header shows "N stories in attention" callout when attention.length > 0
// ---------------------------------------------------------------------------

describe('STORY-861 AC-5: attention callout badge in header', () => {
  it('shows attention callout badge when attention.length > 0', () => {
    currentMockData = {
      ...BASE_MOCK,
      attention: [makeItem({ story_id: 'STORY-A1' }), makeItem({ story_id: 'STORY-A2' })],
    };
    renderWithQuery(<DispatchQueue />);
    // RED: data-testid="attention-callout" doesn't exist in current component.
    const callout = screen.getByTestId('attention-callout');
    expect(callout).toBeTruthy();
    expect(callout.textContent).toMatch(/2.*attention/i);
  });

  it('attention callout badge is absent when attention is empty', () => {
    currentMockData = { ...BASE_MOCK, attention: [] };
    renderWithQuery(<DispatchQueue />);
    // Should be null/absent — this should be PASS after fix, but depends on badge existing.
    // RED until attention-callout is implemented (queryByTestId returns null either way on empty,
    // so this is a structural pass; the group is RED due to the non-empty test above).
    expect(screen.queryByTestId('attention-callout')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC-6: Clicking attention callout switches activeTab to 'history'
// ---------------------------------------------------------------------------

describe('STORY-861 AC-6: clicking attention callout switches to history tab', () => {
  beforeEach(() => {
    currentMockData = {
      ...BASE_MOCK,
      attention: [makeItem({ story_id: 'STORY-CLICK' })],
    };
  });

  it('clicking attention callout makes history tab active', () => {
    renderWithQuery(<DispatchQueue />);
    // RED: attention-callout doesn't exist yet.
    const callout = screen.getByTestId('attention-callout');
    fireEvent.click(callout);

    // After click the history tab content should be active (Queue tab content hidden).
    // History tab button should have active styling or History content should be visible.
    const historyBtn = screen.getByRole('button', { name: /history/i });
    expect(historyBtn.className).toMatch(/border-b-2/);
  });
});

// ---------------------------------------------------------------------------
// AC-9: Regression — existing needs_info badge still works
// ---------------------------------------------------------------------------

describe('STORY-861 AC-9: regression — needs_info badge unchanged', () => {
  it('needs_info badge still appears when needs_info items exist (zero regression)', () => {
    currentMockData = {
      ...BASE_MOCK,
      needs_info: [
        makeItem({
          story_id: 'STORY-NI',
          status: 'needs_info',
          needs_info_path: 'features/x/QUESTION.md',
        }),
      ],
    };
    renderWithQuery(<DispatchQueue />);
    // This MUST remain GREEN throughout Phase 8 — existing behavior guard.
    const badge = screen.getByTestId('needs-info-badge');
    expect(badge).toBeTruthy();
    expect(badge.textContent).toContain('1');
  });
});
