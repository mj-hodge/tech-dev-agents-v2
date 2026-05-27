/**
 * STORY-541: Agent Row Display Contract Tests
 *
 * Verifies the locked display contract from the seed. Each status state
 * must render exact text from the contract. RED state until Phase 8.
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { AgentCard } from '../components/AgentCard';
import { formatTokens } from '../components/AgentCard';
import type { AgentSummary } from '../types/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderCard(agent: AgentSummary) {
  return render(
    <MemoryRouter>
      <AgentCard agent={agent} />
    </MemoryRouter>
  );
}

// Base fixture — all fields populated with sensible defaults
const baseAgent: AgentSummary = {
  name: 'dan',
  status: 'idle',
  role: 'developer',
  enabled: true,
  current_story: null,
  current_phase: null,
  today_foundry_usd: 2.22,
  today_sdk_usd: 10.25,
  today_openai_usd: 0.0,
  today_total_usd: 12.47,
  today_cost_usd: 12.47,
  last_activity: new Date().toISOString(),
  uptime_seconds: 3600,
  active_sessions: 0,
  error_count: 0,
  checked_at: new Date().toISOString(),
  busy: false,
  quota: {
    source: 'loki',
    remaining_tokens: 92000,
    current_block_cost_usd: null,
    current_block_tokens: null,
    reset_in_minutes: 252,
    percent_used: null,
    block_start: null,
    block_end: null,
    p90_limit: null,
    sessions_in_block: null,
  },
};

// ---------------------------------------------------------------------------
// B1: Token Formatter (pure logic)
// ---------------------------------------------------------------------------

describe('formatTokens — STORY-541 display contract', () => {
  it('formats thousands with K suffix', () => {
    expect(formatTokens(47_000)).toBe('47K');
  });

  it('formats millions with M suffix and one decimal', () => {
    expect(formatTokens(1_234_567)).toBe('1.2M');
  });

  it('formats exact integers below 1000', () => {
    expect(formatTokens(842)).toBe('842');
  });

  it('returns em-dash for null', () => {
    expect(formatTokens(null)).toBe('—');
  });

  it('returns "0" for zero tokens', () => {
    expect(formatTokens(0)).toBe('0');
  });
});

// ---------------------------------------------------------------------------
// B2: Contract Rendering — each status state
// ---------------------------------------------------------------------------

describe('AgentCard display contract — STORY-541', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders idle state with quota line', () => {
    const agent: AgentSummary = {
      ...baseAgent,
      name: 'derrick',
      status: 'idle',
      quota: {
        ...baseAgent.quota!,
        remaining_tokens: 92000,
        reset_in_minutes: 252,
      },
    };
    renderCard(agent);

    // Status dot + label
    expect(screen.getByText(/●idle/)).toBeInTheDocument();
    // Quota line with absolute remaining
    expect(screen.getByText(/⏱ 92K tokens left/)).toBeInTheDocument();
    expect(screen.getByText(/resets 4h 12m/)).toBeInTheDocument();
    // No claim → em-dash
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders working state with claim line', () => {
    const agent: AgentSummary = {
      ...baseAgent,
      name: 'daisy',
      status: 'working',
      busy: true,
      current_story_id: 'STORY-540',
      current_phase: 'Phase 8',
      quota: {
        ...baseAgent.quota!,
        remaining_tokens: 38_000,
        reset_in_minutes: 252,
      },
    };
    renderCard(agent);

    expect(screen.getByText(/●working/)).toBeInTheDocument();
    expect(screen.getByText(/⏱ 38K tokens left/)).toBeInTheDocument();
    expect(screen.getByText(/📋 STORY-540 Phase 8/)).toBeInTheDocument();
  });

  it('renders paused state with paused-until in claim line', () => {
    const agent: AgentSummary = {
      ...baseAgent,
      name: 'devon',
      status: 'paused',
      busy: true,
      current_story_id: 'STORY-539',
      rate_limited_until: '2026-04-23T19:00:00Z',
      quota: {
        ...baseAgent.quota!,
        source: 'no_data',
        remaining_tokens: null,
        reset_in_minutes: null,
      },
    };
    renderCard(agent);

    expect(screen.getByText(/●paused/)).toBeInTheDocument();
    expect(screen.getByText(/⏱ —/)).toBeInTheDocument();
    expect(screen.getByText(/📋 STORY-539 \(paused until 19:00 UTC\)/)).toBeInTheDocument();
  });

  it('renders stopped state with red dot', () => {
    const agent: AgentSummary = {
      ...baseAgent,
      name: 'dan',
      status: 'stopped',
    };
    renderCard(agent);

    expect(screen.getByText(/●stopped/)).toBeInTheDocument();
  });

  it('renders unreachable state with gray dot', () => {
    const agent: AgentSummary = {
      ...baseAgent,
      name: 'hermes',
      status: 'unreachable',
      quota: null,
    };
    renderCard(agent);

    expect(screen.getByText(/●unreachable/)).toBeInTheDocument();
    // No quota or claim for unreachable
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders zero tokens distinct from no-data', () => {
    // When remaining_tokens == 0 and source is loki, show "0 tokens",
    // not the no-data em-dash
    const agent: AgentSummary = {
      ...baseAgent,
      status: 'idle',
      quota: {
        ...baseAgent.quota!,
        source: 'loki',
        remaining_tokens: 0,
        reset_in_minutes: 60,
      },
    };
    renderCard(agent);

    expect(screen.getByText(/⏱ 0 tokens/)).toBeInTheDocument();
    // Should NOT show the no-data dash
    expect(screen.queryByText('⏱ —')).not.toBeInTheDocument();
  });

  it('does not render old quota-bar-fill element', () => {
    renderCard(baseAgent);
    // The old percentage bar is replaced by the absolute token display
    expect(document.querySelector('[data-testid="quota-bar-fill"]')).toBeNull();
  });
});
