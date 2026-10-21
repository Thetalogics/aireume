import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const runnerUrl = new URL('../scripts/verify.mjs', import.meta.url).href;
const runnerPath = fileURLToPath(new URL('../scripts/verify.mjs', import.meta.url));
const root = fileURLToPath(new URL('..', import.meta.url));

// Real child processes exercise ordering, output, argument boundaries and exits.
function run(gates, options = {}) {
  return spawnSync(process.execPath, ['--input-type=module', '-e',
    `import { runGates } from ${JSON.stringify(runnerUrl)};
     process.exitCode = runGates(${JSON.stringify(gates)}, ${JSON.stringify(options)});`,
  ], { encoding: 'utf8', cwd: root });
}

function child(name, script, args = []) {
  return { name, command: process.execPath, args: ['-e', script, ...args], cwd: root };
}

test('successful gates run in order and inherit stdout and stderr', () => {
  const result = run([
    child('first', 'console.log("first output"); console.error("first error output")'),
    child('second', 'console.log("second output")'),
  ]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /first output[\s\S]*second output/);
  assert.match(result.stderr, /first error output/);
});

test('a failing child preserves its exact exit code and stops later gates', () => {
  const result = run([
    child('failure', 'console.log("failure output"); process.exit(23)'),
    child('must not run', 'console.log("LATER CHILD RAN")'),
  ]);
  assert.equal(result.status, 23, result.stderr);
  assert.match(result.stdout, /failure output/);
  assert.doesNotMatch(result.stdout, /LATER CHILD RAN/);
});

test('paths with spaces and shell metacharacters remain single literal arguments', () => {
  const argument = `${root}a folder with spaces & literal $(text).txt`;
  const result = run([child('arguments',
    'console.log(JSON.stringify({args:process.argv.slice(1),cwd:process.cwd()}))',
    [argument, 'second argument'])]);
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout.trim().split('\n').at(-1));
  assert.deepEqual(output.args, [argument, 'second argument']);
  assert.equal(output.cwd.toLowerCase(), root.replace(/[\\/]$/, '').toLowerCase());
});

test('unavailable mandatory gates are named and fail before children run', () => {
  const result = run([
    child('must not run', 'console.log("CHILD RAN")'),
    { name: 'bundle budget', pending: 'scripts/check-bundle-budget.mjs is missing' },
  ]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /pending.*bundle budget.*check-bundle-budget\.mjs/i);
  assert.doesNotMatch(result.stdout, /CHILD RAN/);
});

test('list mode shows commands and pending gates without running children', () => {
  const result = run([
    child('listed child', 'console.log("EXECUTED CHILD")'),
    { name: 'future gate', pending: 'future-script.mjs is missing' },
  ], { list: true });
  assert.equal(result.status, 1);
  assert.match(result.stdout, /listed child/);
  assert.match(result.stderr, /pending.*future gate/i);
  assert.doesNotMatch(result.stdout, /^EXECUTED CHILD$/m);
});

test('missing executables fail clearly and prevent subsequent commands', () => {
  const result = run([
    { name: 'missing executable', command: 'nonexistent-verify-executable-983172', args: [] },
    child('must not run', 'console.log("LATER CHILD RAN")'),
  ]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /missing executable.*ENOENT/);
  assert.doesNotMatch(result.stdout, /LATER CHILD RAN/);
});

test('missing service requirements fail preflight before any child runs', () => {
  const result = run([
    child('must not run', 'console.log("CHILD RAN")'),
    { ...child('Redis reliability', 'console.log("SERVICE RAN")'),
      requiredEnv: ['VERIFY_TEST_MISSING_REDIS_8916'] },
  ]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /required.*Redis reliability.*VERIFY_TEST_MISSING_REDIS_8916/i);
  assert.doesNotMatch(result.stdout, /CHILD RAN|SERVICE RAN/);
});

test('service requirements accept gate environment without exposing values', () => {
  const result = run([{
    ...child('configured service', 'process.exit(process.env.VERIFY_TEST_SERVICE === "private-value" ? 0 : 8)'),
    requiredEnv: ['VERIFY_TEST_SERVICE'], env: { VERIFY_TEST_SERVICE: 'private-value' },
  }]);
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(result.stdout + result.stderr, /private-value/);
});

test('excluded service gates are labelled in run and list modes and never execute', () => {
  const gates = [
    { ...child('Redis reliability', 'console.log("EXCLUDED CHILD RAN")'), excluded: 'requires --full' },
    child('available check', 'console.log("AVAILABLE CHILD RAN")'),
  ];
  for (const list of [false, true]) {
    const result = run(gates, { list });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /excluded.*Redis reliability.*--full/i);
    assert.doesNotMatch(result.stdout, /^EXCLUDED CHILD RAN$/m);
    if (!list) assert.match(result.stdout, /^AVAILABLE CHILD RAN$/m);
  }
});

test('platform npm command runs without a shell', () => {
  const result = run([{
    name: 'npm startup', command: process.platform === 'win32' ? 'npm.cmd' : 'npm',
    args: ['--version'], cwd: root,
  }]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /^\d+\.\d+\.\d+$/m);
  assert.doesNotMatch(result.stderr, /DEP0190/);
});

test('importing the runner does not execute its CLI', () => {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e',
    `await import(${JSON.stringify(runnerUrl)}); console.log('imported only');`,
  ], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), 'imported only');
});

test('fast CLI list uses the configured Python executable as one argument', () => {
  const result = spawnSync(process.execPath, [runnerPath, '--fast', '--list'], {
    cwd: root, encoding: 'utf8', env: { ...process.env, PYTHON: 'C:/Python path/python.exe' },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /findings validator/);
  assert.match(result.stdout, /backend tests/);
  assert.match(result.stdout, /frontend tests/);
  assert.match(result.stdout, /strict frontend lint/);
  assert.match(result.stdout, /"C:\/Python path\/python.exe"/);
  assert.match(result.stdout, /--max-warnings=0/);
  assert.doesNotMatch(result.stdout, /frontend production build/);
  for (const name of ['PostgreSQL reliability', 'Redis reliability', 'object-storage GDPR', 'real-backend browser integration']) {
    assert.ok(result.stdout.includes(`[excluded] ${name}`), result.stdout);
  }
});

test('full CLI list names release gates and enforces bundle gate availability', () => {
  const result = spawnSync(process.execPath, [runnerPath, '--full', '--list'], {
    cwd: root, encoding: 'utf8',
  });
  const bundleExists = existsSync(new URL('../scripts/check-bundle-budget.mjs', import.meta.url));
  assert.equal(result.status, bundleExists ? 0 : 1);
  for (const name of ['frontend production build', 'Alembic', 'dependency audit', 'E2E']) {
    assert.ok(result.stdout.includes(name), result.stdout);
  }
  for (const command of ['test_reliability_postgres.py', 'test_reliability_redis.py',
    'test_gdpr_object_storage_integration.py', 'playwright.integration.config.ts']) {
    assert.ok(result.stdout.includes(command), result.stdout);
  }
  for (const requirement of ['RELIABILITY_POSTGRES_URL', 'REDIS_URL', 'S3_ENDPOINT', 'S3_BUCKET',
    'S3_ACCESS_KEY', 'S3_SECRET_KEY', 'DATABASE_URL', 'JWT_SECRET_KEY', 'INTERNAL_SERVICE_SECRET']) {
    assert.ok(result.stdout.includes(requirement), result.stdout);
  }
  assert.match(result.stdout, /frontend dependency audit:.*"audit","--omit=dev","--audit-level=high"/);
  assert.match(result.stdout, /frontend production dependency audit:.*"audit","--omit=dev","--audit-level=low"/);
  if (bundleExists) {
    assert.match(result.stdout, /bundle budget.*check-bundle-budget\.mjs/);
  } else {
    assert.match(result.stderr, /pending.*bundle budget.*check-bundle-budget\.mjs/i);
  }
});

test('full CLI fails missing service preflights without starting a suite', () => {
  const env = { ...process.env };
  for (const name of ['REDIS_URL', 'RELIABILITY_POSTGRES_URL', 'PHASE0_POSTGRES_URL', 'S3_ENDPOINT',
    'S3_BUCKET', 'S3_ACCESS_KEY', 'S3_SECRET_KEY', 'DATABASE_URL', 'JWT_SECRET_KEY', 'INTERNAL_SERVICE_SECRET']) {
    delete env[name];
  }
  const result = spawnSync(process.execPath, [runnerPath, '--full'], { cwd: root, encoding: 'utf8', env });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /required.*Redis reliability.*REDIS_URL/i);
  assert.match(result.stderr, /required.*object-storage GDPR.*S3_ENDPOINT/i);
  assert.match(result.stderr, /required.*real-backend browser integration.*DATABASE_URL/i);
  assert.doesNotMatch(result.stdout, /\[run\]/);
});

test('unknown CLI arguments fail clearly instead of running a suite', () => {
  const result = spawnSync(process.execPath, [runnerPath, '--typo'], { encoding: 'utf8' });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /usage/i);
});
