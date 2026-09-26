import { expect, type Page } from '@playwright/test';
import { generateTotp } from './totp';

/** Authenticated home/dashboard route (there is no `/home` route). */
export async function gotoDashboard(page: Page) {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
}

export async function expectAppShell(page: Page) {
  await expect(page.locator('nav, header').first()).toBeVisible({ timeout: 15000 });
}

export async function gotoAnalyze(page: Page) {
  await page.goto('/analyze');
  await page.waitForLoadState('networkidle');
}

export async function expectRequisitionPicker(page: Page) {
  await expect(page.getByText(/select an opening to start/i).first()).toBeVisible({
    timeout: 15000,
  });
}

export async function selectFirstRequisitionOnAnalyze(page: Page): Promise<boolean> {
  const picker = page.getByText(/select an opening to start/i).first();
  if (!(await picker.isVisible({ timeout: 8000 }).catch(() => false))) {
    return page.getByText(/screening for/i).first().isVisible().catch(() => false);
  }
  const reqButton = page.locator('.grid.gap-2.max-h-80.overflow-y-auto button').first();
  if (!(await reqButton.isVisible({ timeout: 10000 }).catch(() => false))) {
    return false;
  }
  await reqButton.click();
  await expect(page.getByText(/screening for/i).first()).toBeVisible({ timeout: 15000 });
  return true;
}

export async function enableAdHocScreeningIfAvailable(page: Page): Promise<boolean> {
  const link = page.getByRole('button', { name: /quick screen without a requisition/i });
  if (!(await link.isVisible({ timeout: 3000 }).catch(() => false))) {
    return false;
  }
  await link.click();
  await expect(page.locator('textarea').first()).toBeVisible({ timeout: 10000 });
  return true;
}

/**
 * Make Analyze ready for JD/skills confirmation. Fails if neither a requisition
 * nor an ad-hoc JD path is actually available.
 */
export async function prepareAnalyzeJob(page: Page): Promise<'requisition' | 'ad-hoc'> {
  await gotoAnalyze(page);

  const selected = await selectFirstRequisitionOnAnalyze(page);
  if (selected) {
    return 'requisition';
  }

  const adHoc = await enableAdHocScreeningIfAvailable(page);
  if (adHoc) {
    return 'ad-hoc';
  }

  const textareaVisible = await page.locator('textarea').first().isVisible().catch(() => false);
  if (textareaVisible) {
    return 'ad-hoc';
  }

  const hint = (await page.locator('body').innerText().catch(() => '')).slice(0, 500);
  throw new Error(
    `Analyze is not ready for screening (no opening selected and no ad-hoc JD). url=${page.url()} ui=${hint}`,
  );
}

export async function confirmSkillsIfNeeded(page: Page) {
  const skipDefaults = page.getByRole('button', { name: /skip & use defaults/i });
  const confirmAnalyze = page.getByRole('button', { name: /confirm & analyze/i });
  const alreadyConfirmed = page.getByRole('button', { name: /re-edit/i });

  await expect.poll(
    async () => {
      const parseError = (await page.getByText(/could not parse|parse error|failed to parse/i).first().textContent().catch(() => null))?.trim();
      if (parseError) return `parse-error:${parseError}`;
      if (await alreadyConfirmed.isVisible().catch(() => false)) return 'ready';
      if (await skipDefaults.isVisible().catch(() => false)) return 'ready';
      if (await confirmAnalyze.isVisible().catch(() => false)) return 'ready';
      return 'waiting';
    },
    { timeout: 60000, message: 'JD skill confirmation never became available' },
  ).toBe('ready');

  if (await alreadyConfirmed.isVisible().catch(() => false)) {
    return;
  }
  if (await skipDefaults.isVisible().catch(() => false)) {
    await skipDefaults.click();
    return;
  }
  if (await confirmAnalyze.isVisible().catch(() => false)) {
    await confirmAnalyze.click();
    return;
  }
  throw new Error('Skills were not confirmed and no confirm/skip control is visible');
}

export async function goToAnalyzeUploadStep(page: Page) {
  const uploadStep = page.getByRole('button', { name: /^upload$/i }).first();
  await expect(uploadStep).toBeVisible({ timeout: 15000 });
  await uploadStep.click();
  await expect(page.getByText(/step 2: upload & analyze/i)).toBeVisible({ timeout: 15000 });
}

export function apiBaseUrl(): string {
  return (
    process.env.PLAYWRIGHT_API_URL
    || process.env.PLAYWRIGHT_BASE_URL
    || 'https://airesume-staging.thetalogics.com'
  ).replace(/\/$/, '');
}

export async function enrollRequiredMfa(page: Page) {
  await expect(page).toHaveURL(/\/settings\?tab=security/, { timeout: 20_000 });
  await expect(page.getByText(/MFA is required for admin and platform roles/i)).toBeVisible();
  await page.getByRole('switch', { name: 'Off' }).click();
  const secret = (await page.locator('code').first().textContent())?.trim() || '';
  expect(secret, 'MFA setup did not expose a TOTP secret').not.toBe('');
  await page.getByLabel('Authenticator code').fill(generateTotp(secret));
  await page.getByRole('button', { name: /verify and enable/i }).click();
  await expect(page.getByRole('switch', { name: 'Enabled' })).toBeVisible({ timeout: 15_000 });
}

export async function completeOnboardingWithoutSeedData(page: Page) {
  await page.goto('/onboarding');
  await expect(page.getByText(/welcome to aria/i)).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: /skip for now/i }).click();
  await expect(page.getByText('Step 2 of 4', { exact: true }).first()).toBeVisible();
  await page.getByRole('checkbox', { name: /candidate and job data are processed by AI providers/i }).check();
  await page.getByRole('button', { name: /^next$/i }).click();
  await expect(page.getByText('Step 3 of 4', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: /skip for now/i }).click();
  await expect(page.getByText(/you.re all set/i)).toBeVisible();
  await page.getByRole('button', { name: /not now, go to dashboard/i }).click();
  await expect(page.locator('nav, header').first()).toBeVisible({ timeout: 20_000 });
}
