/**
 * STORY-858 — Queue / History split unit tests.
 *
 * AC-1: Queue panel renders zero rows with status=failed.
 * AC-2: History panel renders attention_queue (failed) rows.
 * AC-3: Attention callout visible/hidden based on attention_queue count.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { DispatchQueue } from '../components/DispatchQueue';

// ---------- helpers ----------
const makeItem = (overrides: Record<string, unknown> = {}) => ({
  story_id: 'STORY-999',
  title: null,
  status: 'pending',
  repo: 'tech-dev-agents',
  scope: 'small',
  prompt: '',
  enqueued_by: 'test',
  enqueued_at: new Date().toISOString(),
  claimed_by: null,
  claimed_at: null,
  completed_at: null,
  cancelled_at: null,
  review_started_at: null,
  needs_info_path: null,
  current_phase: null,
  ...overrides,
});

const makeFailedItem = (storyId = 'STORY-FAILED') =>
  makeItem({ story_id: storyId, status: 'failed' });

const makeHistoryData = (items: unknown[] = []) => ({
  items,
  total: items.length,
  limit: 20,
  offset: 0,
  fetched_at: new Date().toISOString(),
});

// ---------- mock state ----------
let mockQueueData: Record<string, unknown> = {};
let mockHistoryData: ReturnType<typeof makeHistoryData> = makeHistoryData();

vi.mock('../hooks/useDispatchQueue', () => ({
  useDispatchQueue: () => ({
    data: mockQueueData,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCancelDispatch: () => ({ mutate: vi.fn() }),
  useDispatchHistory: () => ({
    data: mockHistoryData,
    isLoading: false,
    isError: false,
  }),
}));

const renderWithQuery = (ui: React.ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

// Reset before each test
beforeEach(() => {
  mockQueueData = {
    pending: [],
    in_progress: [],
    claimed: [],
    in_review: [],
    paused: [],
    needs_info: [],
    attention_queue: [],
    total_pending: 0,
    total_claimed: 0,
    fetched_at: new Date().toISOString(),
  };
  mockHistoryData = makeHistoryData();
});

// ---------------------------------------------------------------------------
// AC-1: Queue panel must NOT show failed rows
// ---------------------------------------------------------------------------

describe('AC-1: Queue panel excludes failed / attention_queue rows', () => {
  it('does not render a failed story row in the queue panel', () => {
    mockQueueData = {
      ...mockQueueData,
      attention_queue: [makeFailedItem('STORY-FAILED-001')],
    };
    renderWithQuery(<DispatchQueue />);
    // The queue panel table should NOT contain the failed story
    const queuePanel = document.querySelector('[data-testid="queue-panel"]');
    expect(queuePanel).not.toBeNull();
    // The failed story should not be inside the queue panel
    expect(queuePanel?.textContent).not.toContain('STORY-FAILED-001');
  });

  it('shows "No stories in dispatch queue" when only failed items exist', () => {
    mockQueueData = {
      ...mockQueueData,
      attention_queue: [makeFailedItem()],
      total_pending: 0,
      total_claimed: 0,
    };
    renderWithQuery(<DispatchQueue />);
    expect(screen.getByText('No stories in dispatch queue')).toBeTruthy();
  });

  it('renders pending items in queue panel but not failed items', () => {
    mockQueueData = {
      ...mockQueueData,
      pending: [makeItem({ story_id: 'STORY-ACTIVE', status: 'pending' })],
      attention_queue: [makeFailedItem('STORY-FAILED-002')],
      total_pending: 1,
    };
    renderWithQuery(<DispatchQueue />);
    // Queue panel has active story
    const queuePanel = document.querySelector('[data-testid="queue-panel"]');
    expect(queuePanel?.textContent).toContain('STORY-ACTIVE');
    // Queue panel does NOT have failed story
    expect(queuePanel?.textContent).not.toContain('STORY-FAILED-002');
  });
});

// ---------------------------------------------------------------------------
// AC-2: History panel shows failed / attention_queue rows
// ---------------------------------------------------------------------------

describe('AC-2: History panel includes attention_queue (failed) rows', () => {
  it('renders attention_queue items in history panel', () => {
    mockQueueData = {
      ...mockQueueData,
      attention_queue: [makeFailedItem('STORY-IN-HISTORY')],
    };
    renderWithQuery(<DispatchQueue />);
    const historyPanel = document.querySelector('[data-testid="history-panel"]');
    expect(historyPanel).not.toBeNull();
    expect(historyPanel?.textContent).toContain('STORY-IN-HISTORY');
  });

  it('history panel label is visible on screen', () => {
    renderWithQuery(<DispatchQueue />);
    expect(screen.getByText('History')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC-3: Attention callout in queue header
// ---------------------------------------------------------------------------

describe('AC-3: Attention callout in queue header', () => {
  it('callout is HIDDEN when attention_queue is empty', () => {
    mockQueueData = { ...mockQueueData, attention_queue: [] };
    renderWithQuery(<DispatchQueue />);
    expect(screen.queryByTestId('attention-callout')).toBeNull();
  });

  it('callout is VISIBLE with count when attention_queue has items', () => {
    mockQueueData = {
      ...mockQueueData,
      attention_queue: [makeFailedItem('STORY-FAIL-A'), makeFailedItem('STORY-FAIL-B')],
    };
    renderWithQuery(<DispatchQueue />);
    const callout = screen.getByTestId('attention-callout');
    expect(callout).toBeTruthy();
    expect(callout.textContent).toContain('2');
    expect(callout.textContent?.toLowerCase()).toContain('attention');
  });

  it('callout has accessible aria-label', () => {
    mockQueueData = {
      ...mockQueueData,
      attention_queue: [makeFailedItem()],
    };
    renderWithQuery(<DispatchQueue />);
    const callout = screen.getByTestId('attention-callout');
    expect(callout.getAttribute('aria-label')).toMatch(/attention/i);
  });
});
