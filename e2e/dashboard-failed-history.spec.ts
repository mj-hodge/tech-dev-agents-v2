/**
 * STORY-861 Phase 7 RED tests — Playwright E2E
 *
 * AC-10: failed story does NOT appear in queue rows; DOES appear in History tab.
 *
 * These tests intercept /api/dispatch/queue and inject an `attention` bucket
 * containing a failed story. They assert:
 *   F-1: failed story absent from queue tab rows.
 *   F-2: switching to History tab reveals the failed story.
 *   F-3: attention callout badge appears when attention.length > 0.
 *
 * RED state: all three tests FAIL until Phase 8 delivers the component changes.
 *
 * @smoke
 */

import { test, expect, type Page } from '@playwright/test';

const FAILED_ITEM = {
  story_id: 'STORY-861-E2E',
  title: 'E2E failed story',
  status: 'failed',
  repo: 'tech-dev-agents',
  scope: 'small',
  prompt: '',
  enqueued_by: 'mark',
  enqueued_at: '2026-05-04T00:00:00Z',
  claimed_by: 'devon',
  claimed_at: '2026-05-04T00:01:00Z',
  completed_at: null,
  cancelled_at: null,
  failed_at: '2026-05-04T00:05:00Z',
  commit_sha: null,
  pr_number: null,
  review_started_at: null,
  paused_at: null,
  current_phase: '7',
  phase_started_at: null,
  needs_info_path: null,
  priority: 0,
  target_role: 'developer',
};

const MOCK_QUEUE_WITH_FAILED = {
  pending: [],
  in_progress: [],
  claimed: [],
  in_review: [],
  paused: [],
  needs_info: [],
  attention: [FAILED_ITEM],
  completed: [],
  failed: [],
  total_pending: 0,
  total_claimed: 0,
  fetched_at: new Date().toISOString(),
};

const MOCK_QUEUE_EMPTY = {
  ...MOCK_QUEUE_WITH_FAILED,
  attention: [],
};

async function installMocks(page: Page, queuePayload: object) {
  await page.route('**/api/dispatch/queue*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(queuePayload),
    });
  });
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
  await page.route('**/api/dispatch/history*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0, fetched_at: new Date().toISOString() }),
    });
  });
}

test.describe('STORY-861: failed stories — queue vs history routing', () => {
  // F-1: failed story must NOT appear in queue tab rows
  test('AC-10 F-1: failed story is absent from queue tab', async ({ page }) => {
    await installMocks(page, MOCK_QUEUE_WITH_FAILED);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });

    // Queue tab is active by default — verify no row for the failed story.
    // RED: before STORY-861 fix, attention items bleed into the queue tab via paused bucket.
    const storyCell = page.locator('tbody tr td', { hasText: 'STORY-861-E2E' });
    await expect(storyCell).toHaveCount(0);
  });

  // F-2: switching to history tab shows the failed story
  test('AC-10 F-2: failed story appears in history tab after switch', async ({ page }) => {
    await installMocks(page, MOCK_QUEUE_WITH_FAILED);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });

    // Switch to History tab
    await page.getByRole('button', { name: /history/i }).click();

    // RED: History tab does not yet render attention items from queue response.
    await expect(page.locator('text=STORY-861-E2E')).toBeVisible({ timeout: 5000 });
  });

  // F-3: attention callout badge visible when attention.length > 0
  test('AC-10 F-3: attention callout badge appears when attention stories exist', async ({ page }) => {
    await installMocks(page, MOCK_QUEUE_WITH_FAILED);
    await page.goto('/');
    await page.waitForSelector('text=Dispatch Queue', { timeout: 10000 });

    // RED: data-testid="attention-callout" doesn't exist in current component.
    const callout = page.getByTestId('attention-callout');
    await expect(callout).toBeVisible({ timeout: 5000 });
    await expect(callout).toContainText('1');
  });
});
