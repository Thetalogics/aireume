# Operations, Architecture, and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove confirmed architectural leftovers, pin production infrastructure, reconcile product versions, and prove the release candidate through clean full-stack validation.

**Architecture:** Use evidence before deletion or decomposition, keep production image inputs immutable, and make CI enforce the same gates developers run locally. Treat hosted repository controls and staging deployment as explicit external gates requiring observable verification.

**Tech Stack:** Docker Compose, GitHub Actions, Alembic, Python packaging, npm, Trivy, gitleaks, pip-audit, Playwright

**Spec:** `docs/superpowers/specs/2026-10-21-production-readiness-ui-ux-remediation-design.md`

## Global Constraints

- Delete only code/configuration proven unused by runtime, imports, deployment manifests, and documentation.
- Pin production images to reviewed immutable versions or digests; document how to update them.
- Do not weaken security scanners, budgets, coverage, or required checks to make CI green.
- Keep migrations at one expected head and verify upgrade/downgrade/upgrade behavior.
- External settings are closed only with fetched evidence from the hosting provider.

## Review Focus

- Removing a “dead” service must not break dynamic imports, CLI entry points, background jobs, or migrations.
- Module splits must preserve import paths or update every caller atomically.
- Image pins must support the deployed architecture and retain data compatibility.
- Dependency audit exceptions must be current, justified, and absent when a fix exists.
- Release verification must start from a clean supported environment, not cached local state.

---

### Task 1: Prove and remove dead or duplicate code

**Files:**
- Evaluate/remove: WIP services and legacy analysis service identified by the audit
- Evaluate/remove: `app/nginx/nginx.prod.conf`
- Modify: affected imports, tests, compose files, and documentation

**Interfaces:**
- Produces: an evidence table in the findings ledger listing searches and runtime references for every deletion.

- [ ] **Step 1: Trace each candidate** with `rg`, import analysis, route/job registration, compose/config references, and test-only references.
- [ ] **Step 2: Add a failing import/startup test** if deleting or relocating a public module path could affect runtime.
- [ ] **Step 3: Delete only candidates with no production consumer;** update callers/configuration atomically.
- [ ] **Step 4: Run backend collection, startup smoke tests, frontend tests/build, and compose config validation.**
- [ ] **Step 5: Commit each independent removal** with `refactor: remove unused ...`.

### Task 2: Decompose oversized modules only at tested seams

**Files:**
- Evaluate/modify: `app/backend/routes/analyze.py`
- Evaluate/modify: the audited hybrid pipeline module
- Evaluate/modify: `app/backend/models/db_models.py`
- Evaluate/modify: `app/frontend/src/pages/ReportPage.jsx`
- Test: existing focused suites plus characterization tests added beside each module

**Interfaces:**
- Produces: focused modules with stable public imports; no compatibility adapter unless an active caller requires it.

- [ ] **Step 1: Measure responsibilities and change frequency** and record whether each split reduces current remediation risk.
- [ ] **Step 2: For each justified split, add characterization tests** around its public behavior.
- [ ] **Step 3: Extract one cohesive responsibility at a time** without introducing a framework, registry, or generic base class.
- [ ] **Step 4: Run focused and full suites after each extraction.**
- [ ] **Step 5: Mark an item inapplicable with evidence** when splitting would add indirection without reducing active risk.
- [ ] **Step 6: Commit each extraction independently.**

### Task 3: Pin production container inputs

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: other production/staging compose files that use mutable third-party tags
- Create: `docs/operations/DEPENDENCY_UPDATE_RUNBOOK.md`
- Test: compose configuration validation and container scan workflow

**Interfaces:**
- Produces: immutable third-party image references and a repeatable update/rollback procedure.

- [ ] **Step 1: Inventory every `image:` reference** and classify first-party versus third-party.
- [ ] **Step 2: Resolve reviewed compatible versions/digests** for Ollama, Watchtower, and every other mutable production tag.
- [ ] **Step 3: Replace mutable tags** and document architecture, compatibility, update, scan, and rollback steps.
- [ ] **Step 4: Run** `docker compose -f docker-compose.prod.yml config` and configured Trivy scans.
- [ ] **Step 5: Commit** with `build: pin production container images`.

### Task 4: Reconcile versions and dependency audit parity

**Files:**
- Modify: `README.md`
- Modify: `PRODUCT_SPECIFICATION.md`
- Modify: `package.json`
- Modify: `app/frontend/package.json`
- Modify: backend version source found with `rg -n "VERSION|__version__" app/backend`
- Modify: `app/backend/requirements.lock` only through the documented lock process
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: one canonical product version source or a tested synchronization script; Python audits run under the supported runtime.

- [ ] **Step 1: Add a failing version-consistency test** covering README badge, backend version, and package metadata.
- [ ] **Step 2: Select the shipped version from repository/release evidence** and implement the smallest synchronization mechanism.
- [ ] **Step 3: Recreate the Python 3.11 audit environment** and run `pip-audit` against the lock file; resolve findings rather than relying on Python 3.13 build behavior.
- [ ] **Step 4: Verify Presidio and NumPy installation** under the supported runtime and run the previously failing production-hardening test.
- [ ] **Step 5: Run version, dependency, and backend suites.**
- [ ] **Step 6: Commit** with `build: align release versions and Python audit runtime`.

### Task 5: Enforce repository governance

**Files:**
- Modify: `SECURITY.md`
- Create or modify: `docs/operations/REPOSITORY_CONTROLS.md`
- Modify: `.github/workflows/ci.yml` if required check names need stabilization

**Interfaces:**
- Produces: documented required checks and fetched evidence that protection/rulesets are active on release branches.

- [ ] **Step 1: Enumerate exact required CI job names** and verify they are stable.
- [ ] **Step 2: Read current GitHub branch/ruleset configuration** using authenticated repository tooling.
- [ ] **Step 3: If external mutation is not already authorized, request it explicitly;** then enable required reviews, required status checks, dismissal rules, and force-push/deletion protection for release branches.
- [ ] **Step 4: Fetch configuration again** and record redacted evidence in the operations document.
- [ ] **Step 5: Commit documentation/workflow changes** with `ops: enforce release branch controls`.

### Task 6: Run the clean release candidate gate

**Files:**
- Update: `docs/production-readiness/FINDINGS.md`
- Create: `docs/production-readiness/RELEASE_EVIDENCE.md`

**Interfaces:**
- Consumes: all prior plans.
- Produces: a release decision with commands, timestamps, commit SHA, results, and rollback reference.

- [ ] **Step 1: Confirm the worktree contains no unintended changes** and record the candidate SHA.
- [ ] **Step 2: Install from lockfiles in the pinned runtimes** without reusing mutable dependency state.
- [ ] **Step 3: Run** root verification, backend full suite, frontend full suite, strict lint, production build/budgets, Alembic upgrade/downgrade/upgrade, dependency audits, secret scan, and container scan.
- [ ] **Step 4: Run the complete Playwright E2E and UI audit matrices** in production-like staging.
- [ ] **Step 5: Perform staging smoke checks** for authentication, upload, analysis, reports, entitlements, administration, and rollback readiness.
- [ ] **Step 6: Review every ledger row** and reject release if any actionable item is not `closed` with evidence.
- [ ] **Step 7: Request an independent whole-branch code review** and resolve every valid release-blocking finding.
- [ ] **Step 8: Re-run affected gates, finalize release evidence, and commit** with `docs: record production readiness evidence`.
