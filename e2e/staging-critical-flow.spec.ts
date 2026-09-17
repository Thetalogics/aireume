import { test, expect } from '@playwright/test';
import fs from 'fs';
import os from 'os';
import path from 'path';
import {
  confirmSkillsIfNeeded,
  goToAnalyzeUploadStep,
  prepareAnalyzeJob,
} from './helpers';

const configured = Boolean(process.env.E2E_EMAIL && process.env.E2E_PASSWORD && process.env.E2E_WORKSPACE);

const STABLE_JD = [
  'We are hiring a Senior Backend Engineer to design and operate Python FastAPI services.',
  'The role requires production experience with PostgreSQL, REST APIs, SQLAlchemy, and Redis.',
  'You will own resume screening workflows, structured logging, and reliable background jobs.',
  'Strong communication, code review discipline, and automated testing are required.',
  'Nice to have: Docker, GitHub Actions, Playwright, and experience with LLM-backed products.',
  'This job description is long enough for ARIA to parse skills with more than eighty words total.',
].join(' ');

function writeUniqueResume(runId: string, candidateName: string, email: string): string {
  const resumePath = path.join(os.tmpdir(), `aria-e2e-resume-${runId}.txt`);
  const body = [
    candidateName,
    `email: ${email}`,
    'Python, SQL, FastAPI, PostgreSQL, Redis',
    'Senior software engineer with five years of backend experience building APIs and screening tools.',
    `Synthetic E2E fixture run ${runId}. No real PII.`,
  ].join('\n');
  fs.writeFileSync(resumePath, body, 'utf8');
  return resumePath;
}

test.describe('Authenticated staging critical flow', () => {
  test.skip(!configured, 'E2E_WORKSPACE/E2E_EMAIL/E2E_PASSWORD not configured');

  test.use({ storageState: path.join(__dirname, '.auth/user.json') });

  test('dashboard, requisition, screening result, tenant candidate, logout', async ({ page }) => {
    test.setTimeout(180_000);
    const runId = `${Date.now()}-${process.env.GITHUB_RUN_ID || 'local'}`;
    const candidateName = `E2E Candidate ${runId}`;
    const email = `e2e+${runId}@example.invalid`;
    const resumePath = writeUniqueResume(runId, candidateName, email);

    await page.goto('/');
    await expect(page.locator('nav, header').first()).toBeVisible({ timeout: 20000 });
    await expect(page).not.toHaveURL(/\/login/i);

    const mode = await prepareAnalyzeJob(page);
    if (mode === 'ad-hoc') {
      const jd = page.locator('textarea').first();
      await expect(jd).toBeVisible({ timeout: 15000 });
      await jd.fill(STABLE_JD);
    }

    await confirmSkillsIfNeeded(page);
    await goToAnalyzeUploadStep(page);

    const fileInput = page.locator('input[type="file"]').first();
    await expect(fileInput).toBeAttached({ timeout: 15000 });
    await fileInput.setInputFiles(resumePath);
    await expect(page.getByText(/1 resume ready/i)).toBeVisible({ timeout: 15000 });

    const analyzeBtn = page.getByRole('button', { name: /analyze 1 resume/i });
    await expect(analyzeBtn).toBeEnabled({ timeout: 15000 });
    await analyzeBtn.click();

    const pageError = page.locator('p.text-sm.font-semibold.text-red-900');
    await expect.poll(
      async () => {
        const err = (await pageError.textContent().catch(() => null))?.trim();
        if (err) return `error:${err}`;
        if (await page.getByRole('button', { name: /view top candidate/i }).isVisible().catch(() => false)) {
          return 'done';
        }
        if (await page.getByText(/analysis complete/i).first().isVisible().catch(() => false)) {
          return 'done';
        }
        if (await page.locator('table').getByText(candidateName).first().isVisible().catch(() => false)) {
          return 'done';
        }
        return 'waiting';
      },
      { timeout: 120000, message: 'Screening did not complete with a visible result' },
    ).toBe('done');

    await expect(
      page.getByRole('button', { name: /view top candidate/i })
        .or(page.getByText(/analysis complete/i))
        .or(page.locator('table').getByText(candidateName)),
    ).toBeVisible();

    await page.goto('/candidates');
    await page.getByPlaceholder(/search by name or email/i).fill(candidateName);
    await expect(page.getByText(candidateName).first()).toBeVisible({ timeout: 20000 });

    await page.getByRole('button', { name: 'User menu' }).click();
    await page.getByRole('menuitem', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login/i, { timeout: 15000 });

    await page.goto('/candidates');
    await expect(page).toHaveURL(/\/login/i, { timeout: 15000 });
  });
});
