import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AgentDetailView } from '../components/AgentDetailView';
import { useAgent } from '../hooks/useAgent';
import { useAgentActions } from '../hooks/useAgentActions';
import type { AgentDetail } from '../types/api';

// ---------------------------------------------------------------------------

vi.mock('../hooks/useAgent');
vi.mock('../hooks/useAgentActions');

function renderDetailView(agentName = 'agent-alpha') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/agents/${agentName}`]}>
        <Routes>
          <Route path="/agents/:name" element={<AgentDetailView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const mockRestartMutate = vi.fn();
const mockPauseMutate = vi.fn();

const mockAgentDetail: AgentDetail = {
  name: 'agent-alpha',
  status: 'active',
  current_story: 'STORY-019: Ops Console Dashboard Frontend',
  current_phase: 'Phase 8 — Implementation',
  today_cost_usd: 2.47,
  last_activity: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  cost_history: [
    { date: '2026-03-08', cost: 1.20 },
    { date: '2026-03-09', cost: 2.50 },
    { date: '2026-03-10', cost: 0.80 },
  ],
  activity_timeline: [
    {
      timestamp: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      type: 'phase',
      description: 'Entered Phase 8 — Implementation',
    },
    {
      timestamp: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
      type: 'commit',
      description: 'feat: add LoginPage component',
    },
  ],
  context: {
    teams_link: 'https://teams.microsoft.com/l/channel/example',
    blocker_status: null,
  },
};

// ---------------------------------------------------------------------------

describe('AgentDetailView — AC-4', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    (useAgentActions as ReturnType<typeof vi.fn>).mockReturnValue({
      restart: { mutate: mockRestartMutate, isPending: false },
      pause: { mutate: mockPauseMutate, isPending: false },
    });
  });

  it('renders the agent name as a heading', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    expect(screen.getByText(/agent-alpha/i)).toBeInTheDocument();
  });

  it('renders a cost chart section (CostChart placeholder or chart container)', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    // Either a recharts container or a data-testid sentinel
    const chartEl =
      document.querySelector('[data-testid="cost-chart"]') ??
      document.querySelector('.recharts-wrapper') ??
      screen.queryByText(/cost history/i);
    expect(chartEl).not.toBeNull();
  });

  it('renders activity timeline entries', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    expect(screen.getByText(/Entered Phase 8/i)).toBeInTheDocument();
    expect(screen.getByText(/feat: add LoginPage/i)).toBeInTheDocument();
  });

  it('renders a "Restart" button', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    expect(screen.getByRole('button', { name: /restart/i })).toBeInTheDocument();
  });

  it('renders a "Pause" button', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    expect(screen.getByRole('button', { name: /pause/i })).toBeInTheDocument();
  });

  it('calls restart mutation when "Restart" is clicked and user confirms', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    fireEvent.click(screen.getByRole('button', { name: /restart/i }));

    await waitFor(() => {
      expect(mockRestartMutate).toHaveBeenCalledWith('agent-alpha');
    });
  });

  it('does NOT call restart mutation when user cancels the confirm dialog', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    fireEvent.click(screen.getByRole('button', { name: /restart/i }));

    await waitFor(() => {
      expect(mockRestartMutate).not.toHaveBeenCalled();
    });
  });

  it('calls pause mutation when "Pause" is clicked and user confirms', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    fireEvent.click(screen.getByRole('button', { name: /pause/i }));

    await waitFor(() => {
      expect(mockPauseMutate).toHaveBeenCalledWith('agent-alpha');
    });
  });

  it('disables both action buttons while a mutation is pending', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    (useAgentActions as ReturnType<typeof vi.fn>).mockReturnValue({
      restart: { mutate: mockRestartMutate, isPending: true },
      pause: { mutate: mockPauseMutate, isPending: true },
    });

    renderDetailView();
    expect(screen.getByRole('button', { name: /restart/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /pause/i })).toBeDisabled();
  });

  it('renders a back link to the dashboard', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockAgentDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();
    const backLink = screen.getByRole('link', { name: /back|dashboard/i });
    expect(backLink).toHaveAttribute('href', '/');
  });

  it('renders loading state when isLoading is true', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });

    const { container } = renderDetailView();
    const loadingEl =
      container.querySelector('[class*="animate-pulse"]') ??
      container.querySelector('[class*="skeleton"]') ??
      screen.queryByText(/loading/i);
    expect(loadingEl).not.toBeNull();
  });
});
