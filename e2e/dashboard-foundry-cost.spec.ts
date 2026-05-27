/**
 * STORY-576: Foundry Cost Panel — Playwright Dashboard E2E
 *
 * E01-E02: Panel renders on dashboard, warning banner visible when today > $200.
 * RED until Phase 8 implements FoundryCostPanel.tsx and mounts it in DashboardLayout.
 *
 * @e2e-dashboard
 */

import { test, expect, type Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock API responses
// ---------------------------------------------------------------------------

const MOCK_FOUNDRY_COST_RESPONSE = {
  daily: [
    { date: '2026-04-18', opus_usd: 1486.23, sonnet_usd: 0, haiku_usd: 0, other_usd: 12.5, total_usd: 1498.73 },
    { date: '2026-04-19', opus_usd: 900.0, sonnet_usd: 45.2, haiku_usd: 8.1, other_usd: 5, total_usd: 958.3 },
    { date: '2026-04-20', opus_usd: 750.0, sonnet_usd: 80.0, haiku_usd: 12.0, other_usd: 3, total_usd: 845.0 },
    { date: '2026-04-21', opus_usd: 400.0, sonnet_usd: 100.0, haiku_usd: 15.0, other_usd: 2, total_usd: 517.0 },
    { date: '2026-04-22', opus_usd: 200.0, sonnet_usd: 120.0, haiku_usd: 20.0, other_usd: 1, total_usd: 341.0 },
    { date: '2026-04-23', opus_usd: 80.0, sonnet_usd: 90.0, haiku_usd: 18.0, other_usd: 5, total_usd: 193.0 },
    { date: '2026-04-24', opus_usd: 150.0, sonnet_usd: 60.0, haiku_usd: 10.0, other_usd: 2, total_usd: 222.0 },
  ],
  fetched_at: '2026-04-24T14:00:01Z',
  cache_age_seconds: 3601,
};

const MOCK_FLEET_RESPONSE = {
  total_daily_spend_usd: 222.0,
  total_monthly_spend_usd: 5575.03,
  active_agents: 3,
  busy_agents: 2,
  total_agents: 5,
  online_agents: 3,
  idle_agents: 1,
  stuck_agents: 0,
  offline_agents: 2,
  stories_in_progress: 4,
  fleet_health_score: 0.82,
  active_alerts: 0,
  fetched_at: '2026-04-24T14:00:01Z',
  agents: [],
};

// ---------------------------------------------------------------------------
// Setup: intercept API calls
// ---------------------------------------------------------------------------

async function setupMockApi(page: Page) {
  await page.route('**/api/fleet/foundry-cost*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_FOUNDRY_COST_RESPONSE),
    })
  );

  await page.route('**/api/fleet', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_FLEET_RESPONSE),
    })
  );

  await page.route('**/api/agents', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agents: [], total: 0, fetched_at: '2026-04-24T14:00:01Z' }),
    })
  );

  await page.route('**/api/alerts*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ alerts: [], total: 0, active_count: 0, fetched_at: '2026-04-24T14:00:01Z' }),
    })
  );

  await page.route('**/api/dispatch*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pending: [], in_progress: [], in_review: [], paused: [], needs_info: [],
        claimed: [], total_pending: 0, total_claimed: 0, fetched_at: '2026-04-24T14:00:01Z',
      }),
    })
  );
}

// ---------------------------------------------------------------------------
// Tests — @e2e-dashboard tag (NOT @smoke; auth bypass required)
// ---------------------------------------------------------------------------
//
// Same constraint as dashboard-agent-row.spec.ts: AuthGuard redirects to
// /login before the FoundryCostPanel can mount, so the @smoke tag caused
// these tests to fail on every PR. See STORY-577 for the proper fix
// (auth bypass + complete endpoint mocks). Until that lands, these tests
// run only under @e2e-dashboard.
test.describe('Foundry Cost Panel — Dashboard @e2e-dashboard', () => {
  test('E01: foundry cost panel renders on dashboard load', async ({ page }) => {
    await setupMockApi(page);
    await page.goto('/');

    // Wait for the panel to appear
    await expect(page.getByTestId('foundry-cost-panel')).toBeVisible();
    await expect(page.getByText(/Foundry Cost/i)).toBeVisible();
  });

  test('E02: warning banner shows when today exceeds $200', async ({ page }) => {
    await setupMockApi(page);
    await page.goto('/');

    // Today's total is $222 — warning should be visible
    await expect(page.getByTestId('foundry-cost-warning')).toBeVisible();
    await expect(page.getByText(/exceeds \$200/i)).toBeVisible();
  });
});
