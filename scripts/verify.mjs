import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { delimiter, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Modern Node cannot spawn .cmd files directly. Run npm's JS entry without a shell.
function executable(command, args, env) {
  if (process.platform !== 'win32' || command !== 'npm.cmd') return [command, args];
  const candidates = [env.npm_execpath];
  const searchPath = Object.entries(env).find(([key]) => key.toLowerCase() === 'path')?.[1] ?? '';
  for (const directory of searchPath.split(delimiter)) {
    const npmCommand = join(directory.replace(/^"|"$/g, ''), 'npm.cmd');
    if (existsSync(npmCommand)) {
      candidates.push(join(dirname(npmCommand), 'node_modules', 'npm', 'bin', 'npm-cli.js'));
    }
  }
  const cli = candidates.find((candidate) => candidate && existsSync(candidate));
  if (!cli) throw new Error('Cannot resolve npm-cli.js from npm_execpath or npm.cmd on PATH');
  return [process.execPath, [cli, ...args]];
}

/** Run named executable/argument gates, stopping at the first failure. */
export function runGates(gates, { list = false } = {}) {
  let pending = false;
  for (const gate of gates) {
    if (gate.excluded) {
      console.log(`[excluded] ${gate.name}: ${gate.excluded}`);
    } else if (gate.pending) {
      console.error(`[pending] ${gate.name}: ${gate.pending}`);
      pending = true;
    } else {
      if (list) console.log(`[planned] ${gate.name}: ${JSON.stringify([gate.command, ...gate.args])}`);
      const env = { ...process.env, ...gate.env };
      for (const name of gate.requiredEnv ?? []) {
        if (list) {
          console.log(`[requires] ${gate.name}: ${name}`);
        } else if (!env[name]?.trim()) {
          console.error(`[required] ${gate.name}: ${name} must be set`);
          pending = true;
        }
      }
    }
  }
  if (pending || list) return pending ? 1 : 0;
  for (const gate of gates) {
    if (gate.excluded) continue;
    console.log(`[run] ${gate.name}`);
    try {
      const env = { ...process.env, ...gate.env };
      const [command, args] = executable(gate.command, gate.args, env);
      const result = spawnSync(command, args, { cwd: gate.cwd, env, stdio: 'inherit', shell: false });
      if (result.error) throw result.error;
      if (result.status !== 0) {
        console.error(`[failed] ${gate.name}: ${result.signal ?? `exit ${result.status}`}`);
        return result.status ?? 1;
      }
    } catch (error) {
      console.error(`[failed] ${gate.name}: ${error.message}`);
      return 1;
    }
  }
  return 0;
}

function repositoryGates(full) {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const frontend = join(root, 'app', 'frontend');
  const python = process.env.PYTHON || (process.env.VIRTUAL_ENV
    ? join(process.env.VIRTUAL_ENV, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    : 'python');
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const gate = (name, command, args, cwd = root, env) => ({ name, command, args, cwd, env });
  const gates = [
    gate('verification runner tests', process.execPath, ['--test', 'tests/verify-script.test.mjs']),
    gate('findings validator', python, ['-c',
      'import sys; from pathlib import Path; from scripts.validate_findings import validate_findings; errors = validate_findings(Path(sys.argv[1])); print("\\n".join(errors) if errors else "Findings ledger valid"); sys.exit(bool(errors))',
      'docs/production-readiness/FINDINGS.md']),
    gate('backend tests', python, ['-m', 'pytest', 'tests', 'app/backend/tests']),
    gate('frontend tests', npm, ['test'], frontend),
    gate('strict frontend lint', npm, ['run', 'lint', '--', '--max-warnings=0'], frontend),
  ];
  if (!full) return gates.concat(
    ['PostgreSQL reliability', 'Redis reliability', 'object-storage GDPR', 'real-backend browser integration']
      .map((name) => ({ name, excluded: 'external services intentionally excluded; run npm run verify (--full)' })),
  );
  const bundle = 'scripts/check-bundle-budget.mjs';
  const exceptions = JSON.parse(readFileSync(join(root, 'security', 'pip-audit-exceptions.json'), 'utf8'));
  gates.push(
    gate('frontend production build', npm, ['run', 'build'], frontend),
    existsSync(join(root, bundle))
      ? gate('bundle budget', process.execPath, [bundle])
      : { name: 'bundle budget', pending: `${bundle} is missing (planned mandatory gate)` },
    gate('Alembic heads', python, ['-m', 'alembic', '-c', 'alembic.ini', 'heads']),
    {
      ...gate('PostgreSQL reliability and Alembic migration checks', python, ['-m', 'pytest',
        'app/backend/tests/test_reliability_postgres.py',
        'app/backend/tests/test_phase0_postgres.py',
        'app/backend/tests/test_governance_postgres.py',
        'app/backend/tests/test_phase0_076_to_head.py',
        'app/backend/tests/test_request_idempotency.py',
        'app/backend/tests/test_voice_schedule_idempotency.py',
        'app/backend/tests/test_migration_owner_lock.py',
        'app/backend/tests/test_backup_restore.py',
        'app/backend/tests/test_deployment_config.py',
        'app/backend/tests/test_worker_health.py',
        'app/backend/tests/test_alembic_drift.py', '-v', '--timeout=120', '--tb=short'], root,
      { RELIABILITY_REQUIRED: '1', GOVERNANCE_POSTGRES_REQUIRED: '1' }),
      requiredEnv: ['DATABASE_URL', 'RELIABILITY_POSTGRES_URL', 'PHASE0_POSTGRES_URL'],
    },
    {
      ...gate('Redis reliability', python, ['-m', 'pytest',
        'app/backend/tests/test_reliability_redis.py',
        'app/backend/tests/test_llm_concurrency_redis.py',
        'app/backend/tests/test_auth_rate_limit_redis.py',
        'app/backend/tests/test_rate_limit_headers_redis.py', '-v', '--timeout=30'], root,
      { RELIABILITY_REDIS_REQUIRED: '1' }),
      requiredEnv: ['REDIS_URL'],
    },
    {
      ...gate('object-storage GDPR', python, ['-m', 'pytest',
        'app/backend/tests/test_gdpr_object_storage_integration.py', '-v', '--timeout=60', '--tb=short'], root,
      { OBJECT_STORAGE_INTEGRATION_REQUIRED: '1' }),
      requiredEnv: ['S3_ENDPOINT', 'S3_BUCKET', 'S3_ACCESS_KEY', 'S3_SECRET_KEY'],
    },
    gate('root dependency audit', npm, ['audit', '--audit-level=high']),
    gate('frontend dependency audit', npm, ['audit', '--omit=dev', '--audit-level=high'], frontend),
    gate('frontend production dependency audit', npm, ['audit', '--omit=dev', '--audit-level=low'], frontend),
    gate('Python dependency audit exception dates', python, ['scripts/check_pip_audit_exceptions.py']),
    gate('Python dependency audit', python, ['-m', 'pip_audit', '-r', 'app/backend/requirements.lock',
      ...exceptions.exceptions.flatMap(({ id }) => ['--ignore-vuln', id])]),
    gate('deterministic browser E2E', npm,
      ['run', 'test:e2e', '--', '--config=playwright.ci.config.ts']),
    gate('real-backend database migrations', python, ['-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head']),
    {
      ...gate('real-backend browser integration', npm,
        ['run', 'test:e2e', '--', '--config=playwright.integration.config.ts'], root, {
          PYTHON_EXECUTABLE: python,
          REDIS_REQUIRED: '1', E2E_TEST_MODE: '1', RUN_BACKGROUND_WORKERS: '0',
          CORS_ORIGINS: 'http://127.0.0.1:5173,http://localhost:5173',
          OLLAMA_BASE_URL: 'https://ollama.com', OLLAMA_MODEL: 'ci-not-invoked',
          OLLAMA_API_KEY: 'ci-not-invoked', OLLAMA_HEALTH_SENTINEL_ENABLED: '0',
          PLAYWRIGHT_BASE_URL: 'http://127.0.0.1:5173', PLAYWRIGHT_API_URL: 'http://127.0.0.1:8000',
        }),
      requiredEnv: ['DATABASE_URL', 'REDIS_URL', 'JWT_SECRET_KEY', 'INTERNAL_SERVICE_SECRET'],
    },
  );
  return gates;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.some((arg) => !['--fast', '--full', '--list'].includes(arg))
      || (args.includes('--fast') && args.includes('--full'))) {
    console.error('Usage: node scripts/verify.mjs [--fast|--full] [--list]');
    process.exitCode = 2;
  } else {
    try {
      process.exitCode = runGates(repositoryGates(args.includes('--full')), { list: args.includes('--list') });
    } catch (error) {
      console.error(`[failed] verification setup: ${error.message}`);
      process.exitCode = 1;
    }
  }
}
