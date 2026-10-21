# Frontend Correctness and UI/UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate frontend correctness and dependency findings, enforce bundle budgets, and audit every application route for accessibility, responsive behavior, and visual quality.

**Architecture:** Fix reactive correctness before visual work, then simplify dependencies and chunk boundaries. Build a route/state audit manifest that drives browser checks and screenshot review. Apply targeted improvements within the current React/Tailwind system, using the public-page taste rules only on public-facing surfaces and application UX rules on authenticated dashboards.

**Tech Stack:** React 18, React Router, Tailwind CSS 3, Vite, Vitest, Testing Library, Playwright, ESLint

**Spec:** `docs/superpowers/specs/2026-10-21-production-readiness-ui-ux-remediation-design.md`

## Global Constraints

- Preserve the existing framework and styling system.
- Do not add a UI library or dependency where platform/CSS capabilities suffice.
- Resolve lint causes; do not disable `react-hooks/exhaustive-deps` or weaken warning thresholds.
- Maintain WCAG AA contrast, keyboard access, visible focus, reduced motion, and 44×44px practical touch targets.
- Protect existing uncommitted `package.json`, `package-lock.json`, and MFA settings changes by reconciling rather than overwriting them.

## Review Focus

- Hook corrections must not create request loops, duplicate mutations, stale filters, or state resets.
- Dependency removal must preserve PDF/export behavior or replace it with a verified product-equivalent flow.
- Lazy chunks must not introduce React initialization/order failures or broken direct navigation.
- Auth, onboarding, plan, MFA, admin, and tenant gates must be represented in route audit fixtures.
- Loading, empty, error, validation, denied, and reduced-motion states must be usable without a mouse.

---

### Task 1: Turn frontend warnings into a hard gate

**Files:**
- Modify: `app/frontend/package.json`
- Modify: `.github/workflows/ci.yml`
- Create: `app/frontend/src/__tests__/lintContract.test.js`

**Interfaces:**
- Produces: `npm run lint` exits nonzero on any warning.

- [ ] **Step 1: Add a failing contract test** that inspects the lint script for `--max-warnings=0`.
- [ ] **Step 2: Run** the contract test and confirm failure.
- [ ] **Step 3: Add the strict flag** without adding exclusions or disabling rules.
- [ ] **Step 4: Keep this task uncommitted until Tasks 2–3 reach zero warnings,** then run lint and commit the complete lint-fix set.

### Task 2: Correct hook dependency behavior

**Files:**
- Modify: warning-producing components/pages reported by `npm run lint`, including `ComparisonMatrix.jsx`, `HandoffPackage.jsx`, `useAnalyzePage.js`, `AnalyticsHub.jsx`, `NotificationContext.tsx`, `UserPreferencesContext.tsx`, `usePermissions.js`, `CandidatesPage.jsx`, `InterviewDetailPage.jsx`, `RecruiterInterviewPage.jsx`, `ReportPage.jsx`, `SettingsPage.jsx`, `VoiceScreeningPage.jsx`, and `admin/SSOPage.jsx`
- Test: colocated existing tests plus focused files under `app/frontend/src/__tests__/`

**Interfaces:**
- Produces: stable callbacks/effects with explicit primitive dependencies and unchanged user-visible request semantics.

- [ ] **Step 1: Group warnings by behavior** and add failing tests for fetch counts, filter changes, route-state initialization, blob URL cleanup, notification completion, and permission updates.
- [ ] **Step 2: Run each focused test** and confirm the stale or repeated behavior before changing code.
- [ ] **Step 3: Fix one behavior group at a time** using `useCallback`, effect-local functions, derived values, or reducers only where the state transition requires it.
- [ ] **Step 4: Run focused tests after each group** and ensure no request loop appears.
- [ ] **Step 5: Run the full frontend suite.**

### Task 3: Separate component-only exports

**Files:**
- Modify: all files reported by `react-refresh/only-export-components`
- Create: focused constants/context/hook modules adjacent to their consumers
- Modify: imports and affected tests

**Interfaces:**
- Produces: component modules export components only; hooks, contexts, and constants retain their current public names from dedicated modules.

- [ ] **Step 1: Add import-contract tests** for moved public exports.
- [ ] **Step 2: Move only non-component exports** from each warned module; remove unused eslint-disable comments in `IconButton.jsx` and `AuthContext.tsx`.
- [ ] **Step 3: Run** `npm run lint`; expect zero errors and zero warnings.
- [ ] **Step 4: Run** `npm test`; expect all frontend tests to pass.
- [ ] **Step 5: Commit Tasks 1–3** with `fix: enforce warning-free React code`.

### Task 4: Remove dependency vulnerabilities and reduce PDF weight

**Files:**
- Modify: `app/frontend/package.json`
- Modify: `app/frontend/package-lock.json`
- Modify: PDF/export callers found with `rg -n "html2pdf|jspdf|html2canvas" app/frontend/src`
- Test: affected report/export tests

**Interfaces:**
- Produces: equivalent export behavior using the smallest safe implementation; no unresolved production advisory.

- [ ] **Step 1: Add behavior tests** for the current export contract: content, filename, failure feedback, and print/export action availability.
- [ ] **Step 2: Inventory transitive advisories** with `npm audit --omit=dev --json` and trace each to a direct package.
- [ ] **Step 3: Prefer removing `html2pdf.js`** in favor of an existing server export or browser print flow if tests prove equivalence; otherwise upgrade to a non-vulnerable compatible chain.
- [ ] **Step 4: Update axios/Vite and other affected direct dependencies** within compatible versions; do not use `npm audit fix --force` blindly.
- [ ] **Step 5: Run** frontend tests, lint, build, and `npm audit --omit=dev`; expect no unresolved production vulnerability.
- [ ] **Step 6: Commit** with `fix: remove vulnerable frontend dependency paths`.

### Task 5: Enforce useful bundle boundaries

**Files:**
- Modify: `app/frontend/vite.config.js`
- Create: `scripts/check-bundle-budget.mjs`
- Test: `tests/check-bundle-budget.test.mjs`
- Modify: `package.json`

**Interfaces:**
- Produces: `checkBundleBudget(manifestPath, limits) -> violation[]`; limits apply to initial JS and individual async chunks.

- [ ] **Step 1: Add failing budget tests** using fixture manifests above and below limits.
- [ ] **Step 2: Remove the all-`node_modules` vendor bucket** and use stable feature/library boundaries only where measured output benefits.
- [ ] **Step 3: Ensure heavy optional features load through existing route-level lazy imports** and do not enter the initial graph.
- [ ] **Step 4: Build twice, compare manifests,** and set budgets below the current 949 KB/984 KB findings with documented gzip and raw limits.
- [ ] **Step 5: Add the budget check to `npm run build` or root verification.**
- [ ] **Step 6: Commit** with `perf: enforce frontend bundle budgets`.

### Task 6: Create the UI route/state audit harness

**Files:**
- Create: `e2e/ui-audit/routes.ts`
- Create: `e2e/ui-audit/fixtures.ts`
- Create: `e2e/ui-audit/ui-audit.spec.ts`
- Modify: `playwright.config.ts` or add `playwright.ui-audit.config.ts`
- Create: `docs/production-readiness/UI_UX_AUDIT.md`

**Interfaces:**
- Produces: typed route entries containing path, role, viewport set, theme set, fixture state, and expected primary landmark/heading.

- [ ] **Step 1: Inventory every route in `app/frontend/src/App.jsx`** and fail a test when a non-redirect route has no manifest entry.
- [ ] **Step 2: Add reusable authenticated fixtures** for anonymous, user, recruiter/admin, platform admin, onboarding incomplete, unverified email, MFA enrollment, plan locked, and permission denied.
- [ ] **Step 3: Add automated checks** for horizontal overflow, duplicate IDs, missing landmarks/headings, unlabeled controls, focus visibility, console errors, failed requests, and reduced-motion behavior.
- [ ] **Step 4: Run the harness** at desktop, tablet, and mobile sizes; add every failure to the ledger before fixing.
- [ ] **Step 5: Commit** the harness and initial audit evidence with `test: add route-complete UI audit harness`.

### Task 7: Remediate shared application UX and accessibility

**Files:**
- Modify: shared layout/navigation/form/modal/table/button/skeleton components identified by Task 6
- Consolidate: `app/frontend/src/components/Skeleton.jsx` and `app/frontend/src/components/ui/Skeleton.jsx`
- Test: focused component tests and `e2e/ui-audit/ui-audit.spec.ts`

**Interfaces:**
- Produces: one shared skeleton API and accessible primitives used across routes.

- [ ] **Step 1: Add failing component tests** for skip link, active navigation, focus trapping/restoration, accessible icon buttons, inline errors, live regions, and reduced motion.
- [ ] **Step 2: Fix shared primitives first** so route-level fixes inherit correct behavior.
- [ ] **Step 3: Consolidate the duplicate skeleton implementation** after confirming all call sites; preserve layout-specific variants through props rather than a second component.
- [ ] **Step 4: Re-run component and route-audit suites** and close shared findings in the ledger.
- [ ] **Step 5: Commit** with `fix: harden shared UI accessibility states`.

### Task 8: Audit and polish every route

**Files:**
- Modify: route pages/components identified by Task 6
- Update: `docs/production-readiness/UI_UX_AUDIT.md`
- Save evidence: `.taste-skill/inspect/` only if repository policy tracks generated evidence; otherwise link CI artifacts in the audit document

**Interfaces:**
- Consumes: the route manifest and accessible shared primitives.
- Produces: closed visual/UX findings with before/after evidence.

- [ ] **Step 1: For public pages, run** `node C:/Users/DELL/.codex/skills/taste-skill/scripts/lint.mjs app/frontend/src` and `inspect.mjs` against the running public routes.
- [ ] **Step 2: For app pages, query UI/UX guidance** one observable outcome at a time and apply only verified React/Tailwind guidance.
- [ ] **Step 3: Fix route findings in user-flow groups**: auth/onboarding, core screening/reporting, requisitions/candidates, interviews/media, analytics/team/settings, then administration.
- [ ] **Step 4: After each group, run functional tests, keyboard checks, and screenshots** at 1440, 768, and 390 pixels.
- [ ] **Step 5: Complete three screenshot critique/fix rounds for public surfaces** and at least one before/after review for every authenticated route group.
- [ ] **Step 6: Run** the complete UI audit, frontend test, lint, and build gates; close every actionable UI finding.
- [ ] **Step 7: Commit each route group separately** with scoped `fix(ui): ...` messages.

