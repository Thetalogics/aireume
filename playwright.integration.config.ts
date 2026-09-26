import { defineConfig, devices } from '@playwright/test';

const python = process.env.PYTHON_EXECUTABLE || 'python';

/**
 * Hermetic browser-to-database integration suite for pull requests.
 *
 * PostgreSQL and Redis are supplied by the CI job. Playwright starts the API
 * and frontend from the checked-out source, and the spec creates its own
 * disposable tenant. No provider credentials or shared test account are used.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: /ci-real-backend\.spec\.ts/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 120_000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium-real-backend',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.backend.main:app --host 127.0.0.1 --port 8000`,
      url: 'http://127.0.0.1:8000/ready',
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      cwd: './app/frontend',
      url: 'http://127.0.0.1:5173/login',
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
});
