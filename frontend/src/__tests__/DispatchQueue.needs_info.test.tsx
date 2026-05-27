/**
 * STORY-528/505 fix (2026-04-23): needs_info counter badge on DispatchQueue.
 *
 * When the dispatch queue contains items in `needs_info` state, a violet
 * pulsing badge must appear next to the "Dispatch Queue" header showing
 * the count. Clicking the badge expands the queue and scrolls to the
 * needs_info section.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { DispatchQueue } from '../components/DispatchQueue';

// ---------- test harness ----------
const mockQueueData = {
  pending: [],
  in_progress: [],
  claimed: [],
  in_review: [],
  paused: [],
  needs_info: [] as any[],
  attention_queue: [],  // STORY-858: expose failed items separately
  completed: [],
  failed: [],
  total_pending: 0,
  total_claimed: 0,
};

let currentMockData: typeof mockQueueData = mockQueueData;

vi.mock('../hooks/useDispatchQueue', () => ({
  useDispatchQueue: () => ({
    data: currentMockData,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCancelDispatch: () => ({ mutate: vi.fn() }),
  // STORY-858: HistoryPanel now renders inside DispatchQueue; mock this hook
  // so unit tests don't require a real react-query context with a network stub.
  useDispatchHistory: () => ({
    data: { items: [], total: 0, limit: 20, offset: 0, fetched_at: '' },
    isLoading: false,
    isError: false,
  }),
}));

const renderWithQuery = (ui: React.ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

describe('DispatchQueue needs_info badge', () => {
  beforeEach(() => {
    currentMockData = { ...mockQueueData, needs_info: [] };
  });

  it('is HIDDEN when no stories are in needs_info', () => {
    currentMockData = { ...mockQueueData, needs_info: [] };
    renderWithQuery(<DispatchQueue />);
    expect(screen.queryByTestId('needs-info-badge')).toBeNull();
  });

  it('is VISIBLE with the count when stories are in needs_info', () => {
    currentMockData = {
      ...mockQueueData,
      needs_info: [
        { story_id: 'STORY-528', status: 'needs_info', repo: 'advertising-amazon',
          scope: 'large', prompt: '', enqueued_by: 'test', enqueued_at: '', title: '528',
          claimed_by: null, claimed_at: null, completed_at: null, cancelled_at: null,
          failed_at: null, commit_sha: null, pr_number: null, review_started_at: null,
          paused_at: null, current_phase: null, phase_started_at: null,
          needs_info_path: 'features/x/QUESTION.md', priority: 0, target_role: 'developer' },
        { story_id: 'STORY-505', status: 'needs_info', repo: 'tech-project-mapping',
          scope: 'large', prompt: '', enqueued_by: 'test', enqueued_at: '', title: '505',
          claimed_by: null, claimed_at: null, completed_at: null, cancelled_at: null,
          failed_at: null, commit_sha: null, pr_number: null, review_started_at: null,
          paused_at: null, current_phase: null, phase_started_at: null,
          needs_info_path: 'features/y/QUESTION.md', priority: 0, target_role: 'developer' },
      ],
    };
    renderWithQuery(<DispatchQueue />);
    const badge = screen.getByTestId('needs-info-badge');
    expect(badge).toBeTruthy();
    expect(badge.textContent).toContain('2');
    expect(badge.textContent?.toLowerCase()).toContain('needs info');
    // Has accessible label
    expect(badge.getAttribute('aria-label')).toMatch(/2.*need.*input/i);
    // Has pulsing visual cue
    expect(badge.className).toMatch(/animate-pulse/);
  });

  it('clicking the badge does not collapse the queue', () => {
    currentMockData = {
      ...mockQueueData,
      needs_info: [
        { story_id: 'STORY-528', status: 'needs_info', repo: 'advertising-amazon',
          scope: 'large', prompt: '', enqueued_by: 'test', enqueued_at: '', title: '',
          claimed_by: null, claimed_at: null, completed_at: null, cancelled_at: null,
          failed_at: null, commit_sha: null, pr_number: null, review_started_at: null,
          paused_at: null, current_phase: null, phase_started_at: null,
          needs_info_path: 'features/x/QUESTION.md', priority: 0, target_role: 'developer' },
      ],
    };
    renderWithQuery(<DispatchQueue />);
    const badge = screen.getByTestId('needs-info-badge');
    // The queue must remain expanded after click (propagation stopped).
    // stopPropagation prevents toggling the outer expand/collapse button.
    fireEvent.click(badge);
    // Needs_info row should still be in the DOM after the click
    const section = document.querySelector('[data-section="needs-info"]');
    expect(section).toBeTruthy();
  });
});
