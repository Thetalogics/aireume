import { expect, test, type Page, type Route } from '@playwright/test';

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

const subscription = {
  current_plan: {
    plan: {
      id: 2,
      name: 'enterprise',
      display_name: 'Enterprise',
      limits: {
        analyses_per_month: 500,
        batch_size: 50,
        team_members: 25,
        storage_gb: 100,
        requisitions: true,
        pipeline: true,
        compare: true,
        analytics: true,
        ai_interviews: true,
      },
    },
    status: 'active',
    billing_cycle: 'monthly',
  },
  enabled_features: ['requisitions', 'pipeline', 'compare', 'analytics', 'ai_interviews'],
  usage: {
    analyses_used: 7,
    analyses_limit: 500,
    storage_used_mb: 128,
    storage_limit_gb: 100,
    team_members_count: 4,
    team_members_limit: 25,
    percent_used: 1.4,
  },
  days_until_reset: 18,
  available_plans: [],
};

async function installAuthenticatedApiMocks(page: Page) {
  const seen = new Set<string>();
  const unexpected = new Set<string>();

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, '') || '/';
    seen.add(path);

    if (path === '/version') {
      await route.fulfill(json({ build_id: null }));
      return;
    }
    if (path === '/auth/me') {
      await route.fulfill(json({
        user: {
          id: 101,
          email: 'recruiter@example.com',
          role: 'admin',
          email_verified: true,
          mfa_required: false,
          mfa_enabled: false,
          is_platform_admin: false,
        },
        tenant: {
          id: 201,
          name: 'CI Smoke Workspace',
          slug: 'ci-smoke',
          subscription_status: 'active',
          onboarding_completed: true,
        },
      }));
      return;
    }
    if (path === '/branding/me') {
      await route.fulfill(json({ branding: { brand_name: 'ARIA' } }));
      return;
    }
    if (path === '/users/me/preferences') {
      await route.fulfill(json({
        preferences: {
          notifications: {
            emailOnComplete: true,
            emailOnBatchComplete: true,
            marketing: false,
          },
          seen_modals: {},
          invited_welcome_dismissed: true,
        },
      }));
      return;
    }
    if (path === '/onboarding/status') {
      await route.fulfill(json({
        completed: true,
        checklist_dismissed: true,
        checklist: {
          invite_team: true,
          create_requisition: true,
          screen_candidate: true,
          review_pipeline: true,
          configure_branding: true,
        },
      }));
      return;
    }
    if (path === '/subscription' || path === '/subscription/plans') {
      await route.fulfill(json(path === '/subscription' ? subscription : []));
      return;
    }
    if (path === '/dashboard/summary') {
      await route.fulfill(json({
        action_items: {
          pending_review: 3,
          shortlisted_count: 2,
          in_progress_analyses: 1,
        },
        weekly_metrics: {
          analyses_this_week: 4,
          shortlisted_this_week: 2,
          avg_fit_score: 78,
        },
        pipeline_by_requisition: [{
          requisition_id: 501,
          title: 'Engineering Manager',
          status: 'open',
          is_calibrated: true,
          total_candidates: 6,
          avg_fit_score: 78,
          by_status: {
            pending: 3,
            shortlisted: 2,
            rejected: 1,
            hired: 0,
          },
        }],
      }));
      return;
    }
    if (path === '/dashboard/activity') {
      await route.fulfill(json({
        activities: [{
          id: 901,
          candidate_name: 'Taylor Morgan',
          jd_name: 'Engineering Manager',
          recommendation: 'Shortlist',
          fit_score: 86,
          timestamp: new Date().toISOString(),
        }],
      }));
      return;
    }
    if (path === '/interviews/analytics') {
      await route.fulfill(json({
        voice: { total: 0, completed: 0, quick_count: 0, deep_count: 0 },
        recruiter: { total: 0, completed: 0 },
      }));
      return;
    }

    unexpected.add(path);
    await route.fulfill(json({}));
  });

  return { seen, unexpected };
}

test.describe('Authenticated CI smoke', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('renders protected dashboard with mocked enterprise tenant APIs', async ({ page }) => {
    const api = await installAuthenticatedApiMocks(page);

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('navigation').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/welcome back, recruiter/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /pending review/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /in progress/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /shortlisted view candidates/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /requisitions/i })).toBeVisible();
    await expect(page.getByText(/engineering manager/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /screen resumes/i })).toBeVisible();

    for (const endpoint of [
      '/auth/me',
      '/onboarding/status',
      '/subscription',
      '/dashboard/summary',
      '/dashboard/activity',
    ]) {
      expect(api.seen.has(endpoint), `expected ${endpoint} to be called`).toBeTruthy();
    }
    expect([...api.unexpected]).toEqual([]);
  });
});
