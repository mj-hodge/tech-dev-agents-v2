/**
 * Tests for AgentDetailView — STORY-480 Dashboard Overhaul
 * AC-8: "Current Work" card showing phase progress (N of M) and elapsed time
 *
 * RED state: fails until Phase 8 adds:
 *   - "Current Work" section to AgentDetailView
 *   - phase_total and phase_started_at fields to AgentDetail type
 *   - "N of M" phase progress rendering in detail view
 *   - elapsed time rendering from phase_started_at
 */

import { render, screen } from '@testing-library/react';
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

// STORY-480: new phase tracking fields — cause TypeScript errors until types/api.ts is updated
const mockDetail = {
  name: 'agent-alpha',
  status: 'active' as const,
  current_story: 'STORY-480: Dashboard overhaul',
  current_phase: 'Phase 8 — Implementation',
  today_cost_usd: 12.47,
  last_activity: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  cost_history: [{ date: '2026-04-20', cost: 12.47 }],
  activity_timeline: [],
  context: { teams_link: null, blocker_status: null },
  // STORY-480: new fields
  phase_total: 10,
  phase_started_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(), // 2h ago
} as AgentDetail & { phase_total: number | null; phase_started_at: string | null };

// ---------------------------------------------------------------------------

describe('AgentDetailView — STORY-480 AC-8 (Current Work card)', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    (useAgentActions as ReturnType<typeof vi.fn>).mockReturnValue({
      restart: { mutate: mockRestartMutate, isPending: false },
      pause: { mutate: mockPauseMutate, isPending: false },
    });
  });

  // T480-57: renders "Current Work" section heading
  it('T480-57: renders a "Current Work" section heading', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();

    // Fails until AgentDetailView adds a "Current Work" card/section
    expect(screen.getByText(/current work/i)).toBeInTheDocument();
  });

  // T480-58: renders "8 of 10" phase progress in Current Work card
  it('T480-58: renders "8 of 10" phase progress in the Current Work card', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();

    // Fails until AgentDetailView extracts phase number "8" from "Phase 8 — Implementation"
    // and renders "8 of 10" using phase_total=10
    expect(screen.getByText(/8\s*of\s*10/i)).toBeInTheDocument();
  });

  // T480-59: renders elapsed time "2h" or similar in Current Work card
  it('T480-59: renders elapsed time "2h" or similar when phase_started_at is 2h ago', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockDetail,
      isLoading: false,
      isError: false,
    });

    renderDetailView();

    // Fails until AgentDetailView computes elapsed time from phase_started_at
    // Accept: "2h", "1h 59m", "2h 0m", "120m", "2h ago", etc.
    expect(screen.getByText(/2h|1h\s*\d+m|\d+m\s*ago/i)).toBeInTheDocument();
  });

  // T480-60: Current Work section is absent when current_story is null
  it('T480-60: does not error when current_story is null (Current Work section absent)', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockDetail, current_story: null, current_phase: null },
      isLoading: false,
      isError: false,
    });

    // Should render without throwing
    expect(() => renderDetailView()).not.toThrow();

    // "Current Work" section may be hidden or show a placeholder — just no crash
    const currentWork = screen.queryByText(/current work/i);
    if (currentWork) {
      // If section still renders, it should not show "N of M"
      expect(screen.queryByText(/\d+\s*of\s*\d+/i)).toBeNull();
    }
  });

  // T480-61: phase_total=null — shows phase name without "N of M" (no crash)
  it('T480-61: shows phase name without "N of M" when phase_total is null', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockDetail, phase_total: null },
      isLoading: false,
      isError: false,
    });

    // Should not throw
    expect(() => renderDetailView()).not.toThrow();

    // Phase name should still render, but "N of M" should not
    expect(screen.queryByText(/\d+\s*of\s*\d+/i)).toBeNull();
  });

  // T480-62: phase_started_at=null — shows phase name without elapsed time (no crash)
  it('T480-62: shows phase name without elapsed time when phase_started_at is null', () => {
    (useAgent as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockDetail, phase_started_at: null },
      isLoading: false,
      isError: false,
    });

    // Should not throw
    expect(() => renderDetailView()).not.toThrow();

    // Elapsed time should not render — no "Xh Ym" pattern
    // (phase name and "N of M" may still appear)
    const elapsedEl = screen.queryByText(/^\d+h\s*\d*m?$|^\d+m$/);
    expect(elapsedEl).toBeNull();
  });
});
