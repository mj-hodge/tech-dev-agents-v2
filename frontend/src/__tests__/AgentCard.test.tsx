import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { AgentCard } from '../components/AgentCard';
import { StatusBadge } from '../components/StatusBadge';
import type { AgentSummary } from '../types/api';

// ---------------------------------------------------------------------------

function renderCard(agent: AgentSummary) {
  return render(
    <MemoryRouter>
      <AgentCard agent={agent} />
    </MemoryRouter>
  );
}

function renderBadge(status: AgentSummary['status']) {
  return render(<StatusBadge status={status} />);
}

const baseAgent: AgentSummary = {
  name: 'agent-alpha',
  status: 'active',
  role: 'developer',
  enabled: true,
  current_story: 'STORY-019: Ops Console Dashboard Frontend',
  current_phase: 'Phase 8 — Implementation',
  // STORY-038: Full cost breakdown
  today_foundry_usd: 2.22,
  today_sdk_usd: 10.25,
  today_openai_usd: 0.0,
  today_total_usd: 12.47,
  today_cost_usd: 12.47,
  last_activity: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
  uptime_seconds: 3600,
  active_sessions: 1,
  error_count: 0,
  checked_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(), // 2 minutes ago
};

// ---------------------------------------------------------------------------

describe('AgentCard — AC-3', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the agent name prominently', () => {
    renderCard(baseAgent);
    expect(screen.getByText('agent-alpha')).toBeInTheDocument();
  });

  it('renders the current story text', () => {
    renderCard(baseAgent);
    expect(screen.getByText(/STORY-019/)).toBeInTheDocument();
  });

  it('truncates current_story longer than 40 characters', () => {
    const longStory = 'STORY-099: This is a very long story title that exceeds the limit';
    renderCard({ ...baseAgent, current_story: longStory });
    // Text in the DOM should be truncated (≤ 43 chars with ellipsis) OR use CSS truncation
    const storyEl = screen.getByText(/STORY-099/);
    const displayedText = storyEl.textContent ?? '';
    // Either CSS truncation (text-overflow) or JS truncation
    const hasCssTruncation = storyEl.className.includes('truncate') || storyEl.className.includes('overflow');
    const hasJsTruncation = displayedText.length <= 43; // 40 + "..."
    expect(hasCssTruncation || hasJsTruncation).toBe(true);
  });

  it('renders the current phase', () => {
    renderCard(baseAgent);
    expect(screen.getByText(/Phase 8/i)).toBeInTheDocument();
  });

  // STORY-038: Foundry cost is the primary displayed value
  it('renders today_foundry_usd as primary cost', () => {
    renderCard(baseAgent);
    expect(screen.getByText(/\$2\.22/)).toBeInTheDocument();
  });

  it('renders a relative last activity time (e.g. "2m ago")', () => {
    renderCard(baseAgent);
    // Accepts any relative time format: "2m ago", "2 minutes ago", "just now", etc.
    expect(screen.getByText(/ago|just now/i)).toBeInTheDocument();
  });

  it('renders a StatusBadge for the agent status', () => {
    renderCard(baseAgent);
    // STORY-541: StatusDot also renders status text, so use getAllByText
    // The word "active" should appear (in badge and/or status dot)
    const matches = screen.getAllByText(/active/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('renders a link that navigates to /agents/agent-alpha on click', () => {
    renderCard(baseAgent);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/agents/agent-alpha');
  });

  it('renders "–" or a placeholder when current_story is null', () => {
    renderCard({ ...baseAgent, current_story: null });
    // STORY-541: ClaimLine also renders "–" (en-dash \u2013), so use getAllByText
    const matches = screen.getAllByText(/\u2013/);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  // STORY-038: Tooltip shows full breakdown on hover
  it('tooltip shows full cost breakdown', () => {
    renderCard(baseAgent);
    const costEl = screen.getByText(/\$2\.22/);
    const title = costEl.getAttribute('title');
    expect(title).toContain('SDK');
    expect(title).toContain('Foundry');
    expect(title).toContain('OpenAI');
    expect(title).toContain('Total');
  });

  // STORY-038 + STORY-736: Warning when foundry=0 and sdk>0 AND cost_status='ok'
  // STORY-736 changed the condition: warning only fires when Cost Management is
  // reachable (status='ok') to stop crying wolf when CM is simply down.
  it('shows warning icon when foundry=0 and sdk>0', () => {
    const agentNoFoundry = {
      ...baseAgent,
      today_foundry_usd: 0,
      today_sdk_usd: 10.25,
      today_total_usd: 10.25,
      today_cost_usd: 10.25,
      foundry_cost_status: 'ok',
    };
    renderCard(agentNoFoundry);
    const warning = screen.getByTestId('foundry-warning');
    expect(warning).toBeInTheDocument();
  });

  it('does NOT show warning when foundry>0', () => {
    renderCard(baseAgent);
    expect(screen.queryByTestId('foundry-warning')).toBeNull();
  });
});

// ---------------------------------------------------------------------------

describe('StatusBadge — AC-3 (badge colors)', () => {
  it('uses green classes for "active" status', () => {
    const { container } = renderBadge('active');
    const dot = container.querySelector('[class*="green"]');
    expect(dot).not.toBeNull();
  });

  it('uses yellow classes for "idle" status', () => {
    const { container } = renderBadge('idle');
    const dot = container.querySelector('[class*="yellow"]');
    expect(dot).not.toBeNull();
  });

  it('uses red classes for "error" status', () => {
    const { container } = renderBadge('error');
    const dot = container.querySelector('[class*="red"]');
    expect(dot).not.toBeNull();
  });

  it('uses gray classes for "offline" status', () => {
    const { container } = renderBadge('offline');
    const dot = container.querySelector('[class*="gray"]');
    expect(dot).not.toBeNull();
  });

  it('renders the status label text', () => {
    renderBadge('active');
    expect(screen.getByText('active')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------

describe('AgentCard — STORY-049 (role badge)', () => {
  it('renders a role badge with the agent role text', () => {
    renderCard(baseAgent);
    expect(screen.getByText('developer')).toBeInTheDocument();
  });

  it('renders a purple badge for manager role', () => {
    const managerAgent = { ...baseAgent, name: 'morris', role: 'manager' };
    const { container } = renderCard(managerAgent);
    const badge = screen.getByText('manager');
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain('purple');
  });

  it('renders a green badge for developer role', () => {
    const { container } = renderCard(baseAgent);
    const badge = screen.getByText('developer');
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain('green');
  });
});
