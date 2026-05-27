/**
 * Tests for AgentCard — STORY-480 Dashboard Overhaul
 * AC-3: Presence dot on agent card
 * AC-4: Phase progress (N of M) and elapsed time display
 *
 * RED state: fails until Phase 8 adds:
 *   - presenceState prop to AgentCard
 *   - presence dot with color coding
 *   - phase_total and phase_started_at fields to AgentSummary type
 *   - "N of M" phase progress rendering
 *   - elapsed time rendering
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { AgentCard } from '../components/AgentCard';
import type { AgentSummary, PresenceState } from '../types/api';

// ---------------------------------------------------------------------------

// STORY-480: new phase tracking fields — cause TypeScript errors until types/api.ts is updated
const baseAgent = {
  name: 'agent-alpha',
  status: 'active' as const,
  role: 'developer',
  enabled: true,
  current_story: 'STORY-480: Dashboard overhaul',
  current_phase: 'Phase 8',
  today_foundry_usd: 2.22,
  today_sdk_usd: 10.25,
  today_openai_usd: 0.0,
  today_total_usd: 12.47,
  today_cost_usd: 12.47,
  last_activity: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  uptime_seconds: 3600,
  active_sessions: 1,
  error_count: 0,
  checked_at: new Date().toISOString(),
  // STORY-480: new fields
  phase_total: 10,
  phase_started_at: new Date(Date.now() - 90 * 60 * 1000).toISOString(), // 90 min ago
} as AgentSummary & { phase_total: number | null; phase_started_at: string | null };

function renderCard(agent: typeof baseAgent, presenceState?: PresenceState) {
  return render(
    <MemoryRouter>
      <AgentCard agent={agent as any} presenceState={presenceState} />
    </MemoryRouter>
  );
}

// ---------------------------------------------------------------------------

describe('AgentCard — STORY-480 AC-3 (presence dot)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // T480-31: renders green presence dot when presenceState="working"
  it('T480-31: renders green presence dot when presenceState="working"', () => {
    const { container } = renderCard(baseAgent, 'working');

    // Fails until AgentCard accepts presenceState prop and renders colored dot
    const greenDot = container.querySelector(
      '[data-testid="presence-dot"], [class*="presence"]'
    );
    expect(greenDot).not.toBeNull();
    expect(greenDot!.className).toMatch(/green/);
  });

  // T480-32: renders gray/muted dot when presenceState="idle"
  it('T480-32: renders gray or muted dot when presenceState="idle"', () => {
    const { container } = renderCard(baseAgent, 'idle');

    // Fails until AgentCard renders gray dot for idle state
    const dot = container.querySelector(
      '[data-testid="presence-dot"], [class*="presence"]'
    );
    expect(dot).not.toBeNull();
    expect(dot!.className).toMatch(/gray|muted|slate/);
  });

  // T480-33: renders yellow dot when presenceState="rate_limited"
  it('T480-33: renders yellow dot when presenceState="rate_limited"', () => {
    const { container } = renderCard(baseAgent, 'rate_limited');

    // Fails until AgentCard renders yellow dot for rate_limited state
    const dot = container.querySelector(
      '[data-testid="presence-dot"], [class*="presence"]'
    );
    expect(dot).not.toBeNull();
    expect(dot!.className).toMatch(/yellow|amber/);
  });

  // T480-34: renders red dot when presenceState="offline"
  it('T480-34: renders red dot when presenceState="offline"', () => {
    const { container } = renderCard(baseAgent, 'offline');

    // Fails until AgentCard renders red dot for offline state
    const dot = container.querySelector(
      '[data-testid="presence-dot"], [class*="presence"]'
    );
    expect(dot).not.toBeNull();
    expect(dot!.className).toMatch(/red/);
  });

  // T480-35: renders no presence dot when presenceState is undefined
  it('T480-35: renders no presence dot when presenceState is undefined', () => {
    const { container } = renderCard(baseAgent, undefined);

    // Should not render a presence dot at all when prop is omitted
    const dot = container.querySelector('[data-testid="presence-dot"]');
    expect(dot).toBeNull();
  });
});

// ---------------------------------------------------------------------------

describe('AgentCard — STORY-480 AC-4 (phase progress + elapsed time)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // T480-36: renders "8 of 10" phase progress
  it('T480-36: renders "8 of 10" phase progress when current_phase="Phase 8" and phase_total=10', () => {
    renderCard(baseAgent);

    // Fails until AgentCard extracts phase number from "Phase 8" and renders "8 of 10"
    expect(screen.getByText(/8\s*of\s*10/i)).toBeInTheDocument();
  });

  // T480-37: renders elapsed time like "1h 30m" or "90m" when phase_started_at is 90min ago
  it('T480-37: renders elapsed time "1h 30m" or similar when phase_started_at is 90min ago', () => {
    renderCard(baseAgent);

    // Fails until AgentCard computes and renders elapsed time from phase_started_at
    // Accept formats: "1h 30m", "90m", "1h", "2h", etc.
    const timeEl = screen.queryByText(/\d+h|\d+m/);
    expect(timeEl).not.toBeNull();
  });

  // T480-38: renders "—" or nothing for phase progress when phase_total is null
  it('T480-38: renders without crash when phase_total is null (shows phase name only)', () => {
    const agentNoTotal = {
      ...baseAgent,
      phase_total: null,
    };

    // Should not throw — "N of M" just won't appear
    expect(() => renderCard(agentNoTotal)).not.toThrow();
    // "Phase 8" phase name should still appear
    expect(screen.getByText(/Phase 8/i)).toBeInTheDocument();
    // "N of M" should NOT appear when phase_total is null
    expect(screen.queryByText(/\d+\s*of\s*\d+/i)).toBeNull();
  });
});
