# Production Readiness Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a trustworthy remediation ledger, supported runtime, and single local/CI verification entry point before product fixes begin.

**Architecture:** Keep orchestration at the repository root and delegate to the existing backend, frontend, migration, and E2E commands. Store closure evidence in one versioned Markdown ledger so every baseline and newly discovered finding has an auditable state.

**Tech Stack:** npm scripts, PowerShell, Bash, pytest, Vitest, ESLint, Vite, Playwright, Alembic

**Spec:** `docs/superpowers/specs/2026-10-21-production-readiness-ui-ux-remediation-design.md`

## Global Constraints

- Preserve existing product behavior unless a documented defect or usability failure requires a change.
- Do not rewrite the application, migrate frameworks, or add speculative abstractions.
- Do not overwrite pre-existing uncommitted changes in authentication, production-hardening tests, frontend package files, or MFA settings/tests.
- Each behavioral fix begins with a failing regression test where practical.
- No finding may end as deferred, silently suppressed, or omitted.
- Python 3.11 is the supported audit and CI runtime unless repository evidence establishes a different version.

## Review Focus

- A failed child command must make the root verification command fail with the original exit code.
- Paths containing spaces must work on Windows and Unix runners.
- Optional service-dependent suites must be named and reported, never silently skipped.
- Existing dirty files must remain byte-for-byte unchanged unless a later approved task owns them.
- Ledger validation must reject unknown IDs, duplicate IDs, and invalid terminal states.

---

### Task 1: Capture the baseline and ledger schema

**Files:**
- Create: `docs/production-readiness/FINDINGS.md`
- Create: `scripts/validate_findings.py`
- Test: `tests/test_validate_findings.py`

**Interfaces:**
- Consumes: baseline IDs and lifecycle from the approved spec.
- Produces: `validate_findings(path: Path) -> list[str]`; an empty list means the ledger is structurally valid.

- [ ] **Step 1: Write failing tests** for duplicate IDs, unknown states, missing evidence fields on `closed` findings, and a valid open ledger.
- [ ] **Step 2: Run** `python -m pytest tests/test_validate_findings.py -v`; expect failures because the validator does not exist.
- [ ] **Step 3: Implement** the Markdown table validator and seed all baseline findings as `reproduced`, with columns for owner, state, root cause, tests, evidence, and residual risk.
- [ ] **Step 4: Run** `python -m pytest tests/test_validate_findings.py -v`; expect all tests to pass.
- [ ] **Step 5: Commit** only the ledger, validator, and tests with `test: add remediation ledger gate`.

### Task 2: Define the supported toolchain

**Files:**
- Create: `.nvmrc`
- Create: `.python-version`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: versions already used successfully in CI.
- Produces: explicit Node and Python versions shared by documentation and CI.

- [ ] **Step 1: Add a CI assertion** that prints and checks the selected Python and Node major versions.
- [ ] **Step 2: Run a local equivalent** and confirm it fails before the version files exist.
- [ ] **Step 3: Add exact runtime files** and update README prerequisites; reuse the versions already configured by CI rather than inventing new floors.
- [ ] **Step 4: Validate** YAML syntax and run the version assertion locally.
- [ ] **Step 5: Commit** with `build: pin supported development runtimes`.

### Task 3: Replace the dummy root test command

**Files:**
- Create: `scripts/verify.mjs`
- Modify: `package.json`
- Test: `tests/verify-script.test.mjs`

**Interfaces:**
- Produces: `npm test` for deterministic fast checks and `npm run verify` for the full local release gate.

- [ ] **Step 1: Write failing Node tests** proving command failure propagation, paths with spaces, and named optional-suite reporting.
- [ ] **Step 2: Run** `node --test tests/verify-script.test.mjs`; expect module-not-found failure.
- [ ] **Step 3: Implement** a minimal `child_process.spawnSync` runner with argument arrays, inherited output, and exact exit-code propagation; do not add an orchestration dependency.
- [ ] **Step 4: Wire scripts** so fast verification runs the ledger validator, backend tests, frontend tests, and lint; full verification adds build, Alembic checks, dependency audits, and E2E.
- [ ] **Step 5: Run** the Node tests and `npm test`; record any product failures in the ledger instead of weakening the runner.
- [ ] **Step 6: Commit** with `build: add repository verification entry point`.

### Task 4: Make Python collection deterministic

**Files:**
- Modify: `pytest.ini`
- Modify or rename: colliding top-level test modules identified by `pytest --collect-only`
- Test: collection itself

**Interfaces:**
- Produces: one documented `python -m pytest` collection command with no import-name collision.

- [ ] **Step 1: Run** `python -m pytest --collect-only -q` under Python 3.11 and record the exact collision.
- [ ] **Step 2: Apply the smallest correction**: package test directories or rename only the colliding modules; do not alter test semantics.
- [ ] **Step 3: Re-run** collection twice from a clean process; expect identical counts and zero errors.
- [ ] **Step 4: Run** the directly affected tests.
- [ ] **Step 5: Commit** with `test: make pytest collection deterministic`.

