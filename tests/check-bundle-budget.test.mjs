import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { checkBundleBudget } from '../scripts/check-bundle-budget.mjs'

function manifest(files) {
  const path = join(mkdtempSync(join(tmpdir(), 'budget-')), 'manifest.json')
  writeFileSync(path, JSON.stringify({ files }))
  return path
}

test('accepts chunks inside the initial and async limits', () => {
  const violations = checkBundleBudget(manifest([
    { file: 'index.js', bytes: 1000, initial: true },
    { file: 'page.js', bytes: 2000, initial: false },
  ]), { initialBytes: 1500, chunkBytes: 2500 })
  assert.deepEqual(violations, [])
})

test('names every chunk that exceeds its limit', () => {
  const violations = checkBundleBudget(manifest([
    { file: 'index.js', bytes: 2000, initial: true },
    { file: 'page.js', bytes: 3000, initial: false },
  ]), { initialBytes: 1500, chunkBytes: 2500 })
  assert.deepEqual(violations.map((item) => item.file), ['index.js', 'page.js'])
})
