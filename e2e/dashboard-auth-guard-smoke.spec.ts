import { test, expect } from '@playwright/test';

// Smoke contract: unauthenticated dashboard access should be guarded.
// This preserves dashboard-path coverage in @smoke without requiring
// mocked auth bypass or full dashboard data fixtures.
test('@smoke dashboard route enforces auth guard', async ({ page }) => {
  await page.goto('/dashboard');

  // Current behavior in CI is a redirect to /login. Keep this check tolerant
  // of auth-provider URL details while proving route protection is active.
  await page.waitForURL(/\/login(?:[/?#]|$)/, { timeout: 15_000 });
  await expect(page).toHaveURL(/\/login(?:[/?#]|$)/);
});
