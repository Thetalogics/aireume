import { test as setup, expect } from '@playwright/test';
import path from 'path';
import { apiBaseUrl } from './helpers';

const authFile = path.join(__dirname, '.auth/user.json');

const E2E_WORKSPACE = process.env.E2E_WORKSPACE || '';
const E2E_EMAIL = process.env.E2E_EMAIL || '';
const E2E_PASSWORD = process.env.E2E_PASSWORD || '';

const SCREENING_ROLES = new Set(['admin', 'recruiter', 'hiring_manager']);

setup('authenticate', async ({ page }) => {
  setup.skip(!E2E_EMAIL || !E2E_PASSWORD || !E2E_WORKSPACE, 'E2E credentials not configured');

  let loginStatus: number | null = null;
  page.on('response', (res) => {
    try {
      const url = res.url();
      if (res.request().method() === 'POST' && url.includes('/api/auth/login')) {
        loginStatus = res.status();
      }
    } catch {
      // Ignore observer errors; login assertions below still fail closed.
    }
  });

  await page.goto('/login');
  await page.waitForLoadState('domcontentloaded');

  await page.getByLabel('Workspace').fill(E2E_WORKSPACE);
  await expect(page.getByLabel('Email')).toBeVisible({ timeout: 10000 });

  if (await page.getByLabel('Authenticator code').isVisible().catch(() => false)) {
    throw new Error(
      'E2E account requires MFA. Use a dedicated staging tenant without MFA for authenticated E2E.',
    );
  }

  await page.getByLabel('Email').fill(E2E_EMAIL);
  await page.locator('#login-password').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: /sign in|log in|login/i }).click();

  const loginError = page.locator('[role="alert"]').first();
  await expect(async () => {
    if (page.url().includes('/login')) {
      const error = (await loginError.textContent().catch(() => null))?.trim() || '(none)';
      const mfa = await page.getByLabel('Authenticator code').isVisible().catch(() => false);
      throw new Error(
        `Authentication did not leave /login (http=${loginStatus ?? 'n/a'} mfa=${mfa} error=${error} url=${page.url()})`,
      );
    }
  }).toPass({ timeout: 15000 });

  const pathName = new URL(page.url()).pathname;
  if (pathName.includes('/onboarding')) {
    throw new Error(
      'E2E workspace onboarding is incomplete. Complete onboarding on the dedicated staging tenant before running authenticated E2E.',
    );
  }

  await expect(page.locator('nav, header').first()).toBeVisible({ timeout: 15000 });

  const meResp = await page.request.get(`${apiBaseUrl()}/api/auth/me`);
  expect(meResp.ok(), `GET /api/auth/me failed with HTTP ${meResp.status()}`).toBeTruthy();
  const me = await meResp.json();
  const slug = String(me.tenant?.slug || '');
  expect(slug.toLowerCase(), `E2E user workspace is '${slug}', expected '${E2E_WORKSPACE}'`).toBe(
    E2E_WORKSPACE.toLowerCase(),
  );
  const role = String(me.user?.role || '').toLowerCase();
  const canScreen = Boolean(me.user?.is_platform_admin) || SCREENING_ROLES.has(role);
  expect(canScreen, `E2E user role '${role}' cannot run screening`).toBeTruthy();
  const status = String(me.tenant?.subscription_status || '');
  if (['canceled', 'cancelled', 'past_due', 'unpaid', 'expired', 'suspended'].includes(status.toLowerCase())) {
    throw new Error(`E2E tenant subscription_status='${status}' blocks screening`);
  }

  const quotaResp = await page.request.get(`${apiBaseUrl()}/api/subscription/check/resume_analysis`);
  expect(quotaResp.ok(), `GET /api/subscription/check/resume_analysis failed with HTTP ${quotaResp.status()}`).toBeTruthy();
  const quota = await quotaResp.json();
  expect(
    quota.allowed === true,
    `E2E tenant cannot screen (quota/plan): ${quota.message || JSON.stringify({ allowed: quota.allowed, limit: quota.limit })}`,
  ).toBeTruthy();

  await page.context().storageState({ path: authFile });
});
