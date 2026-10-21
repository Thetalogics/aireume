import assert from 'node:assert/strict'
import test from 'node:test'
import { routes, unclassified } from '../e2e/ui-audit/routes.mjs'

test('every App.jsx route has one audit role', () => {
  assert.deepEqual(unclassified, [])
  assert.equal(new Set(routes.map((route) => route.path)).size, routes.length)
  assert.equal(routes.find((route) => route.path === '/login').role, 'anonymous')
  assert.equal(routes.find((route) => route.path === '/admin').role, 'platform-admin')
  assert.equal(routes.find((route) => route.path === '*').role, 'any')
  assert.ok(routes.length >= 60)
})
