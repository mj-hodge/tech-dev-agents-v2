/**
 * Tests for FleetOverviewBar — STORY-480 Dashboard Overhaul
 * AC-1: Budget Used card with progress bar
 * AC-2 update: Foundry/SDK/OpenAI cost breakdown text under Daily Spend
 *
 * RED state: fails until Phase 8 adds:
 *   - "Budget Used" card to FleetOverviewBar
 *   - progress bar element (role="progressbar" or data-testid="budget-progress")
 *   - budget percentage display
 *   - Foundry/SDK/OpenAI breakdown text
 *   - daily_budget_usd, daily_foundry_usd, daily_sdk_usd, daily_openai_usd fields in FleetOverview type
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FleetOverviewBar } from '../components/FleetOverviewBar';
import { useFleet } from '../hooks/useFleet';
import type { FleetOverview } from '../types/api';

// ---------------------------------------------------------------------------

vi.mock('../hooks/useFleet');

function renderBar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FleetOverviewBar />
    </QueryClientProvider>
  );
}

// STORY-480: New fields — these cause TypeScript errors until types/api.ts is updated
const mockFleet = {
  total_daily_spend_usd: 24.50,
  total_monthly_spend_usd: 456.78,
  active_agents: 2,
  busy_agents: 1,
  total_agents: 2,
  online_agents: 2,
  idle_agents: 0,
  stuck_agents: 0,
  offline_agents: 0,
  stories_in_progress: 3,
  fleet_health_score: 0.90,
  active_alerts: 0,
  fetched_at: new Date().toISOString(),
  agents: [],
  // STORY-480: New fields — these will cause TypeScript errors until types/api.ts is updated
  daily_budget_usd: 50.0,
  daily_foundry_usd: 15.22,
  daily_sdk_usd: 8.75,
  daily_openai_usd: 0.53,
} as FleetOverview & {
  daily_budget_usd: number;
  daily_foundry_usd: number;
  daily_sdk_usd: number;
  daily_openai_usd: number;
};

// ---------------------------------------------------------------------------

describe('FleetOverviewBar — STORY-480 AC-1 (Budget Used card)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // T480-21: renders "Budget Used" label or heading
  it('T480-21: renders "Budget Used" label or heading', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();

    // Fails until FleetOverviewBar renders a "Budget Used" card
    expect(screen.getByText(/budget used/i)).toBeInTheDocument();
  });

  // T480-22: renders a progress bar element
  it('T480-22: renders a progress bar element for budget utilization', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    const { container } = renderBar();

    // Fails until FleetOverviewBar renders a progress bar
    const progressBar =
      screen.queryByRole('progressbar') ??
      container.querySelector('[data-testid="budget-progress"]');
    expect(progressBar).not.toBeNull();
  });

  // T480-23: shows budget percentage "49%" (24.50 / 50.0 = 49%)
  it('T480-23: shows budget percentage "49%" (24.50/50.0)', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();

    // Fails until FleetOverviewBar calculates and renders budget %
    expect(screen.getByText(/49%/)).toBeInTheDocument();
  });

  // T480-27: budget progress bar color is green when under 80%
  it('T480-27: budget progress bar has green color when under 80% utilized', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet, // 24.50 / 50.0 = 49%
      isLoading: false,
      isError: false,
    });

    const { container } = renderBar();

    // Fails until FleetOverviewBar applies green styling when budget < 80%
    const progressEl =
      screen.queryByRole('progressbar') ??
      container.querySelector('[data-testid="budget-progress"]');
    expect(progressEl).not.toBeNull();
    expect(progressEl!.className).toMatch(/green/);
  });

  // T480-28: budget progress bar shows warning color when over 90%
  it('T480-28: budget progress bar has warning/red color when over 90% utilized', () => {
    const highSpendFleet = {
      ...mockFleet,
      total_daily_spend_usd: 46.0, // 46.0 / 50.0 = 92%
    };

    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: highSpendFleet,
      isLoading: false,
      isError: false,
    });

    const { container } = renderBar();

    // Fails until FleetOverviewBar applies warning styling when budget > 90%
    const progressEl =
      screen.queryByRole('progressbar') ??
      container.querySelector('[data-testid="budget-progress"]');
    expect(progressEl).not.toBeNull();
    expect(progressEl!.className).toMatch(/red|orange|yellow|warning/);
  });
});

// ---------------------------------------------------------------------------

describe('FleetOverviewBar — STORY-480 AC-2 update (cost breakdown text)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // T480-24: renders Foundry cost breakdown text
  it('T480-24: renders Foundry cost breakdown "$15.22" near "Foundry" label', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();

    // Fails until FleetOverviewBar renders Foundry breakdown under Daily Spend
    expect(screen.getByText(/foundry/i)).toBeInTheDocument();
    expect(screen.getByText(/\$15\.22/)).toBeInTheDocument();
  });

  // T480-25: renders SDK cost breakdown text
  it('T480-25: renders SDK cost breakdown "$8.75" near "SDK" label', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();

    // Fails until FleetOverviewBar renders SDK breakdown under Daily Spend
    expect(screen.getByText(/\bsdk\b/i)).toBeInTheDocument();
    expect(screen.getByText(/\$8\.75/)).toBeInTheDocument();
  });

  // T480-26: renders OpenAI cost breakdown text
  it('T480-26: renders OpenAI cost breakdown "$0.53" near "OpenAI" label', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();

    // Fails until FleetOverviewBar renders OpenAI breakdown under Daily Spend
    expect(screen.getByText(/openai/i)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.53/)).toBeInTheDocument();
  });
});
