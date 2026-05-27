import path from 'node:path';
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixels: 100,
    },
  },
  reporter: [['html', { outputFolder: 'playwright-report', open: 'never' }]],
  // Auto-start the Vite dev server for tests that load the dashboard.
  // Skipped when PLAYWRIGHT_BASE_URL is overridden (e.g. testing a remote
  // env). Without this, smoke tests using page.goto('/') fail in CI with
  // ERR_CONNECTION_REFUSED — the symptom that surfaced once the broken
  // playwright-smoke job was wired to the right config.
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : {
    // Run from frontend/ — webServer.cwd is resolved relative to the
    // config file (e2e/), so go up one level into frontend/.
    cwd: path.resolve(__dirname, '..', 'frontend'),
    command: 'npm run dev -- --host 127.0.0.1 --port 5173',
    url: 'http://localhost:5173',
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
