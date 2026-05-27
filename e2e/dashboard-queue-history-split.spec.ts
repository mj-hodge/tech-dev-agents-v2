/**
 * STORY-858 — Queue / History panel split.
 *
 * Asserts:
 *  - A story with status=failed (in attention_queue lane) does NOT appear
 *    in the Queue panel.
 *  - That same story DOES appear in the History panel.
 *  - The attention callout ("N in attention — view history") is visible
 *    in the Queue panel header when attention_queue count > 0.
 *
 * @smoke
 */

import { test, expect, type Page } from '@playwright/test';

const FAILED_STORY_ID = 'STORY-858-FAILED';

const mkFailedItem = () => ({
  story_id: FAILED_STORY_ID,
  title: 'Failure cluster test item',
  status: 'failed',
  repo: 'tech-dev-agents',
  scope: 'small',
  prompt: '',
  enqueued_by: 'test',
  enqueued_at: new Date().toISOString(),
  claimed_by: 'dan',
  claimed_at: new Date(Date.now() - 3_600_000).toISOString(),
  completed_at: null,
  cancelled_at: null,
  review_started_at: null,
  needs_info_path: null,
  current_phase: 8,
  phase_started_at: null,
  priority: 0,
  target_role: 'developer',
});

/** Mocks all API routes the dashboard polls on load. */
async function installMocks(page: Page) {
  // v2 queue — one failed story in the attention lane, nothing actionable
  await page.route('**/api/dispatch/v2/queue*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pending: [],
        in_progress: [],
        in_review: [],
        paused: [],
        needs_info: [],
        attention: [mkFailedItem()],  // the v2 server uses `attention` key
      }),
    });
  });

  // history endpoint — empty (so the failed story only shows from queue attention lane)
  await page.route('**/api/dispatch/history*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [],
        total: 0,
        limit: 20,
        offset: 0,
        fetched_at: new Date().toISOString(),
      }),
    });
  });

  // Stub other endpoints so the page loads cleanly
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
  await page.route('**/api/alerts*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ alerts: [], total: 0, active_count: 0, fetched_at: new Date().toISOString() }),
    });
  });
  await page.route('**/api/dispatch/metrics*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        claim_409_per_story_5m_max: 0,
        head_of_line_age_seconds: null,
        failure_reason_null_rate: 0,
        claim_conflict_rate_5m: 0,
        fetched_at: new Date().toISOString(),
      }),
    });
  });
}

test.describe('STORY-858: queue/history split', () => {
  test('failed story is NOT shown in the Queue panel', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.waitForSelector('[data-testid="queue-panel"]', { timeout: 10_000 });

    const queuePanel = page.locator('[data-testid="queue-panel"]');
    await expect(queuePanel).toBeVisible();

    // The failed story must not appear inside the queue panel
    await expect(queuePanel.getByText(FAILED_STORY_ID)).toHaveCount(0);
  });

  test('failed story IS shown in the History panel', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.waitForSelector('[data-testid="history-panel"]', { timeout: 10_000 });

    const historyPanel = page.locator('[data-testid="history-panel"]');
    await expect(historyPanel).toBeVisible();
    await expect(historyPanel.getByText(FAILED_STORY_ID)).toBeVisible();
  });

  test('attention callout appears in Queue header when failed items exist', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.waitForSelector('[data-testid="attention-callout"]', { timeout: 10_000 });

    const callout = page.getByTestId('attention-callout');
    await expect(callout).toBeVisible();
    await expect(callout).toContainText('1');
    await expect(callout).toContainText('attention');
  });

  test('Queue panel shows "No stories" message when only failed items exist', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10_000 });

    // The queue panel body should show the empty-queue message
    await expect(page.getByText('No stories in dispatch queue')).toBeVisible();
  });
});
