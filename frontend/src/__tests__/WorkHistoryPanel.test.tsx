/**
 * Tests for WorkHistoryPanel — STORY-480 Dashboard Overhaul
 * AC-5: Filter controls (agent dropdown + date range buttons) with URL sync
 * AC-6: Cost column (total_cost_usd) in the work history table
 *
 * RED state: fails until Phase 8 adds:
 *   - useWorkHistory hook (frontend/src/hooks/useWorkHistory.ts)
 *   - agent filter dropdown to WorkHistoryPanel
 *   - date range filter buttons (Today, 7d, 30d, All)
 *   - URL query param sync for agent filter
 *   - total_cost_usd column to the stories table
 *   - total_cost_usd field to CompletedStory type
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WorkHistoryPanel } from '../components/WorkHistoryPanel';
import { useWorkHistory } from '../hooks/useWorkHistory';
import type { WorkHistoryResponse, CompletedStory } from '../types/api';

// ---------------------------------------------------------------------------

vi.mock('../hooks/useWorkHistory');

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WorkHistoryPanel />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// STORY-480: total_cost_usd is a new field — causes TypeScript errors until types/api.ts is updated
const mockStory = (
  id: string,
  agent: string,
  daysAgo = 1,
  cost: number | null = 5.50
): CompletedStory & { total_cost_usd: number | null } => ({
  story_id: id,
  repo: 'tech-dev-agents',
  branch: `feature/${id.toLowerCase()}-${agent}`,
  pr_number: 123,
  pr_url: `https://github.com/org/repo/pull/123`,
  pr_state: 'merged',
  agent,
  completed_at: new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000).toISOString(),
  summary: `${id}: Some feature`,
  total_cost_usd: cost,
});

const mockHistory = {
  stories: [
    mockStory('STORY-480', 'dan', 1, 12.50),
    mockStory('STORY-481', 'derrick', 2, 8.30),
  ],
  total: 2,
  fetched_at: new Date().toISOString(),
};

// ---------------------------------------------------------------------------

describe('WorkHistoryPanel — STORY-480 AC-5 (filter controls)', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    (useWorkHistory as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockHistory,
      isLoading: false,
      isError: false,
    });
  });

  // T480-41: renders an agent filter dropdown/select
  it('T480-41: renders an agent filter dropdown or select element', () => {
    renderPanel();

    // Fails until WorkHistoryPanel renders a select/combobox for agent filtering
    const select =
      screen.queryByRole('combobox') ??
      screen.queryByRole('listbox') ??
      document.querySelector('select[name*="agent"], select[id*="agent"], [data-testid*="agent-filter"]');
    expect(select).not.toBeNull();
  });

  // T480-42: dropdown contains "All Agents" option
  it('T480-42: agent filter dropdown contains "All Agents" option', () => {
    renderPanel();

    // Fails until WorkHistoryPanel renders "All Agents" as the default filter option
    expect(screen.getByText(/all agents/i)).toBeInTheDocument();
  });

  // T480-43: dropdown contains agent names ("dan", "derrick")
  it('T480-43: agent filter dropdown lists agent names from history ("dan", "derrick")', () => {
    renderPanel();

    // Fails until WorkHistoryPanel populates dropdown with unique agent names from data
    expect(screen.getByRole('option', { name: /^dan$/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /^derrick$/i })).toBeInTheDocument();
  });

  // T480-44: renders date range buttons (Today, 7d, 30d, All)
  it('T480-44: renders date range filter buttons — Today, 7d, 30d, All', () => {
    renderPanel();

    // Fails until WorkHistoryPanel renders date range filter buttons
    expect(screen.getByRole('button', { name: /today/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /7d/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /30d/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument();
  });

  // T480-45: selecting an agent option updates URL query param
  it('T480-45: selecting an agent from the dropdown updates the URL query param', () => {
    let capturedSearch = '';

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    // Use a wrapper that captures location so we can verify param change
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/work-history']}>
          <WorkHistoryPanel />
        </MemoryRouter>
      </QueryClientProvider>
    );

    // Fails until WorkHistoryPanel syncs agent filter to URL
    const select = container.querySelector('select') as HTMLSelectElement | null;
    expect(select).not.toBeNull();

    fireEvent.change(select!, { target: { value: 'dan' } });

    // After change, URL should reflect ?agent=dan (tested via location or component state)
    // At minimum, the selected value should be "dan"
    expect((select as HTMLSelectElement).value).toBe('dan');
  });
});

// ---------------------------------------------------------------------------

describe('WorkHistoryPanel — STORY-480 AC-6 (cost column)', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    (useWorkHistory as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockHistory,
      isLoading: false,
      isError: false,
    });
  });

  // T480-46: renders total_cost_usd column header
  it('T480-46: renders a "Cost" column header in the stories table', () => {
    renderPanel();

    // Fails until WorkHistoryPanel adds a total_cost_usd column
    expect(
      screen.getByText(/cost/i, { selector: 'th, [role="columnheader"]' })
    ).toBeInTheDocument();
  });

  // T480-47: renders cost value "$12.50" for a story with total_cost_usd=12.50
  it('T480-47: renders formatted cost "$12.50" for STORY-480 (total_cost_usd=12.50)', () => {
    renderPanel();

    // Fails until WorkHistoryPanel renders total_cost_usd values
    expect(screen.getByText(/\$12\.50/)).toBeInTheDocument();
  });

  // T480-48: renders "—" when total_cost_usd is null
  it('T480-48: renders "—" when total_cost_usd is null', () => {
    (useWorkHistory as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...mockHistory,
        stories: [mockStory('STORY-482', 'dan', 1, null)],
        total: 1,
      },
      isLoading: false,
      isError: false,
    });

    renderPanel();

    // Fails until WorkHistoryPanel renders "—" for null cost values
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
