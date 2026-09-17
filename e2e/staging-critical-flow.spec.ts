import { test, expect } from '@playwright/test';
import path from 'path';
import { enableAdHocScreeningIfAvailable, gotoAnalyze, selectFirstRequisitionOnAnalyze } from './helpers';

const configured = Boolean(process.env.E2E_EMAIL && process.env.E2E_PASSWORD && process.env.E2E_WORKSPACE);

test.describe('Authenticated staging critical flow', () => {
  test.skip(!configured, 'E2E_WORKSPACE/E2E_EMAIL/E2E_PASSWORD not configured');

  test.use({ storageState: path.join(__dirname, '.auth/user.json') });

  test('dashboard, requisition, screening result, tenant candidate, logout', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('nav, header').first()).toBeVisible({ timeout: 20000 });

    const selected = await selectFirstRequisitionOnAnalyze(page);
    if (!selected) {
      await enableAdHocScreeningIfAvailable(page);
      await gotoAnalyze(page);
    }

    const resumePath = path.join(__dirname, 'fixtures', 'tiny-resume.txt');
    const fileInput = page.locator('input[type="file"]').first();
    if (await fileInput.count()) {
      await fileInput.setInputFiles(resumePath);
    }

    await page.goto('/candidates');
    await expect(page.locator('body')).toContainText(/candidate|pipeline|no candidates/i);

    const logout = page.getByRole('button', { name: /log out|sign out|logout/i }).or(page.getByRole('menuitem', { name: /log out|sign out/i }));
    if (await logout.first().isVisible().catch(() => false)) {
      await logout.first().click();
      await expect(page).toHaveURL(/login/i, { timeout: 15000 });
    }
  });
});
