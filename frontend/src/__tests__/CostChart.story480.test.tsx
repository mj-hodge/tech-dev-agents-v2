/**
 * Tests for CostChart — STORY-480 Dashboard Overhaul
 * AC-7: Stacked AreaChart with 3 series (Foundry, SDK, OpenAI)
 *
 * RED state: fails until Phase 8:
 *   - Updates CostChart to accept multi-series data format
 *   - Renders 3 distinct Area series (Foundry/SDK/OpenAI)
 *   - Renders legend entries for each series
 *   - Applies appropriate colors per series
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { CostChart } from '../components/CostChart';
import type { CostHistoryEntry } from '../types/api';

// ---------------------------------------------------------------------------

// STORY-480: Multi-series data format — CostHistoryEntry currently has { date, cost }
// These new fields will cause TypeScript errors until types/api.ts is updated
interface MultiSeriesCostEntry {
  date: string;
  foundry_cost_usd: number;
  sdk_cost_usd: number;
  openai_cost_usd: number;
}

const multiSeriesData: MultiSeriesCostEntry[] = [
  { date: '2026-04-18', foundry_cost_usd: 3.5, sdk_cost_usd: 8.2, openai_cost_usd: 0.3 },
  { date: '2026-04-19', foundry_cost_usd: 2.1, sdk_cost_usd: 10.5, openai_cost_usd: 0.1 },
  { date: '2026-04-20', foundry_cost_usd: 4.0, sdk_cost_usd: 9.0, openai_cost_usd: 0.5 },
];

// Old single-series format for backward-compat test
const legacyData: CostHistoryEntry[] = [
  { date: '2026-04-18', cost: 12.0 },
  { date: '2026-04-19', cost: 13.1 },
  { date: '2026-04-20', cost: 13.5 },
];

// ---------------------------------------------------------------------------

describe('CostChart — STORY-480 AC-7 (stacked multi-series AreaChart)', () => {
  // T480-51: renders legend entry for "Foundry"
  it('T480-51: renders a legend entry for "Foundry"', () => {
    render(<CostChart data={multiSeriesData as any} />);

    // Fails until CostChart renders a Foundry series with a legend entry
    expect(screen.getByText(/foundry/i)).toBeInTheDocument();
  });

  // T480-52: renders legend entry for "SDK"
  it('T480-52: renders a legend entry for "SDK"', () => {
    render(<CostChart data={multiSeriesData as any} />);

    // Fails until CostChart renders an SDK series with a legend entry
    expect(screen.getByText(/\bsdk\b/i)).toBeInTheDocument();
  });

  // T480-53: renders legend entry for "OpenAI"
  it('T480-53: renders a legend entry for "OpenAI"', () => {
    render(<CostChart data={multiSeriesData as any} />);

    // Fails until CostChart renders an OpenAI series with a legend entry
    expect(screen.getByText(/openai/i)).toBeInTheDocument();
  });

  // T480-54: renders 3 distinct data series areas
  it('T480-54: renders at least 3 distinct recharts Area elements', () => {
    const { container } = render(<CostChart data={multiSeriesData as any} />);

    // Fails until CostChart uses AreaChart with 3 <Area> series
    const areas = container.querySelectorAll('.recharts-area-area, .recharts-area');
    expect(areas.length).toBeGreaterThanOrEqual(3);
  });

  // T480-55: Foundry series uses blue color
  it('T480-55: Foundry series uses a blue color (stroke or fill)', () => {
    const { container } = render(<CostChart data={multiSeriesData as any} />);

    // Fails until CostChart assigns blue color to Foundry area
    // Check for blue in inline styles or SVG fill/stroke attributes
    const svgElements = container.querySelectorAll(
      'path[stroke*="#3b82f6"], path[fill*="#3b82f6"], ' +
      'path[stroke*="#2563eb"], path[fill*="#2563eb"], ' +
      'path[stroke*="#1d4ed8"], path[fill*="#1d4ed8"], ' +
      '[class*="blue"]'
    );
    expect(svgElements.length).toBeGreaterThan(0);
  });

  // T480-56: backward-compat: renders without error when given old { date, cost }[] format
  it('T480-56: renders without error when given legacy single-series { date, cost }[] data', () => {
    // This may pass already if CostChart handles unknown formats gracefully
    expect(() => render(<CostChart data={legacyData} />)).not.toThrow();
  });
});
