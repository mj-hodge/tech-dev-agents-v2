/**
 * STORY-541: Dashboard Agent Row Display Contract — Playwright Dashboard E2E
 *
 * Drives a real browser against the dashboard with mocked /api/agents data
 * covering all 6 status states. Asserts text-level rendering matches the
 * locked display contract from seed.md.
 *
 * @e2e-dashboard
 */

import { test, expect, type Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Fixture: one agent per status state, matching the locked contract exactly
// ---------------------------------------------------------------------------

const MOCK_AGENTS_RESPONSE = {
  agents: [
    {
      name: 'Dan',
      status: 'idle',
      role: 'developer',
      enabled: true,
      busy: false,
      current_story: null,
      current_phase: null,
      current_story_id: null,
      today_foundry_usd: 2.22,
      today_sdk_usd: 10.25,
      today_openai_usd: 0.0,
      today_total_usd: 12.47,
      today_cost_usd: 12.47,
      last_activity: '2026-04-23T10:00:00Z',
      uptime_seconds: 86400,
      active_sessions: 0,
      error_count: 0,
      checked_at: '2026-04-23T10:00:00Z',
      quota: {
        source: 'loki',
        remaining_tokens: 47_000,
        reset_in_minutes: 252,
        percent_used: null,
        block_start: null,
        block_end: null,
        p90_limit: null,
        sessions_in_block: null,
      },
    },
    {
      name: 'Derrick',
      status: 'idle',
      role: 'developer',
      enabled: true,
      busy: false,
      current_story: null,
      current_phase: null,
      current_story_id: null,
      today_foundry_usd: 1.50,
      today_sdk_usd: 5.00,
      today_openai_usd: 0.0,
      today_total_usd: 6.50,
      today_cost_usd: 6.50,
      last_activity: '2026-04-23T10:00:00Z',
      uptime_seconds: 43200,
      active_sessions: 0,
      error_count: 0,
      checked_at: '2026-04-23T10:00:00Z',
      quota: {
        source: 'loki',
        remaining_tokens: 92_000,
        reset_in_minutes: 252,
        percent_used: null,
        block_start: null,
        block_end: null,
        p90_limit: null,
        sessions_in_block: null,
      },
    },
    {
      name: 'Daisy',
      status: 'working',
      role: 'developer',
      enabled: true,
      busy: true,
      current_story: 'STORY-540',
      current_phase: 'Phase 8',
      current_story_id: 'STORY-540',
      today_foundry_usd: 3.00,
      today_sdk_usd: 12.00,
      today_openai_usd: 0.0,
      today_total_usd: 15.00,
      today_cost_usd: 15.00,
      last_activity: '2026-04-23T10:00:00Z',
      uptime_seconds: 86400,
      active_sessions: 1,
      error_count: 0,
      checked_at: '2026-04-23T10:00:00Z',
      quota: {
        source: 'loki',
        remaining_tokens: 38_000,
        reset_in_minutes: 252,
        percent_used: null,
        block_start: null,
        block_end: null,
        p90_limit: null,
        sessions_in_block: null,
      },
    },
    {
      name: 'Devon',
      status: 'paused',
      role: 'developer',
      enabled: true,
      busy: true,
      current_story: 'STORY-539',
      current_phase: null,
      current_story_id: 'STORY-539',
      rate_limited_until: '2026-04-23T19:00:00Z',
      today_foundry_usd: 0.0,
      today_sdk_usd: 0.0,
      today_openai_usd: 0.0,
      today_total_usd: 0.0,
      today_cost_usd: 0.0,
      last_activity: '2026-04-23T10:00:00Z',
      uptime_seconds: 86400,
      active_sessions: 0,
      error_count: 0,
      checked_at: '2026-04-23T10:00:00Z',
      quota: {
        source: 'no_data',
        remaining_tokens: null,
        reset_in_minutes: null,
        percent_used: null,
        block_start: null,
        block_end: null,
        p90_limit: null,
        sessions_in_block: null,
      },
    },
    {
      name: 'Morris',
      status: 'stopped',
      role: 'manager',
      enabled: true,
      busy: false,
      current_story: null,
      current_phase: null,
      current_story_id: null,
      today_foundry_usd: 0.0,
      today_sdk_usd: 0.0,
      today_openai_usd: 0.0,
      today_total_usd: 0.0,
      today_cost_usd: 0.0,
      last_activity: '2026-04-23T08:00:00Z',
      uptime_seconds: 0,
      active_sessions: 0,
      error_count: 0,
      checked_at: '2026-04-23T10:00:00Z',
      quota: null,
    },
    {
      name: 'Hermes',
      status: 'unreachable',
      role: 'developer',
      enabled: true,
      busy: false,
      current_story: null,
      current_phase: null,
      current_story_id: null,
      today_foundry_usd: 0.0,
      today_sdk_usd: 0.0,
      today_openai_usd: 0.0,
      today_total_usd: 0.0,
      today_cost_usd: 0.0,
      last_activity: null,
      uptime_seconds: 0,
      active_sessions: 0,
      error_count: 0,
      checked_at: '2026-04-23T10:00:00Z',
      quota: null,
    },
  ],
  total: 6,
  fetched_at: '2026-04-23T10:00:00Z',
};

// ---------------------------------------------------------------------------
// Setup: mock the /api/agents endpoint before each test
// ---------------------------------------------------------------------------

async function setupMockApi(page: Page) {
  await page.route('**/api/agents', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_AGENTS_RESPONSE),
    })
  );
  // Also mock fleet and other endpoints to prevent 404s
  await page.route('**/api/fleet', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  );
}

// ---------------------------------------------------------------------------
// Tests — @e2e-dashboard tag (NOT @smoke; auth bypass required)
// ---------------------------------------------------------------------------
//
// STORY-558 originally removed @smoke from these tests because the
// AuthGuard in App.tsx redirects unauthenticated visitors to /login,
// which means `text=Dan` never appears and every test in the describe
// block times out at 30s. STORY-565 re-added @smoke claiming "no MSAL
// auth is needed" — that was incorrect; the AuthGuard still ran. Result:
// every PR's `Playwright e2e smoke tests` job has been red on main since
// 2026-04-25, blocking the entire fleet's CI.
//
// The proper fix (audit setupMockApi for missing endpoints AND add a
// CI-mode auth bypass) is tracked in STORY-577. Until that lands, these
// tests run only under @e2e-dashboard, not @smoke.
test.describe('Dashboard Agent Row Display Contract @e2e-dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApi(page);
    await page.goto('/');
    // Wait for the agent cards to render
    await page.waitForSelector('text=Dan');
  });

  test('displays idle agent row with quota line', async ({ page }) => {
    // Derrick is idle with 92K tokens
    await expect(page.getByText('●idle')).toBeVisible();
    await expect(page.getByText('⏱ 92K tokens left')).toBeVisible();
    await expect(page.getByText(/resets 4h 12m/)).toBeVisible();
  });

  test('displays working agent row with claim line', async ({ page }) => {
    await expect(page.getByText('●working')).toBeVisible();
    await expect(page.getByText('📋 STORY-540 Phase 8')).toBeVisible();
    await expect(page.getByText('⏱ 38K tokens left')).toBeVisible();
  });

  test('displays paused agent row with paused-until', async ({ page }) => {
    await expect(page.getByText('●paused')).toBeVisible();
    await expect(
      page.getByText('📋 STORY-539 (paused until 19:00 UTC)')
    ).toBeVisible();
    await expect(page.getByText('⏱ —')).toBeVisible();
  });

  test('displays stopped agent row', async ({ page }) => {
    await expect(page.getByText('●stopped')).toBeVisible();
  });

  test('displays unreachable agent row', async ({ page }) => {
    await expect(page.getByText('●unreachable')).toBeVisible();
  });

  test('all six status states visible on dashboard', async ({ page }) => {
    // Verify all status dots are present on a single page
    await expect(page.getByText('●working')).toBeVisible();
    await expect(page.getByText('●paused')).toBeVisible();
    await expect(page.getByText('●stopped')).toBeVisible();
    await expect(page.getByText('●unreachable')).toBeVisible();
    // Two idle agents (Dan and Derrick)
    const idleDots = page.getByText('●idle');
    await expect(idleDots.first()).toBeVisible();
  });

  test('full-page screenshot matches baseline', async ({ page }) => {
    await expect(page).toHaveScreenshot('dashboard-all-states.png', {
      fullPage: true,
    });
  });

  test('per-row screenshots for each state', async ({ page }) => {
    // Locate each agent row and take a scoped screenshot
    const rows = [
      { name: 'Derrick', file: 'row-idle.png' },
      { name: 'Daisy', file: 'row-working.png' },
      { name: 'Devon', file: 'row-paused.png' },
      { name: 'Morris', file: 'row-stopped.png' },
      { name: 'Hermes', file: 'row-unreachable.png' },
    ];

    for (const { name, file } of rows) {
      const row = page.locator(`[data-agent-name="${name}"]`).or(
        page.getByText(name).locator('..')
      );
      await expect(row.first()).toHaveScreenshot(file);
    }
  });
});
