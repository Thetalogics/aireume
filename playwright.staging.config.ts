import { defineConfig, devices } from '@playwright/test';

/**
 * Authenticated staging E2E. Environment-gated (GitHub environment staging-e2e).
 * Not an automatic production promotion blocker.
 * Missing secrets must fail the workflow, not skip.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  grep: /Authenticated staging critical flow/,
  use: {
    baseURL: process.env.E2E_BASE_URL || process.env.PLAYWRIGHT_BASE_URL || 'https://airesume-staging.thetalogics.com',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'setup',
      testMatch: /.*\.setup\.ts/,
      retries: 0,
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: './e2e/.auth/user.json',
      },
      dependencies: ['setup'],
    },
  ],
});
