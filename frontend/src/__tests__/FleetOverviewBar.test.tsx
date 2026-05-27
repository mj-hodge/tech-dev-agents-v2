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

const mockFleet: FleetOverview = {
  total_daily_spend_usd: 1234.56,
  total_monthly_spend_usd: 5678.90,
  active_agents: 3,
  busy_agents: 2,
  total_agents: 5,
  online_agents: 3,
  idle_agents: 1,
  stuck_agents: 0,
  offline_agents: 2,
  stories_in_progress: 4,
  fleet_health_score: 0.82,
  active_alerts: 0,
  fetched_at: new Date().toISOString(),
  agents: [],
};

// ---------------------------------------------------------------------------

describe('FleetOverviewBar — AC-2', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders KPI cards when data is available', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();

    // There should be 4 distinct card-level elements or at least 4 labelled values
    // Accept headings or accessible labels for each card
    expect(screen.getByText(/total spend/i)).toBeInTheDocument();
    expect(screen.getByText(/active agents/i)).toBeInTheDocument();
    expect(screen.getByText(/busy agents/i)).toBeInTheDocument();
    expect(screen.getByText(/active stories/i)).toBeInTheDocument();
    expect(screen.getByText(/health score/i)).toBeInTheDocument();
  });

  it('formats total_spend as currency $1,234.56', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();
    expect(screen.getByText(/\$1,234\.56/)).toBeInTheDocument();
  });

  it('formats active_agents as "N / M" ratio', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();
    expect(screen.getByText(/3\s*\/\s*5/)).toBeInTheDocument();
  });

  it('formats active_stories as a plain integer', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();
    // "4" should appear — not in a currency or percentage context
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('formats health_score as "82%"', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFleet,
      isLoading: false,
      isError: false,
    });

    renderBar();
    expect(screen.getByText(/82%/)).toBeInTheDocument();
  });

  it('applies green styling when health_score >= 80', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockFleet, fleet_health_score: 0.8 },
      isLoading: false,
      isError: false,
    });

    const { container } = renderBar();
    // The health score card should contain a green Tailwind class
    const healthEl = screen.getByText(/80%/).closest('[class]');
    expect(healthEl?.className).toMatch(/green/);
  });

  it('applies yellow styling when health_score is between 50 and 79', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockFleet, fleet_health_score: 0.65 },
      isLoading: false,
      isError: false,
    });

    renderBar();
    const healthEl = screen.getByText(/65%/).closest('[class]');
    expect(healthEl?.className).toMatch(/yellow/);
  });

  it('applies red styling when health_score < 50', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockFleet, fleet_health_score: 0.3 },
      isLoading: false,
      isError: false,
    });

    renderBar();
    const healthEl = screen.getByText(/30%/).closest('[class]');
    expect(healthEl?.className).toMatch(/red/);
  });

  it('renders loading skeletons when isLoading is true', () => {
    (useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });

    const { container } = renderBar();
    // Expect at least one skeleton/pulse element
    const skeletons = container.querySelectorAll('[class*="animate-pulse"], [data-testid*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });
});
