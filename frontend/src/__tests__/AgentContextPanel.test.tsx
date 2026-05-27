import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AgentContextPanel } from '../components/AgentContextPanel';
import type { AgentContext } from '../types/api';

// ---------------------------------------------------------------------------
// AgentContextPanel receives the context object directly as a prop so it
// does not need Router or Query wrappers.
// ---------------------------------------------------------------------------

function renderPanel(context: AgentContext) {
  return render(<AgentContextPanel context={context} />);
}

// ---------------------------------------------------------------------------

describe('AgentContextPanel — AC-6', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --- Teams deep link -------------------------------------------------------

  it('renders a Teams deep link that opens in a new tab', () => {
    renderPanel({
      teams_link: 'https://teams.microsoft.com/l/channel/example',
      blocker_status: null,
    });

    const link = screen.getByRole('link', { name: /teams/i });
    expect(link).toHaveAttribute('href', 'https://teams.microsoft.com/l/channel/example');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('does not render a Teams link when teams_link is null', () => {
    renderPanel({
      teams_link: null,
      blocker_status: null,
    });

    expect(screen.queryByRole('link', { name: /teams/i })).toBeNull();
  });

  // --- Blocker badge — "Blocked:" -------------------------------------------

  it('renders a red badge when blocker_status starts with "Blocked:"', () => {
    renderPanel({
      teams_link: null,
      blocker_status: 'Blocked: waiting for product decision on API shape',
    });

    const badge =
      screen.getByText(/blocked/i).closest('[class]') ??
      screen.getByText(/blocked/i).parentElement;
    expect(badge?.className).toMatch(/red/);
  });

  it('renders the full blocker message text for "Blocked:" status', () => {
    renderPanel({
      teams_link: null,
      blocker_status: 'Blocked: waiting for product decision on API shape',
    });

    expect(
      screen.getByText(/waiting for product decision on API shape/i)
    ).toBeInTheDocument();
  });

  // --- Blocker badge — "Decision needed:" ------------------------------------

  it('renders a yellow badge when blocker_status starts with "Decision needed:"', () => {
    renderPanel({
      teams_link: null,
      blocker_status: 'Decision needed: choose between option A and option B',
    });

    const badge =
      screen.getByText(/decision needed/i).closest('[class]') ??
      screen.getByText(/decision needed/i).parentElement;
    expect(badge?.className).toMatch(/yellow|amber/);
  });

  it('renders the full message text for "Decision needed:" status', () => {
    renderPanel({
      teams_link: null,
      blocker_status: 'Decision needed: choose between option A and option B',
    });

    expect(
      screen.getByText(/choose between option A and option B/i)
    ).toBeInTheDocument();
  });

  // --- No badge -------------------------------------------------------------

  it('renders no blocker badge when blocker_status is null', () => {
    renderPanel({
      teams_link: null,
      blocker_status: null,
    });

    expect(screen.queryByText(/blocked|decision needed/i)).toBeNull();
  });

  it('renders no blocker badge when blocker_status is an arbitrary non-prefixed message', () => {
    renderPanel({
      teams_link: null,
      blocker_status: 'Currently running Phase 8 tasks',
    });

    // Should NOT show a red or yellow badge — the text may or may not appear
    const redBadge = document.querySelector('[class*="red"]');
    const yellowBadge = document.querySelector('[class*="yellow"]');
    expect(redBadge).toBeNull();
    expect(yellowBadge).toBeNull();
  });

  // --- Combined ---------------------------------------------------------------

  it('renders both Teams link and red badge together when both are set', () => {
    renderPanel({
      teams_link: 'https://teams.microsoft.com/l/channel/example',
      blocker_status: 'Blocked: needs review',
    });

    expect(screen.getByRole('link', { name: /teams/i })).toBeInTheDocument();
    const badge =
      screen.getByText(/blocked/i).closest('[class]') ??
      screen.getByText(/blocked/i).parentElement;
    expect(badge?.className).toMatch(/red/);
  });
});
