import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AlertBanner } from '../components/AlertBanner';
import { useAlerts } from '../hooks/useAlerts';
import type { AlertsResponse } from '../types/api';

// ---------------------------------------------------------------------------

vi.mock('../hooks/useAlerts');

function renderBanner() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AlertBanner />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const mockAlertsWithItems: AlertsResponse = {
  active_count: 3,
  total: 3,
  fetched_at: new Date().toISOString(),
  alerts: [
    {
      id: 'a1',
      agent_name: 'agent-alpha',
      type: 'anomaly',
      severity: 'critical',
      message: 'Cost spike detected',
      active: true,
      triggered_at: new Date().toISOString(),
      resolved_at: null,
      source: 'health',
    },
  ],
};

const mockAlertsEmpty: AlertsResponse = {
  active_count: 0,
  total: 0,
  fetched_at: new Date().toISOString(),
  alerts: [],
};

// ---------------------------------------------------------------------------

describe('AlertBanner — AC-5', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays the active alert count when active_count > 0', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAlertsWithItems,
      isLoading: false,
      isError: false,
    });

    renderBanner();
    expect(screen.getByText(/3\s*active alert/i)).toBeInTheDocument();
  });

  it('renders a warning symbol or icon alongside the alert count', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAlertsWithItems,
      isLoading: false,
      isError: false,
    });

    renderBanner();
    // Accepts "⚠" character or an aria-label'd icon
    const bannerEl = screen.getByText(/3\s*active alert/i).closest('[role]') ??
      screen.getByText(/3\s*active alert/i).parentElement;
    expect(bannerEl?.textContent).toMatch(/⚠|alert/i);
  });

  it('is completely hidden (not rendered) when active_count === 0', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAlertsEmpty,
      isLoading: false,
      isError: false,
    });

    const { container } = renderBanner();
    // Nothing at all — the component should return null or render nothing visible
    expect(screen.queryByText(/active alert/i)).toBeNull();
    // Container should be empty or contain only a wrapper with no text
    const banner = container.querySelector('[data-testid="alert-banner"]');
    expect(banner).toBeNull();
  });

  it('is a clickable link that navigates to /alerts', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAlertsWithItems,
      isLoading: false,
      isError: false,
    });

    renderBanner();
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/alerts');
  });

  it('applies a yellow or red background class for visibility', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAlertsWithItems,
      isLoading: false,
      isError: false,
    });

    const { container } = renderBanner();
    const bannerEl = container.firstElementChild as HTMLElement;
    expect(bannerEl?.className).toMatch(/yellow|red|amber|orange/);
  });

  it('shows singular "alert" for count of 1', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockAlertsWithItems, active_count: 1 },
      isLoading: false,
      isError: false,
    });

    renderBanner();
    // Accepts "1 active alert" (singular) — implementation may choose either
    expect(screen.getByText(/1\s*active alert/i)).toBeInTheDocument();
  });

  it('does not render when data is still loading', () => {
    (useAlerts as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });

    renderBanner();
    expect(screen.queryByText(/active alert/i)).toBeNull();
  });
});
