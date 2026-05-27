/**
 * STORY-736: AgentCard — foundry_cost_status rendering tests.
 *
 * SC-2: When Cost Management is unavailable/unconfigured, the UI shows "cost data
 *       unavailable" (not $0.00) with a tooltip explaining the issue.
 * SC-7: Cost Management 24–48h lag is rendered as a "stale" indicator rather than
 *       indistinguishable from $0.
 *
 * RED state (fails until Phase 8):
 *   - AgentCard does not yet branch on foundry_cost_status
 *   - Warning icon fires whenever foundry=0 && sdk>0, ignoring cost_status
 *   - No "—" or "stale" indicator exists in the component today
 *
 * Tests:
 *   D01 — "—" replaces $0.00 when cost_status is "unavailable"
 *   D02 — Tooltip contains "unavailable" text when cost_status is "unavailable"
 *   D03 — Warning icon NOT shown when cost_status is "unavailable" (stop crying wolf)
 *   D04 — Stale indicator visible when cost_status is "stale"
 *   D05 — $0.00 shown (not "—") when cost_status is "no_usage" (legitimate zero)
 *   D06 — Warning icon only shown when cost_status=="ok" AND foundry==0 AND sdk>0
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { AgentCard } from '../components/AgentCard';
import type { AgentSummary } from '../types/api';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Base agent with healthy non-zero Foundry cost. */
const baseAgent: AgentSummary & { foundry_cost_status?: string | null } = {
  name: 'dan',
  status: 'working',
  role: 'developer',
  enabled: true,
  current_story: 'STORY-736: Fix Foundry Cost Display',
  current_phase: 'Phase 8',
  today_foundry_usd: 3.50,
  today_sdk_usd: 10.25,
  today_openai_usd: 0.0,
  today_total_usd: 13.75,
  today_cost_usd: 13.75,
  last_activity: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
  uptime_seconds: 7200,
  active_sessions: 1,
  error_count: 0,
  checked_at: new Date(Date.now() - 30 * 1000).toISOString(),
  foundry_cost_status: 'ok',
};

/** Agent where Cost Management is unavailable — foundry=0 but sdk>0. */
const unavailableAgent: AgentSummary & { foundry_cost_status?: string | null } = {
  ...baseAgent,
  today_foundry_usd: 0,
  today_sdk_usd: 10.25,
  today_total_usd: 10.25,
  today_cost_usd: 10.25,
  foundry_cost_status: 'unavailable',
};

/** Agent where Azure reports legitimately $0 Foundry spend (no_usage). */
const noUsageAgent: AgentSummary & { foundry_cost_status?: string | null } = {
  ...baseAgent,
  today_foundry_usd: 0,
  today_sdk_usd: 0,
  today_total_usd: 0,
  today_cost_usd: 0,
  foundry_cost_status: 'no_usage',
};

/** Agent with stale Cost Management data (24–48h Azure reporting lag). */
const staleAgent: AgentSummary & { foundry_cost_status?: string | null } = {
  ...baseAgent,
  today_foundry_usd: 1.22,
  today_sdk_usd: 5.0,
  today_total_usd: 6.22,
  today_cost_usd: 6.22,
  foundry_cost_status: 'stale',
};

/** Agent with ok status but zero Foundry and positive SDK — the "real" warning case. */
const zeroFoundryOkStatusAgent: AgentSummary & { foundry_cost_status?: string | null } = {
  ...baseAgent,
  today_foundry_usd: 0,
  today_sdk_usd: 10.25,
  today_total_usd: 10.25,
  today_cost_usd: 10.25,
  foundry_cost_status: 'ok',
};

function renderCard(agent: AgentSummary) {
  return render(
    <MemoryRouter>
      <AgentCard agent={agent} />
    </MemoryRouter>
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AgentCard — STORY-736 (cost_status rendering)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * D01: When cost_status is "unavailable", the Azure Spend section should show
   *      "—" (em dash) instead of $0.00.
   *
   * RED: Component currently calls formatCurrency(0) → "$0.00" regardless of status.
   */
  it('D01: renders "—" instead of $0.00 when foundry_cost_status is "unavailable"', () => {
    renderCard(unavailableAgent as AgentSummary);

    // The Azure Spend value should be "—" not "$0.00"
    // Use the Azure Spend container to scope the assertion
    const azureSpend = screen.getByText(/Azure Spend/i).closest('span');
    const valueEl = azureSpend?.parentElement;
    expect(valueEl?.textContent).toMatch(/—/);
    expect(valueEl?.textContent).not.toMatch(/\$0\.00/);
  });

  /**
   * D02: The tooltip on the Azure Spend element should mention "unavailable"
   *      or "check Cost Management" so operators know what to do.
   *
   * RED: Current tooltip only shows the cost breakdown numbers, not a status message.
   */
  it('D02: tooltip contains "unavailable" text when foundry_cost_status is "unavailable"', () => {
    renderCard(unavailableAgent as AgentSummary);

    // Find the Azure Spend span — it should have a title attribute with status info
    const costEl = screen.getByText(/Azure Spend/i);
    const container = costEl.closest('[title]') ?? costEl.parentElement?.closest('[title]');
    const title = container?.getAttribute('title') ?? '';
    expect(title.toLowerCase()).toMatch(/unavailable|check cost management/);
  });

  /**
   * D03: When cost_status is "unavailable", the warning triangle (⚠) should NOT
   *      be shown — Cost Management being down is not a ⚠ situation, it's expected.
   *
   * RED: Current logic shows ⚠ whenever foundry=0 && sdk>0, ignoring cost_status.
   *      This causes false alarms every time CM is down.
   */
  it('D03: does NOT render warning icon when foundry_cost_status is "unavailable"', () => {
    renderCard(unavailableAgent as AgentSummary);

    // Warning icon must not appear when the cost source is just unavailable
    expect(screen.queryByTestId('foundry-warning')).toBeNull();
  });

  /**
   * D04: When cost_status is "stale", a clock/stale indicator should be visible.
   *      SC-7: The 24–48h Azure Cost Management lag must be surfaced, not silenced.
   *
   * RED: No stale indicator exists in the component today.
   */
  it('D04: renders a stale indicator (clock icon or "stale" text) when foundry_cost_status is "stale"', () => {
    renderCard(staleAgent as AgentSummary);

    // Look for a testid, clock emoji, or the word "stale" in the cost area
    const staleEl =
      screen.queryByTestId('foundry-stale') ??
      screen.queryByText(/stale|as of/i) ??
      screen.queryByText('🕐');
    expect(staleEl).not.toBeNull();
  });

  /**
   * D05: When cost_status is "no_usage", $0.00 should be displayed — this is a
   *      legitimate zero (Azure confirmed no spend), not an error state.
   *
   * Note: This test exercises both foundry=0 AND sdk=0 so no warning fires.
   * The component should render $0.00 normally.
   * This test may PASS with current code; retained as a regression guard.
   */
  it('D05: renders $0.00 (not "—") when foundry_cost_status is "no_usage"', () => {
    renderCard(noUsageAgent as AgentSummary);

    // $0.00 should be displayed as the primary Foundry Spend value
    expect(screen.getByText(/\$0\.00/)).toBeInTheDocument();
    // Must NOT show "—" for the Azure Spend value
    const azureSpanText = screen.getByText(/Azure Spend/i).closest('span')?.textContent ?? '';
    expect(azureSpanText).not.toMatch(/^—$/);
  });

  /**
   * D06: Warning icon only fires when cost_status is "ok" AND foundry==0 AND sdk>0.
   *      This is the case where Azure IS reachable and returns $0, but SDK shows spend —
   *      suggesting a real misconfiguration in the agent → RG mapping.
   *
   * RED: Current logic shows warning whenever foundry=0 && sdk>0, regardless of cost_status.
   *      After Phase 8, the condition must also require cost_status === "ok".
   */
  it('D06: warning icon fires ONLY when cost_status=="ok" AND foundry==0 AND sdk>0', () => {
    // Case A: status="ok", foundry=0, sdk>0 → warning SHOULD show
    const { unmount } = renderCard(zeroFoundryOkStatusAgent as AgentSummary);
    expect(screen.getByTestId('foundry-warning')).toBeInTheDocument();
    unmount();

    // Case B: status="unavailable", foundry=0, sdk>0 → warning must NOT show
    renderCard(unavailableAgent as AgentSummary);
    expect(screen.queryByTestId('foundry-warning')).toBeNull();
  });
});
