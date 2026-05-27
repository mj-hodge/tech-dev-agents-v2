/**
 * Tests for PresencePanel component — T426-23 through T426-30
 *
 * RED state: fails until Phase 8 creates:
 *   - frontend/src/hooks/usePresence.ts
 *   - frontend/src/components/PresencePanel.tsx
 *   - frontend/src/types/api.ts (PresenceState, AgentPresenceItem, PresenceResponse)
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Imports from not-yet-created files — RED state for the module system.
import { PresencePanel } from "../components/PresencePanel";
import { usePresence } from "../hooks/usePresence";
import type { PresenceResponse } from "../types/api";

// ---------------------------------------------------------------------------
// Mock the hook so component tests are isolated from the network layer
// ---------------------------------------------------------------------------

vi.mock("../hooks/usePresence");

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PresencePanel />
    </QueryClientProvider>
  );
}

const now = new Date().toISOString();

const mockPresence: PresenceResponse = {
  agents: [
    { name: "dan",     state: "working",      checked_at: now, detail: null },
    { name: "derrick", state: "idle",          checked_at: now, detail: null },
    { name: "ada",     state: "rate_limited",  checked_at: now, detail: null },
    { name: "turing",  state: "offline",       checked_at: now, detail: "SSH timed out" },
  ],
  cached: false,
  checked_at: now,
};

// ---------------------------------------------------------------------------

describe("PresencePanel — T426-23–30", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --- T426-23: Renders agent names ---

  it("T426-23: renders a bubble for each agent in the response", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockPresence,
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText("dan")).toBeInTheDocument();
    expect(screen.getByText("derrick")).toBeInTheDocument();
    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(screen.getByText("turing")).toBeInTheDocument();
  });

  // --- T426-24: Status labels ---

  it("T426-24: displays the correct label text for each state", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockPresence,
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/working/i)).toBeInTheDocument();
    expect(screen.getByText(/idle/i)).toBeInTheDocument();
    expect(screen.getByText(/rate.?limited/i)).toBeInTheDocument();
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
  });

  // --- T426-25: Color coding ---

  it("T426-25: working state bubble has green styling", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...mockPresence,
        agents: [{ name: "dan", state: "working", checked_at: now, detail: null }],
      },
      isLoading: false,
      isError: false,
    });

    const { container } = renderPanel();

    // Expect a green Tailwind class on or near the status dot
    const greenEls = container.querySelectorAll('[class*="green"]');
    expect(greenEls.length).toBeGreaterThan(0);
  });

  it("T426-25b: offline state bubble has red styling", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...mockPresence,
        agents: [{ name: "turing", state: "offline", checked_at: now, detail: "SSH timeout" }],
      },
      isLoading: false,
      isError: false,
    });

    const { container } = renderPanel();

    const redEls = container.querySelectorAll('[class*="red"]');
    expect(redEls.length).toBeGreaterThan(0);
  });

  it("T426-25c: rate_limited state bubble has yellow styling", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...mockPresence,
        agents: [{ name: "ada", state: "rate_limited", checked_at: now, detail: null }],
      },
      isLoading: false,
      isError: false,
    });

    const { container } = renderPanel();

    const yellowEls = container.querySelectorAll('[class*="yellow"]');
    expect(yellowEls.length).toBeGreaterThan(0);
  });

  it("T426-25d: idle state bubble has gray styling", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...mockPresence,
        agents: [{ name: "derrick", state: "idle", checked_at: now, detail: null }],
      },
      isLoading: false,
      isError: false,
    });

    const { container } = renderPanel();

    const grayEls = container.querySelectorAll('[class*="gray"]');
    expect(grayEls.length).toBeGreaterThan(0);
  });

  // --- T426-26: Loading skeleton ---

  it("T426-26: shows loading skeleton when isLoading=true", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });

    const { container } = renderPanel();

    const skeletons = container.querySelectorAll(
      '[class*="animate-pulse"], [data-testid*="skeleton"]'
    );
    expect(skeletons.length).toBeGreaterThan(0);
  });

  // --- T426-27: Error state ---

  it("T426-27: shows error message when isError=true", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });

    renderPanel();

    expect(screen.getByText(/failed to load presence/i)).toBeInTheDocument();
  });

  // --- T426-28: 'cached' badge ---

  it("T426-28: shows 'cached' indicator when response.cached=true", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockPresence, cached: true },
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/cached/i)).toBeInTheDocument();
  });

  it("T426-28b: does not show cached indicator when response.cached=false", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { ...mockPresence, cached: false },
      isLoading: false,
      isError: false,
    });

    renderPanel();

    // 'cached' text should not appear when cached=false
    expect(screen.queryByText(/^cached$/i)).not.toBeInTheDocument();
  });

  // --- T426-29: title tooltip for offline agent with detail ---

  it("T426-29: offline agent bubble has title attribute with detail text", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...mockPresence,
        agents: [{ name: "turing", state: "offline", checked_at: now, detail: "SSH timed out" }],
      },
      isLoading: false,
      isError: false,
    });

    const { container } = renderPanel();

    // The bubble wrapper should carry the detail as a title tooltip
    const withTitle = container.querySelector('[title="SSH timed out"]');
    expect(withTitle).not.toBeNull();
  });

  // --- T426-30: Section heading ---

  it("T426-30: renders an 'Agent Presence' section heading", () => {
    (usePresence as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockPresence,
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/agent presence/i)).toBeInTheDocument();
  });
});
