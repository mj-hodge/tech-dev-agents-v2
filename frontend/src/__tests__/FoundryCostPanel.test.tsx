/**
 * STORY-576: FoundryCostPanel — Vitest unit tests.
 *
 * F01-F07: Chart rendering, warning banner, loading/error/empty states.
 * All tests are RED until Phase 8 implements FoundryCostPanel.tsx + useFoundryCost.ts.
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FoundryCostPanel } from '../components/FoundryCostPanel';
import { useFoundryCost } from '../hooks/useFoundryCost';
import type { FoundryCostData } from '../hooks/useFoundryCost';

// ---------------------------------------------------------------------------
// Mock the data hook
// ---------------------------------------------------------------------------

vi.mock('../hooks/useFoundryCost');

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FoundryCostPanel />
    </QueryClientProvider>
  );
}

function mockUseFoundryCost(value: {
  data: FoundryCostData | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  (useFoundryCost as ReturnType<typeof vi.fn>).mockReturnValue(value);
}

// ---------------------------------------------------------------------------
// Fixtures — 7-day cost data matching the seed scenario
// ---------------------------------------------------------------------------

const MOCK_COST_DATA: FoundryCostData = {
  daily: [
    { date: '2026-04-18', opus_usd: 1486.23, sonnet_usd: 0, haiku_usd: 0, other_usd: 12.5, total_usd: 1498.73 },
    { date: '2026-04-19', opus_usd: 900.0, sonnet_usd: 45.2, haiku_usd: 8.1, other_usd: 5, total_usd: 958.3 },
    { date: '2026-04-20', opus_usd: 750.0, sonnet_usd: 80.0, haiku_usd: 12.0, other_usd: 3, total_usd: 845.0 },
    { date: '2026-04-21', opus_usd: 400.0, sonnet_usd: 100.0, haiku_usd: 15.0, other_usd: 2, total_usd: 517.0 },
    { date: '2026-04-22', opus_usd: 200.0, sonnet_usd: 120.0, haiku_usd: 20.0, other_usd: 1, total_usd: 341.0 },
    { date: '2026-04-23', opus_usd: 80.0, sonnet_usd: 90.0, haiku_usd: 18.0, other_usd: 5, total_usd: 193.0 },
    { date: '2026-04-24', opus_usd: 150.0, sonnet_usd: 60.0, haiku_usd: 10.0, other_usd: 2, total_usd: 222.0 },
  ],
  fetched_at: '2026-04-24T14:00:01Z',
  cache_age_seconds: 3601,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('FoundryCostPanel — STORY-576', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('F01: renders the chart container with all three model series', () => {
    mockUseFoundryCost({ data: MOCK_COST_DATA, isLoading: false, isError: false });
    renderPanel();

    expect(screen.getByTestId('foundry-cost-panel')).toBeInTheDocument();
    expect(screen.getByTestId('foundry-cost-chart')).toBeInTheDocument();
    // Recharts renders Area elements — verify the chart container exists
    const chart = screen.getByTestId('foundry-cost-chart');
    expect(chart.querySelector('.recharts-area')).toBeTruthy();
  });

  it('F02: displays the 7-day total spend', () => {
    mockUseFoundryCost({ data: MOCK_COST_DATA, isLoading: false, isError: false });
    renderPanel();

    // Total across 7 days = 1498.73 + 958.30 + 845 + 517 + 341 + 193 + 222 = 4575.03
    expect(screen.getByTestId('foundry-cost-total')).toBeInTheDocument();
    expect(screen.getByText(/\$4,575/)).toBeInTheDocument();
  });

  it('F03: shows warning banner when today total exceeds $200', () => {
    // Today (last entry) has total_usd = 222.00 > 200
    mockUseFoundryCost({ data: MOCK_COST_DATA, isLoading: false, isError: false });
    renderPanel();

    expect(screen.getByTestId('foundry-cost-warning')).toBeInTheDocument();
    expect(screen.getByText(/exceeds \$200/i)).toBeInTheDocument();
  });

  it('F04: hides warning banner when today total is under $200', () => {
    const lowCostData: FoundryCostData = {
      ...MOCK_COST_DATA,
      daily: MOCK_COST_DATA.daily.map((d, i) =>
        i === MOCK_COST_DATA.daily.length - 1
          ? { ...d, opus_usd: 50, total_usd: 122.0 }
          : d
      ),
    };
    mockUseFoundryCost({ data: lowCostData, isLoading: false, isError: false });
    renderPanel();

    expect(screen.queryByTestId('foundry-cost-warning')).not.toBeInTheDocument();
  });

  it('F05: handles empty data gracefully', () => {
    const emptyData: FoundryCostData = {
      daily: [],
      fetched_at: null,
      cache_age_seconds: 0,
    };
    mockUseFoundryCost({ data: emptyData, isLoading: false, isError: false });
    renderPanel();

    expect(screen.getByText(/no cost data/i)).toBeInTheDocument();
  });

  it('F06: shows loading state', () => {
    mockUseFoundryCost({ data: undefined, isLoading: true, isError: false });
    const { container } = renderPanel();

    // Expect skeleton/pulse element
    const skeletons = container.querySelectorAll(
      '[class*="animate-pulse"], [data-testid*="skeleton"]'
    );
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('F07: shows error state', () => {
    mockUseFoundryCost({ data: undefined, isLoading: false, isError: true });
    renderPanel();

    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
  });
});
