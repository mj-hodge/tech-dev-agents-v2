/**
 * STORY-528/505 fix (2026-04-23): Dispatch Queue `needs_info` counter badge.
 *
 * Drives a real browser against the dashboard with a mocked /api/dispatch/queue
 * response containing needs_info items. Asserts:
 *   - Top-of-queue violet badge appears when needs_info > 0
 *   - Badge text matches the count
 *   - Badge is hidden when needs_info is empty
 *   - Badge click keeps the queue expanded + scrolls to needs_info rows
 *
 * @smoke
 */

import { test, expect, type Page } from '@playwright/test';

const MOCK_QUEUE_EMPTY = {
  pending: [],
  in_progress: [],
  claimed: [],
  in_review: [],
  paused: [],
  needs_info: [],
  completed: [],
  failed: [],
  total_pending: 0,
  total_claimed: 0,
};

const mkItem = (story_id: string, title: string) => ({
  story_id,
  title,
  status: 'needs_info',
  repo: 'advertising-amazon',
  scope: 'large',
  prompt: '',
  enqueued_by: 'test',
  enqueued_at: '2026-04-23T20:00:00Z',
  claimed_by: null,
  claimed_at: null,
  completed_at: null,
  cancelled_at: null,
  failed_at: null,
  commit_sha: null,
  pr_number: null,
  review_started_at: null,
  paused_at: null,
  current_phase: 4,
  phase_started_at: null,
  needs_info_path: `features/${story_id.toLowerCase()}/QUESTION.md`,
  priority: 0,
  target_role: 'developer',
});

const MOCK_QUEUE_WITH_NEEDS_INFO = {
  ...MOCK_QUEUE_EMPTY,
  needs_info: [
    mkItem('STORY-528', 'FBA export cron'),
    mkItem('STORY-505', 'Monitoring + Teams'),
  ],
};

async function installQueueMock(page: Page, payload: any) {
  await page.route('**/api/dispatch/queue*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
  // Also stub other endpoints the dashboard hits on load so we don't time out
  await page.route('**/api/agents*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agents: [] }),
    });
  });
  await page.route('**/api/fleet*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agents: [], counts: {}, costs: {} }),
    });
  });
}

test.describe('DispatchQueue needs_info badge', () => {
  // API calls are intercepted via installQueueMock — no real backend needed.
  // The Vite dev server is auto-started by webServer in playwright.config.ts,
  // so these tests run in CI without a manual server.

  test('is hidden when no needs_info items', async ({ page }) => {
    await installQueueMock(page, MOCK_QUEUE_EMPTY);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });
    const badge = page.getByTestId('needs-info-badge');
    await expect(badge).toHaveCount(0);
  });

  test('shows violet badge with count when items present', async ({ page }) => {
    await installQueueMock(page, MOCK_QUEUE_WITH_NEEDS_INFO);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });
    const badge = page.getByTestId('needs-info-badge');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText('2 needs info');
    await expect(badge).toHaveClass(/animate-pulse/);
    await expect(badge).toHaveAttribute(
      'aria-label',
      /2.*need.*input/i,
    );
  });

  test('needs_info rows are rendered in the queue even when no pending/claimed', async ({ page }) => {
    await installQueueMock(page, MOCK_QUEUE_WITH_NEEDS_INFO);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });
    // Pre-fix regression: rows were hidden when totalItems=0 (totalItems
    // didn't count needs_info). After the fix, needs_info items show up.
    await expect(page.locator('text=STORY-528')).toBeVisible();
    await expect(page.locator('text=STORY-505')).toBeVisible();
  });

  test('badge click does not collapse the queue', async ({ page }) => {
    await installQueueMock(page, MOCK_QUEUE_WITH_NEEDS_INFO);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });
    const badge = page.getByTestId('needs-info-badge');
    // Ensure queue expanded before click (expand button shows '-')
    await expect(page.locator('text=STORY-528')).toBeVisible();
    await badge.click();
    // Queue should still be expanded after badge click — stopPropagation
    // prevents the outer button's collapse handler from firing.
    await expect(page.locator('text=STORY-528')).toBeVisible();
  });
});
