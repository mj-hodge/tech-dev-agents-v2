/**
 * STORY-735: AgentCard Quota Display Tests
 *
 * Tests the per-agent quota display enhancements:
 * - SC-2: Fallback indicator when p90_limit is null (asterisk + tooltip)
 * - SC-3: Reset time display from reset_in_minutes
 * - SC-4: sessions_in_block prominence
 * - SC-1: Per-agent differentiation (different agents show different token counts)
 *
 * Pure-logic tests: formatResetTime utility
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import * as AgentCardModule from './AgentCard';
import { AgentCard, formatTokens } from './AgentCard';
import type { AgentSummary, QuotaInfo } from '../types/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal AgentSummary with optional overrides. */
function makeAgent(overrides: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name: 'dan',
    status: 'working',
    busy: false,
    role: 'developer',
    enabled: true,
    last_activity: null,
    uptime_seconds: 3600,
    active_sessions: 1,
    error_count: 0,
    checked_at: new Date().toISOString(),
    current_story: null,
    current_phase: null,
    today_foundry_usd: 1.5,
    today_sdk_usd: 2.0,
    today_openai_usd: 0,
    today_total_usd: 3.5,
    today_cost_usd: 3.5,
    ...overrides,
  };
}

/** Build a QuotaInfo with optional overrides. */
function makeQuota(overrides: Partial<QuotaInfo> = {}): QuotaInfo {
  return {
    source: 'loki',
    percent_used: 62.5,
    reset_in_minutes: 83,
    block_start: '2026-04-27T10:00:00Z',
    block_end: '2026-04-27T15:00:00Z',
    remaining_tokens: 75_000,
    p90_limit: 200_000,
    sessions_in_block: 3,
    current_block_cost_usd: 1.35,
    current_block_tokens: 125_000,
    ...overrides,
  };
}

/** Render AgentCard inside MemoryRouter (required by <Link>). */
function renderCard(agent: AgentSummary) {
  return render(
    <MemoryRouter>
      <AgentCard agent={agent} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Pure logic: formatResetTime (to be exported from AgentCard.tsx in Phase 8)
// ---------------------------------------------------------------------------

/**
 * formatResetTime must be exported from AgentCard.tsx by Phase 8.
 * These tests access it via the wildcard import. They will FAIL
 * until Phase 8 adds the function — that's the RED state.
 */
function getFormatResetTime(): (m: number | null | undefined) => string {
  const fn = (AgentCardModule as Record<string, unknown>)['formatResetTime'];
  if (typeof fn !== 'function') {
    throw new Error('formatResetTime is not yet exported from AgentCard.tsx — Phase 8 will add it');
  }
  return fn as (m: number | null | undefined) => string;
}

describe('formatResetTime (pure logic)', () => {
  it('converts 83 minutes to "1h 23m"', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(83)).toBe('1h 23m');
  });

  it('converts 60 minutes to "1h 0m"', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(60)).toBe('1h 0m');
  });

  it('converts 45 minutes to "45m"', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(45)).toBe('45m');
  });

  it('converts 0 minutes to "0m"', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(0)).toBe('0m');
  });

  it('returns fallback string for null input', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(null)).toBe('—');
  });

  it('returns fallback string for undefined input', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(undefined)).toBe('—');
  });

  it('converts 300 minutes (full block) to "5h 0m"', () => {
    const formatResetTime = getFormatResetTime();
    expect(formatResetTime(300)).toBe('5h 0m');
  });
});

// ---------------------------------------------------------------------------
// Pure logic: formatTokens (existing export)
// ---------------------------------------------------------------------------

describe('formatTokens (existing)', () => {
  it('formats null as dash', () => {
    expect(formatTokens(null)).toBe('—');
  });

  it('formats 0 as "0"', () => {
    expect(formatTokens(0)).toBe('0');
  });

  it('formats 125000 as "125K"', () => {
    expect(formatTokens(125_000)).toBe('125K');
  });

  it('formats 1500000 as "1.5M"', () => {
    expect(formatTokens(1_500_000)).toBe('1.5M');
  });

  it('formats 500 as "500"', () => {
    expect(formatTokens(500)).toBe('500');
  });
});

// ---------------------------------------------------------------------------
// SC-2: Fallback indicator when p90_limit is null
// ---------------------------------------------------------------------------

describe('SC-2: p90_limit null fallback indicator', () => {
  it('shows estimated-baseline indicator when p90_limit is null', () => {
    const agent = makeAgent({
      quota: makeQuota({ p90_limit: null, current_block_tokens: 125_000 }),
    });
    renderCard(agent);

    // Expect an asterisk or indicator element marking the limit as estimated
    const indicator = screen.getByTestId('quota-estimated-indicator');
    expect(indicator).toBeInTheDocument();
  });

  it('indicator has tooltip explaining 200K estimated baseline', () => {
    const agent = makeAgent({
      quota: makeQuota({ p90_limit: null }),
    });
    renderCard(agent);

    const indicator = screen.getByTestId('quota-estimated-indicator');
    // Tooltip should mention estimated baseline and STORY-543
    expect(indicator.getAttribute('title')).toMatch(/estimated/i);
    expect(indicator.getAttribute('title')).toMatch(/200K|200,000|200k/i);
  });

  it('does NOT show estimated indicator when p90_limit has a value', () => {
    const agent = makeAgent({
      quota: makeQuota({ p90_limit: 180_000 }),
    });
    renderCard(agent);

    expect(screen.queryByTestId('quota-estimated-indicator')).not.toBeInTheDocument();
  });

  it('does NOT show estimated indicator when quota is null', () => {
    const agent = makeAgent({ quota: null });
    renderCard(agent);

    expect(screen.queryByTestId('quota-estimated-indicator')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SC-3: Reset time display from reset_in_minutes
// ---------------------------------------------------------------------------

describe('SC-3: reset time display', () => {
  it('shows formatted reset time on the quota card', () => {
    const agent = makeAgent({
      quota: makeQuota({ reset_in_minutes: 83 }),
    });
    renderCard(agent);

    // Should display "1h 23m" somewhere on the card
    expect(screen.getByText(/1h 23m/)).toBeInTheDocument();
  });

  it('shows reset time label "resets in"', () => {
    const agent = makeAgent({
      quota: makeQuota({ reset_in_minutes: 83 }),
    });
    renderCard(agent);

    expect(screen.getByText(/resets? in/i)).toBeInTheDocument();
  });

  it('handles reset_in_minutes = 0 (block about to reset)', () => {
    const agent = makeAgent({
      quota: makeQuota({ reset_in_minutes: 0 }),
    });
    renderCard(agent);

    expect(screen.getByText(/0m/)).toBeInTheDocument();
  });

  it('falls back gracefully when reset_in_minutes is null', () => {
    const agent = makeAgent({
      quota: makeQuota({ reset_in_minutes: null }),
    });
    renderCard(agent);

    // Should not crash; block_end fallback or dash should render
    // Must NOT show "resets in NaN" or similar
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it('does not render reset time when quota is null', () => {
    const agent = makeAgent({ quota: null });
    renderCard(agent);

    expect(screen.queryByText(/resets? in/i)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SC-4: sessions_in_block prominence
// ---------------------------------------------------------------------------

describe('SC-4: sessions_in_block display', () => {
  it('shows sessions count on the card', () => {
    const agent = makeAgent({
      quota: makeQuota({ sessions_in_block: 3 }),
    });
    renderCard(agent);

    expect(screen.getByText(/3 sessions?/i)).toBeInTheDocument();
  });

  it('shows sessions_in_block = 1 as singular or numeric', () => {
    const agent = makeAgent({
      quota: makeQuota({ sessions_in_block: 1 }),
    });
    renderCard(agent);

    expect(screen.getByText(/1 session/i)).toBeInTheDocument();
  });

  it('shows sessions_in_block = 0', () => {
    const agent = makeAgent({
      quota: makeQuota({ sessions_in_block: 0 }),
    });
    renderCard(agent);

    expect(screen.getByText(/0 sessions?/i)).toBeInTheDocument();
  });

  it('does not render sessions when quota is null', () => {
    const agent = makeAgent({ quota: null });
    renderCard(agent);

    expect(screen.queryByText(/sessions?/i)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SC-1: Per-agent current_block_tokens differentiation
// ---------------------------------------------------------------------------

describe('SC-1: current_block_tokens per-agent display', () => {
  it('renders current_block_tokens as formatted value on the card', () => {
    const agent = makeAgent({
      quota: makeQuota({ current_block_tokens: 125_000 }),
    });
    renderCard(agent);

    // Should show "125K" or "125,000" somewhere in the card
    expect(screen.getByText(/125K|125,000/)).toBeInTheDocument();
  });

  it('two agents with different tokens render different values', () => {
    const { unmount } = renderCard(
      makeAgent({
        name: 'dan',
        quota: makeQuota({ current_block_tokens: 125_000 }),
      }),
    );
    const danText = document.body.textContent ?? '';
    unmount();

    renderCard(
      makeAgent({
        name: 'derrick',
        quota: makeQuota({ current_block_tokens: 42_000 }),
      }),
    );
    const derrickText = document.body.textContent ?? '';

    // The two cards must render different token strings
    expect(danText).not.toBe(derrickText);
    expect(danText).toMatch(/125K|125,000/);
    expect(derrickText).toMatch(/42K|42,000/);
  });

  it('renders current_block_tokens alongside the limit denominator', () => {
    const agent = makeAgent({
      quota: makeQuota({
        current_block_tokens: 125_000,
        p90_limit: null, // fallback to ~200K
      }),
    });
    renderCard(agent);

    // Should show something like "125K / ~200K*" or "125K / 200K"
    expect(screen.getByText(/125K/)).toBeInTheDocument();
    expect(screen.getByText(/200K/)).toBeInTheDocument();
  });

  it('does not render token count when quota is null', () => {
    const agent = makeAgent({ quota: null });
    renderCard(agent);

    // No quota section at all
    expect(screen.queryByTestId('quota-estimated-indicator')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Edge cases: quota rendering robustness
// ---------------------------------------------------------------------------

describe('Quota edge cases', () => {
  it('renders card without crashing when quota is undefined', () => {
    const agent = makeAgent();
    // quota defaults to undefined (not in overrides)
    expect(() => renderCard(agent)).not.toThrow();
  });

  it('renders card without crashing when all quota fields are null', () => {
    const agent = makeAgent({
      quota: {
        source: 'no_data',
        percent_used: null,
        reset_in_minutes: null,
        block_start: null,
        block_end: null,
        remaining_tokens: null,
        p90_limit: null,
        sessions_in_block: null,
        current_block_cost_usd: null,
        current_block_tokens: null,
      },
    });
    expect(() => renderCard(agent)).not.toThrow();
  });

  it('renders card with source="no_data" without quota details', () => {
    const agent = makeAgent({
      quota: makeQuota({
        source: 'no_data',
        current_block_tokens: null,
        sessions_in_block: null,
        reset_in_minutes: null,
      }),
    });
    renderCard(agent);

    // Should not show sessions or token counts when data unavailable
    expect(screen.queryByText(/sessions?/i)).not.toBeInTheDocument();
  });
});
