/**
 * STORY-738 — Needs Info Response UI: Answer Modal E2E tests.
 *
 * Phase 7 — RED state.
 *
 * Drives a real browser against the dashboard with mocked dispatch API
 * endpoints. Tests the NeedsInfoAnswerModal flow:
 *   E01: Answer button visible on needs_info rows (SC-1)
 *   E02: Clicking Answer opens modal and fetches question (SC-2)
 *   E03: Submit sends POST /answer and closes modal (SC-3, SC-5)
 *   E04: Queue refreshes after submit (SC-6)
 *   E05: Degraded state shows fallback when question_text is null (SC-7)
 *   E06: Submit button disabled when answer is empty
 *   E07: Error banner on fetch failure with retry
 *   E08: 409 response shows "already answered" message
 *
 * RED reasons:
 *   - NeedsInfoAnswerModal component does not exist yet
 *   - GET /dispatch/{story_id}/question endpoint does not exist
 *   - POST /dispatch/{story_id}/answer endpoint does not exist
 *   - Answer button not yet rendered in DispatchQueue needs_info rows
 */

import { test, expect, type Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const STORY_ID = 'STORY-738';

const mkItem = (story_id: string, overrides?: Record<string, unknown>) => ({
  story_id,
  title: `${story_id} test story`,
  status: 'needs_info',
  repo: 'tech-dev-agents',
  scope: 'medium',
  prompt: '',
  enqueued_by: 'test',
  enqueued_at: '2026-04-30T12:00:00Z',
  claimed_by: 'daisy',
  claimed_at: '2026-04-30T11:00:00Z',
  completed_at: null,
  cancelled_at: null,
  failed_at: null,
  commit_sha: null,
  pr_number: null,
  review_started_at: null,
  paused_at: null,
  current_phase: 7,
  phase_started_at: null,
  needs_info_path: `features/${story_id.toLowerCase()}/QUESTION.md`,
  priority: 0,
  target_role: 'developer',
  question_text: null,
  answer_text: null,
  ...overrides,
});

const MOCK_QUEUE = {
  pending: [],
  in_progress: [],
  claimed: [],
  in_review: [],
  paused: [],
  needs_info: [mkItem(STORY_ID)],
  completed: [],
  failed: [],
  total_pending: 0,
  total_claimed: 0,
};

const MOCK_QUESTION_RESPONSE = {
  story_id: STORY_ID,
  repo: 'tech-dev-agents',
  agent: 'daisy',
  current_phase: 7,
  needs_info_path: 'features/story-738/QUESTION.md',
  question_text: 'Should the modal include markdown preview or plain text only?',
  has_question_text: true,
  fetched_at: '2026-04-30T12:00:00Z',
};

const MOCK_QUESTION_NO_TEXT = {
  ...MOCK_QUESTION_RESPONSE,
  question_text: null,
  has_question_text: false,
};

const MOCK_ANSWER_RESPONSE = {
  story_id: STORY_ID,
  status: 'pending',
  answered_at: '2026-04-30T12:01:00Z',
};

// ---------------------------------------------------------------------------
// Route installation helpers
// ---------------------------------------------------------------------------

async function installBaseMocks(page: Page) {
  // Mock the dispatch queue endpoint
  await page.route('**/api/dispatch/queue*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_QUEUE),
    });
  });

  // Stub other endpoints the dashboard hits on load
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
      body: JSON.stringify({
        agents: [],
        stories_in_progress: 0,
        daily_foundry_spend: 0,
        daily_sdk_spend: 0,
      }),
    });
  });
  await page.route('**/api/alerts*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ alerts: [], active_anomalies: [], count: 0, fetched_at: '2026-04-30T12:00:00Z' }),
    });
  });
  await page.route('**/api/agents/presence*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agents: [], cached: false, checked_at: '2026-04-30T12:00:00Z' }),
    });
  });
}

async function installQuestionMock(
  page: Page,
  response: Record<string, unknown> = MOCK_QUESTION_RESPONSE,
  status: number = 200,
) {
  await page.route(`**/api/dispatch/${STORY_ID}/question`, async (route) => {
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
}

async function installAnswerMock(
  page: Page,
  response: Record<string, unknown> = MOCK_ANSWER_RESPONSE,
  status: number = 200,
) {
  await page.route(`**/api/dispatch/${STORY_ID}/answer`, async (route) => {
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('STORY-738: NeedsInfoAnswerModal', () => {
  test('E01: Answer button is visible on needs_info rows (SC-1)', async ({ page }) => {
    await installBaseMocks(page);
    await page.goto('/');

    // The Answer button should be rendered for needs_info items
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await expect(answerButton).toBeVisible({ timeout: 10000 });
  });

  test('E02: Clicking Answer opens modal and shows question (SC-2)', async ({ page }) => {
    await installBaseMocks(page);
    await installQuestionMock(page);
    await page.goto('/');

    // Click the Answer button
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();

    // Modal should appear with question text
    const modal = page.getByRole('dialog');
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Question text should be displayed
    await expect(
      page.getByText('Should the modal include markdown preview')
    ).toBeVisible({ timeout: 5000 });

    // Header should show agent and story info (scoped to dialog to avoid
    // strict-mode violation — STORY_ID also appears in the queue table row)
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(STORY_ID)).toBeVisible();
  });

  test('E03: Submit sends POST /answer and closes modal (SC-3, SC-5)', async ({ page }) => {
    await installBaseMocks(page);
    await installQuestionMock(page);
    await installAnswerMock(page);
    await page.goto('/');

    // Open the modal
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();

    // Wait for modal to load
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });

    // Type an answer
    const textarea = page.getByRole('textbox');
    await textarea.fill('Plain text only for v1.');

    // Click submit
    const submitButton = page.getByRole('button', { name: /submit/i });
    await submitButton.click();

    // Modal should close after successful submit
    await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5000 });
  });

  test('E04: Queue refreshes after submit (SC-6)', async ({ page }) => {
    let queueFetchCount = 0;
    // Track queue refetches
    await page.route('**/api/dispatch/queue*', async (route) => {
      queueFetchCount++;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          // After answer, return queue without the needs_info item
          queueFetchCount > 1
            ? { ...MOCK_QUEUE, needs_info: [] }
            : MOCK_QUEUE
        ),
      });
    });

    // Stub other endpoints
    await page.route('**/api/agents*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"agents":[]}' });
    });
    await page.route('**/api/fleet*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agents: [], stories_in_progress: 0, daily_foundry_spend: 0, daily_sdk_spend: 0 }),
      });
    });
    await page.route('**/api/alerts*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"alerts":[],"active_anomalies":[],"count":0,"fetched_at":"2026-04-30T12:00:00Z"}' });
    });
    await page.route('**/api/agents/presence*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"agents":[],"cached":false,"checked_at":"2026-04-30T12:00:00Z"}' });
    });
    await installQuestionMock(page);
    await installAnswerMock(page);

    await page.goto('/');

    // Open modal, type answer, submit
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });

    const textarea = page.getByRole('textbox');
    await textarea.fill('Queue should refresh after this.');
    const submitButton = page.getByRole('button', { name: /submit/i });
    await submitButton.click();

    // After submit, queue should refetch — needs_info item should disappear
    // The mock returns an empty needs_info list on the second fetch
    await expect(page.getByText(STORY_ID)).not.toBeVisible({ timeout: 10000 });
  });

  test('E05: Degraded state shows fallback when question_text is null (SC-7)', async ({ page }) => {
    await installBaseMocks(page);
    await installQuestionMock(page, MOCK_QUESTION_NO_TEXT);
    await page.goto('/');

    // Open the modal
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();

    // Modal should show — but with fallback panel instead of question text
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });

    // Fallback panel should mention the file path or "not available"
    await expect(
      page.getByText(/question text not available|file path only/i)
    ).toBeVisible({ timeout: 5000 });

    // needs_info_path should be shown for manual SSH fallback
    await expect(
      page.getByText(/QUESTION\.md/i)
    ).toBeVisible();
  });

  test('E06: Submit button disabled when answer textarea is empty', async ({ page }) => {
    await installBaseMocks(page);
    await installQuestionMock(page);
    await page.goto('/');

    // Open the modal
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });

    // Submit button should be disabled when textarea is empty
    const submitButton = page.getByRole('button', { name: /submit/i });
    await expect(submitButton).toBeDisabled();

    // Type something — button should become enabled
    const textarea = page.getByRole('textbox');
    await textarea.fill('Test answer');
    await expect(submitButton).toBeEnabled();

    // Clear — button should be disabled again
    await textarea.fill('');
    await expect(submitButton).toBeDisabled();
  });

  test('E07: Error banner on fetch failure with retry', async ({ page }) => {
    await installBaseMocks(page);

    // Mock question endpoint to return 500
    await page.route(`**/api/dispatch/${STORY_ID}/question`, async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    await page.goto('/');

    // Open the modal
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });

    // Should show error state within the modal (scoped to dialog to avoid
    // strict-mode violation — alerts panel may also show error text)
    const dialog = page.getByRole('dialog');
    await expect(
      dialog.getByText(/error|failed|could not/i)
    ).toBeVisible({ timeout: 5000 });

    // Should have a retry button
    await expect(
      dialog.getByRole('button', { name: /retry/i })
    ).toBeVisible();
  });

  test('E08: 409 response shows "already answered" message', async ({ page }) => {
    await installBaseMocks(page);
    await installQuestionMock(page);

    // Mock answer endpoint to return 409
    await page.route(`**/api/dispatch/${STORY_ID}/answer`, async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: `Cannot answer ${STORY_ID} from status=pending — only needs_info stories.`,
        }),
      });
    });

    await page.goto('/');

    // Open modal, type answer, submit
    const answerButton = page.getByRole('button', { name: /answer/i }).first();
    await answerButton.click();
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });

    const textarea = page.getByRole('textbox');
    await textarea.fill('Duplicate answer attempt.');
    const submitButton = page.getByRole('button', { name: /submit/i });
    await submitButton.click();

    // Should show "already answered" message
    await expect(
      page.getByText(/already answered|already been answered/i)
    ).toBeVisible({ timeout: 5000 });
  });
});
