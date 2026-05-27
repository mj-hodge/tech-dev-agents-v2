/**
 * STORY-736: FoundryCostPanel — unavailable / all-zero data handling.
 *
 * SC-5: FoundryCostPanel shows meaningful data (not an all-zero chart) when
 *       Foundry is in use; shows empty-state messaging when Cost Management is down.
 *
 * RED state (fails until Phase 8):
 *   F736-01 — Panel shows "data unavailable" when all daily entries are zero-cost.
 *             Currently renders an all-zero stacked-area chart instead.
 *
 * PASS already:
 *   F736-02 — Panel shows chart when data has real cost values (regression guard).
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
// Fixtures
// ---------------------------------------------------------------------------

/** 7-day window where every model and every day is $0 (Cost Management unreachable). */
const ALL_ZERO_DATA: FoundryCostData = {
  daily: [
    { date: '2026-04-21', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
    { date: '2026-04-22', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
    { date: '2026-04-23', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
    { date: '2026-04-24', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
    { date: '2026-04-25', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
    { date: '2026-04-26', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
    { date: '2026-04-27', opus_usd: 0, sonnet_usd: 0, haiku_usd: 0, other_usd: 0, total_usd: 0 },
  ],
  fetched_at: '2026-04-27T09:00:00Z',
  cache_age_seconds: 120,
};

/** 7-day window with real Foundry cost values. */
const REAL_COST_DATA: FoundryCostData = {
  daily: [
    { date: '2026-04-21', opus_usd: 450.0, sonnet_usd: 80.0, haiku_usd: 12.0, other_usd: 3, total_usd: 545.0 },
    { date: '2026-04-22', opus_usd: 380.0, sonnet_usd: 90.0, haiku_usd: 15.0, other_usd: 2, total_usd: 487.0 },
    { date: '2026-04-23', opus_usd: 200.0, sonnet_usd: 70.0, haiku_usd: 10.0, other_usd: 5, total_usd: 285.0 },
    { date: '2026-04-24', opus_usd: 300.0, sonnet_usd: 60.0, haiku_usd: 8.0, other_usd: 1, total_usd: 369.0 },
    { date: '2026-04-25', opus_usd: 150.0, sonnet_usd: 55.0, haiku_usd: 6.0, other_usd: 0, total_usd: 211.0 },
    { date: '2026-04-26', opus_usd: 100.0, sonnet_usd: 40.0, haiku_usd: 5.0, other_usd: 0, total_usd: 145.0 },
    { date: '2026-04-27', opus_usd: 80.0,  sonnet_usd: 35.0, haiku_usd: 4.0, other_usd: 0, total_usd: 119.0 },
  ],
  fetched_at: '2026-04-27T09:00:00Z',
  cache_age_seconds: 120,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('FoundryCostPanel — STORY-736 (cost data unavailability)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * F736-01: When all 7 daily entries have total_usd=0, the panel should show an
   *          "unavailable" or "data unavailable" message instead of an all-zero chart.
   *
   * This scenario occurs when Cost Management credentials are missing / unconfigured,
   * causing the cron that populates the foundry cost table to write zeros.
   *
   * RED: Currently the panel renders the chart (with all-zero lines) when daily.length>0.
   *      Phase 8 must detect totalSpend===0 and show the empty-state message.
   */
  it('F736-01: shows "cost data unavailable" message when all daily entries are zero-cost', () => {
    mockUseFoundryCost({ data: ALL_ZERO_DATA, isLoading: false, isError: false });
    renderPanel();

    // Panel must not show the chart when all values are zero
    expect(screen.queryByTestId('foundry-cost-chart')).toBeNull();

    // Panel must show a message indicating data is unavailable
    expect(
      screen.getByText(/unavailable|no foundry data|cost data unavailable/i)
    ).toBeInTheDocument();
  });

  /**
   * F736-02: When data has real cost values, the chart renders normally.
   *
   * Regression guard — the all-zero check must not accidentally suppress real data.
   * Output-variance partner to F736-01.
   *
   * This test PASSES with current implementation and serves as a green regression guard.
   */
  it('F736-02: shows the chart when data has real cost values', () => {
    mockUseFoundryCost({ data: REAL_COST_DATA, isLoading: false, isError: false });
    renderPanel();

    expect(screen.getByTestId('foundry-cost-chart')).toBeInTheDocument();
    // Total across 7 days = 545+487+285+369+211+145+119 = 2161
    expect(screen.getByTestId('foundry-cost-total')).toBeInTheDocument();
  });
});
