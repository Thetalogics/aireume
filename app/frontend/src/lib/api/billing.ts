import api from './client'

// ─── Health ───────────────────────────────────────────────────────────────────

export async function checkHealth() {
  // Health endpoint is at /health (root level), not under /api/
  // Use fetch directly since the axios instance prepends /api
  const res = await fetch('/health', { credentials: 'include' })
  if (!res.ok) throw new Error('Health check failed')
  return res.json()
}

// ─── Subscription ───────────────────────────────────────────────────────────

export async function getSubscription() {
  const response = await api.get('/subscription')
  return response.data
}

export async function getAvailablePlans() {
  const response = await api.get('/subscription/plans')
  return response.data
}

export async function checkUsage(action, quantity = 1) {
  const response = await api.get(`/subscription/check/${action}?quantity=${quantity}`)
  return response.data
}

export async function getUsageHistory(limit = 100) {
  const response = await api.get(`/subscription/usage-history?limit=${limit}`)
  return response.data
}

// ─── Billing (Invoices) ─────────────────────────────────────────────────────

export async function getInvoices(limit = 50, offset = 0) {
  const response = await api.get(`/billing/invoices?limit=${limit}&offset=${offset}`)
  return response.data
}

export async function getInvoice(invoiceId) {
  const response = await api.get(`/billing/invoices/${invoiceId}`)
  return response.data
}

// ─── Admin (for testing/plan management) ──────────────────────────────────────

export async function adminResetUsage() {
  const response = await api.post('/subscription/admin/reset-usage')
  return response.data
}

export async function adminChangePlan(planId) {
  const response = await api.post(`/subscription/admin/change-plan/${planId}`)
  return response.data
}

// ─── User-friendly Error Messages ─────────────────────────────────────────────

export function getUserFriendlyError(error) {
  if (!error.response) {
    return "Network error. Please check your connection and try again."
  }
  const status = error.response.status
  const detail = error.response.data?.detail

  const errorMap = {
    400: detail || "Invalid request. Please check your input.",
    401: "Session expired. Please log in again.",
    403: detail === "CSRF token missing or invalid"
      ? "Session expired, please refresh the page."
      : "You don't have permission for this action.",
    404: "The requested resource was not found.",
    413: "File is too large. Please upload a smaller file.",
    429: detail || "Too many requests. Please wait a moment.",
    500: "Server error. Our team has been notified.",
    502: "Service temporarily unavailable. Please try again.",
    503: "Service is under maintenance. Please try again later.",
  }

  return errorMap[status] || detail || "An unexpected error occurred."
}

// ─── Platform Admin API ─────────────────────────────────────

/**
 * Safely extract a human-readable error message from an API error response.
 * FastAPI validation errors return `detail` as an array of objects,
 * which would crash React if rendered directly as a child.
 */
export function extractApiError(err, fallback = 'An error occurred') {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map(e => e.msg || e.message || String(e)).join('; ')
  }
  if (detail) return String(detail)
  return fallback
}

export async function getAdminTenants(params = {}) {
  const response = await api.get('/admin/tenants', { params })
  return response.data
}

export async function getAdminTenantDetail(tenantId) {
  const response = await api.get(`/admin/tenants/${tenantId}`)
  return response.data
}

export async function suspendTenant(tenantId, reason) {
  const response = await api.post(`/admin/tenants/${tenantId}/suspend`, { reason })
  return response.data
}

export async function reactivateTenant(tenantId) {
  const response = await api.post(`/admin/tenants/${tenantId}/reactivate`)
  return response.data
}

export async function adminChangeTenantPlan(tenantId, planId) {
  const response = await api.post(`/admin/tenants/${tenantId}/change-plan`, { plan_id: planId })
  return response.data
}

export async function adminAdjustUsage(tenantId, data) {
  const response = await api.post(`/admin/tenants/${tenantId}/adjust-usage`, data)
  return response.data
}

export async function getAdminTenantUsageHistory(tenantId, limit = 100) {
  const response = await api.get(`/admin/tenants/${tenantId}/usage-history`, { params: { limit } })
  return response.data
}

export async function getAdminAuditLogs(params = {}) {
  const response = await api.get('/admin/audit-logs', { params })
  return response.data
}

export async function exportAuditLogs(params = {}) {
  const response = await api.get('/admin/audit-logs/export', {
    params,
    responseType: 'blob',
  })
  return response.data
}

export async function getWebhookEvents() {
  const response = await api.get('/webhooks/events')
  return response.data
}

// ─── Platform Admin API — Phase 2 ──────────────────────────

export async function getAdminFeatureFlags() {
  const response = await api.get('/admin/feature-flags')
  return response.data
}

export async function toggleFeatureFlag(flagId, enabledGlobally) {
  const response = await api.put(`/admin/feature-flags/${flagId}`, { enabled_globally: enabledGlobally })
  return response.data
}

export async function getTenantFeatureOverrides(tenantId) {
  const response = await api.get(`/admin/tenants/${tenantId}/features`)
  return response.data
}

export async function setTenantFeatureOverride(tenantId, flagId, enabled) {
  const response = await api.put(`/admin/tenants/${tenantId}/features/${flagId}`, { enabled })
  return response.data
}

export async function deleteTenantFeatureOverride(tenantId, flagId) {
  const response = await api.delete(`/admin/tenants/${tenantId}/features/${flagId}`)
  return response.data
}

export async function getTenantWebhooks(tenantId) {
  const response = await api.get(`/admin/tenants/${tenantId}/webhooks`)
  return response.data
}

export async function createTenantWebhook(tenantId, data) {
  const response = await api.post(`/admin/tenants/${tenantId}/webhooks`, data)
  return response.data
}

export async function deleteTenantWebhook(tenantId, webhookId) {
  const response = await api.delete(`/admin/tenants/${tenantId}/webhooks/${webhookId}`)
  return response.data
}

export async function getWebhookDeliveries(tenantId, webhookId, limit = 50) {
  const response = await api.get(`/admin/tenants/${tenantId}/webhooks/${webhookId}/deliveries`, { params: { limit } })
  return response.data
}

export async function getAdminMetricsOverview() {
  const response = await api.get('/admin/metrics/overview')
  return response.data
}

export async function getAdminUsageTrends(days = 30) {
  const response = await api.get('/admin/metrics/usage-trends', { params: { days } })
  return response.data
}

// ─── Plan Management Admin API ──────────────────────────────────────────

export async function getAdminPlans() {
  const response = await api.get('/admin/plans')
  return response.data
}

export async function createPlan(data) {
  const response = await api.post('/admin/plans', data)
  return response.data
}

export async function updatePlan(planId, data) {
  const response = await api.put(`/admin/plans/${planId}`, data)
  return response.data
}

export async function archivePlan(planId, force = false) {
  const response = await api.delete(`/admin/plans/${planId}`, { params: { force } })
  return response.data
}

// ─── Billing Admin API ──────────────────────────────────────────

export async function getAdminInvoices(params = {}) {
  const response = await api.get('/admin/invoices', { params })
  return response.data
}

export async function getAdminDunningRecords(params = {}) {
  const response = await api.get('/admin/dunning', { params })
  return response.data
}

export async function resolveDunning(tenantId) {
  const response = await api.post(`/admin/dunning/${tenantId}/resolve`)
  return response.data
}

export async function getBillingConfig() {
  const response = await api.get('/admin/billing/config')
  return response.data
}

export async function updateBillingConfig(data) {
  const response = await api.put('/admin/billing/config', data)
  return response.data
}

export async function getBillingProviders() {
  const response = await api.get('/admin/billing/providers')
  return response.data
}

export async function getBillingSettings() {
  const response = await api.get('/admin/billing/settings')
  return response.data
}

export async function updateBillingSettings(data) {
  const response = await api.put('/admin/billing/settings', data)
  return response.data
}

export async function testBillingConnection(provider) {
  const response = await api.post('/admin/billing/settings/test', { provider })
  return response.data
}

export async function generateCheckoutLink(tenantId, planId) {
  const response = await api.post('/admin/billing/generate-checkout-link', { tenant_id: tenantId, plan_id: planId })
  return response.data
}

