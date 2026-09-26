import { defineConfig, devices } from '@playwright/test';

/**
 * Authenticated staging E2E. Environment-gated (GitHub environment staging-e2e).
 * Missing configuration fails during config loading; this suite must never
 * report success by skipping authentication.
 */
const requiredEnvironment = ['E2E_BASE_URL', 'E2E_EMAIL', 'E2E_PASSWORD', 'E2E_WORKSPACE'];
const missingEnvironment = requiredEnvironment.filter((name) => !process.env[name]?.trim());
if (missingEnvironment.length) {
  throw new Error(`Authenticated staging E2E is missing: ${missingEnvironment.join(', ')}`);
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL,
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
      testMatch: /staging-critical-flow\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        storageState: './e2e/.auth/user.json',
      },
      dependencies: ['setup'],
    },
  ],
});
