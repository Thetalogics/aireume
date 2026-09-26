import { expect, test } from '@playwright/test';
import { apiBaseUrl, completeOnboardingWithoutSeedData, enrollRequiredMfa } from './helpers';

test.use({ storageState: { cookies: [], origins: [] } });

test.describe('CI real-backend integration', () => {
  test('registers, enrolls MFA, persists onboarding, and revokes the session', async ({ page, request }) => {
    const runId = `${Date.now()}-${process.env.GITHUB_RUN_ID || 'local'}`;
    const companyName = `ARIA Integration ${runId}`;
    const email = `aria-integration-${runId}@example.com`;
    const password = 'E2EIntegration123!';

    const ready = await request.get(`${apiBaseUrl()}/ready`);
    expect(ready.ok(), `backend readiness failed: ${await ready.text()}`).toBeTruthy();

    await page.goto('/register');
    await page.getByLabel('Company Name').fill(companyName);
    await page.getByLabel('Work Email').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: /create workspace/i }).click();
    await expect(page).toHaveURL(/\/check-email/, { timeout: 20_000 });

    const workspace = await page.evaluate(() => sessionStorage.getItem('aria_workspace_slug') || '');
    expect(workspace, 'registration did not persist the workspace slug').not.toBe('');

    const verify = await request.post(`${apiBaseUrl()}/api/auth/test/verify-email`, {
      data: { email },
    });
    expect(verify.ok(), `test-only email verification failed: ${await verify.text()}`).toBeTruthy();

    await page.goto('/login');
    await page.getByLabel('Workspace').fill(workspace);
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: /sign in/i }).click();

    await enrollRequiredMfa(page);
    await completeOnboardingWithoutSeedData(page);

    const me = await page.request.get(`${apiBaseUrl()}/api/auth/me`);
    expect(me.ok(), `authenticated GET /api/auth/me failed: ${await me.text()}`).toBeTruthy();
    const identity = await me.json();
    expect(String(identity.tenant?.slug || '').toLowerCase()).toBe(workspace.toLowerCase());
    expect(String(identity.user?.email || '').toLowerCase()).toBe(email.toLowerCase());

    const onboarding = await page.request.get(`${apiBaseUrl()}/api/onboarding/status`);
    expect(onboarding.ok(), `onboarding status failed: ${await onboarding.text()}`).toBeTruthy();
    expect((await onboarding.json()).completed).toBe(true);

    const subscription = await page.request.get(`${apiBaseUrl()}/api/subscription`);
    expect(subscription.ok(), `subscription lookup failed: ${await subscription.text()}`).toBeTruthy();
    const subscriptionBody = await subscription.json();
    expect(String(subscriptionBody.current_plan?.plan?.name || '').toLowerCase()).toBe('starter');

    await page.reload();
    await expect(page.locator('nav, header').first()).toBeVisible({ timeout: 20_000 });

    await page.getByRole('button', { name: 'User menu' }).click();
    await page.getByRole('menuitem', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login/i, { timeout: 15_000 });
    await page.goto('/requisitions');
    await expect(page).toHaveURL(/\/login/i, { timeout: 15_000 });
  });
});
