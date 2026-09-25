import { test, expect } from '@playwright/test';

// These run WITHOUT the saved auth state so we can exercise the login page itself.
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('Auth workflow (login page)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });
    await page.route('**/api/auth/oauth/providers', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ providers: [] }),
      });
    });
    await page.route('**/api/sso/config/**', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Workspace not found' }),
      });
    });
    await page.route('**/api/auth/login', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid email or password' }),
      });
    });
    await page.route('**/api/auth/refresh', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });
  });

  test('renders workspace, email and password fields', async ({ page }) => {
    await page.goto('/login');
    await page.waitForLoadState('networkidle');

    await expect(page.getByPlaceholder('your-company')).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel('Email')).toBeVisible();
    await expect(page.getByPlaceholder('••••••••')).toBeVisible();
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
  });

  test('password visibility can be toggled (accessible control)', async ({ page }) => {
    await page.goto('/login');
    const pw = page.getByPlaceholder('••••••••');
    await expect(pw).toHaveAttribute('type', 'password');

    await page.getByRole('button', { name: /show password/i }).click();
    await expect(pw).toHaveAttribute('type', 'text');

    await page.getByRole('button', { name: /hide password/i }).click();
    await expect(pw).toHaveAttribute('type', 'password');
  });

  test('invalid credentials surface an error message', async ({ page }) => {
    await page.goto('/login');
    await page.getByPlaceholder('your-company').fill('does-not-exist');
    await page.getByLabel('Email').fill('nobody@example.com');
    await page.getByPlaceholder('••••••••').fill('WrongPassword123!');
    await page.getByRole('button', { name: /sign in/i }).click();

    // Should show an error banner rather than navigating away
    await expect(page.getByText(/invalid|incorrect|not found|failed/i).first())
      .toBeVisible({ timeout: 15000 });
  });

  test('offers a link to create a workspace', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('link', { name: /create workspace/i })).toBeVisible();
  });
});
