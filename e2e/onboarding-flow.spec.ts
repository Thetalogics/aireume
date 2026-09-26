import { test, expect } from '@playwright/test';
import { apiBaseUrl, completeOnboardingWithoutSeedData, enrollRequiredMfa } from './helpers';

/**
 * Full self-serve onboarding: register → verify (test helper) → login → wizard → analyze.
 *
 * Requires backend API with E2E_TEST_MODE=1 (staging or local with PLAYWRIGHT_API_URL).
 */
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('Onboarding flow (register → verify → wizard → analyze)', () => {
  test('new workspace can register, verify, skip wizard, and reach analyze', async ({ page, request }) => {
    const stamp = Date.now();
    const companyName = `E2E Corp ${stamp}`;
    const email = `e2e-${stamp}@example.com`;
    const password = 'E2ETestPass123!';

    // Register
    await page.goto('/register');
    await page.waitForLoadState('networkidle');
    await page.getByLabel('Company Name').fill(companyName);
    await page.getByLabel('Work Email').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: /create workspace/i }).click();

    await expect(page).toHaveURL(/\/check-email/, { timeout: 15000 });

    // Verify via E2E test endpoint
    const verifyResp = await request.post(`${apiBaseUrl()}/api/auth/test/verify-email`, {
      data: { email },
    });
    expect(verifyResp.ok(), `verify-email failed: ${await verifyResp.text()}`).toBeTruthy();

    // Login — workspace slug is stored in sessionStorage during register
    const slug = await page.evaluate(() => sessionStorage.getItem('aria_workspace_slug') || '');
    expect(slug.length).toBeGreaterThan(0);

    await page.goto('/login');
    await page.getByPlaceholder('your-company').fill(slug);
    await page.getByPlaceholder('you@company.com').fill(email);
    await page.getByPlaceholder('••••••••').fill(password);
    await page.getByRole('button', { name: /sign in/i }).click();

    await enrollRequiredMfa(page);
    await completeOnboardingWithoutSeedData(page);

    // First analysis entry point
    await page.goto('/analyze');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText(/step 1|opening.*skills|job description/i).first()).toBeVisible({
      timeout: 15000,
    });
    await expect(
      page.getByText(/select an opening to start|quick screen without a requisition/i).first()
    ).toBeVisible();
  });
});
