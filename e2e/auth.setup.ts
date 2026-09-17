import { test as setup, expect } from '@playwright/test';
import path from 'path';

const authFile = path.join(__dirname, '.auth/user.json');

const E2E_WORKSPACE = process.env.E2E_WORKSPACE || '';
const E2E_EMAIL = process.env.E2E_EMAIL || '';
const E2E_PASSWORD = process.env.E2E_PASSWORD || '';

setup('authenticate', async ({ page }) => {
  setup.skip(!E2E_EMAIL || !E2E_PASSWORD || !E2E_WORKSPACE, 'E2E credentials not configured');

  await page.goto('/login');

  await page.getByPlaceholder('your-company').fill(E2E_WORKSPACE);
  await page.getByPlaceholder('you@company.com').fill(E2E_EMAIL);
  await page.getByPlaceholder('••••••••').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: /sign in|log in|login/i }).click();

  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 15000 });

  await expect(page.locator('nav, header').first()).toBeVisible();

  await page.context().storageState({ path: authFile });
});
