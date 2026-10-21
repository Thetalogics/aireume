import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const source = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../../app/frontend/src/App.jsx'),
  'utf8',
)
const paths = [...new Set([...source.matchAll(/path="([^"]+)"/g)].map((match) => match[1]))]

const anonymous = new Set([
  '/login', '/register', '/forgot-password', '/reset-password/:token',
  '/verify-email/:token', '/check-email', '/legal/:document', '/handoff/:token',
])
const redirects = new Set([
  '/jd-library', '/jd-library/:id', '/jd-library/:id/handoff', '/jd-library/:id/candidates',
  '/projects', '/projects/:id', '/interviews/comparison', '/batch', '/templates', '/dashboard-old',
])
const admin = new Set([
  '/admin', 'tenants/:id', 'tenants', 'plans', 'users', 'features', 'webhooks',
  'rate-limits', 'sso', 'email', 'metrics', 'audit', 'security', 'billing',
  'billing-settings', 'invoices', 'dunning', 'crm', 'erasure', 'impersonation',
])

export const routes = paths.map((path) => ({
  path,
  role: path === '*' ? 'any'
    : anonymous.has(path) ? 'anonymous'
    : redirects.has(path) ? 'redirect'
    : admin.has(path) ? 'platform-admin'
    : 'user',
  viewports: [1440, 768, 390],
}))

export const unclassified = routes.filter((route) => !route.role)
