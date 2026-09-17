import api from './client'

// ─── Interview Evaluation & Scorecard ─────────────────────────────────────────

export async function getEvaluations(resultId) {
  const res = await api.get(`/results/${resultId}/evaluations`)
  return res.data
}

export async function saveEvaluation(resultId, { question_category, question_index, rating, notes }) {
  const res = await api.put(`/results/${resultId}/evaluations`, {
    question_category,
    question_index,
    rating: rating || null,
    notes: notes || null,
  })
  return res.data
}

export async function saveOverallAssessment(resultId, { overall_assessment, recruiter_recommendation }) {
  const res = await api.put(`/results/${resultId}/evaluations/overall`, {
    overall_assessment,
    recruiter_recommendation: recruiter_recommendation || null,
  })
  return res.data
}

export async function getScorecard(resultId) {
  const res = await api.get(`/results/${resultId}/scorecard`)
  return res.data
}

export async function generateDebrief(resultId, conversationSummary, recommendation) {
  const resp = await api.post(`/results/${resultId}/generate-debrief`, {
    conversation_summary: conversationSummary,
    recommendation,
  })
  return resp.data
}

export async function submitScoreFeedback(resultId, sentiment) {
  const resp = await api.post(`/results/${resultId}/score-feedback`, { sentiment })
  return resp.data
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export async function seedSampleData() {
  const response = await api.post('/onboarding/seed-sample')
  return response.data
}

export async function getOnboardingStatus() {
  const response = await api.get('/onboarding/status')
  return response.data
}

export async function updateOrganization(data) {
  const response = await api.post('/onboarding/organization', data)
  return response.data
}

export async function selectOnboardingPlan(planId) {
  const response = await api.post('/onboarding/select-plan', { plan_id: planId })
  return response.data
}

export async function completeOnboarding() {
  const response = await api.post('/onboarding/complete')
  return response.data
}

export async function skipOnboarding() {
  const response = await api.post('/onboarding/skip')
  return response.data
}

export async function inviteTeamDuringOnboarding(emails, role = 'recruiter') {
  const response = await api.post('/onboarding/invite-team', { emails, role })
  return response.data
}

export async function getOnboardingChecklist() {
  const response = await api.get('/onboarding/checklist')
  return response.data
}

export async function updateOnboardingChecklistItem(key, completed = true) {
  const response = await api.patch('/onboarding/checklist', { key, completed })
  return response.data
}

export async function dismissOnboardingChecklist() {
  const response = await api.post('/onboarding/checklist/dismiss')
  return response.data
}

export async function recordOnboardingEvent(event, properties = {}) {
  const response = await api.post('/onboarding/events', { event, properties })
  return response.data
}

export async function getUserPreferences() {
  const response = await api.get('/users/me/preferences')
  return response.data
}

export async function patchUserPreferences(patch) {
  const response = await api.patch('/users/me/preferences', patch)
  return response.data
}

export async function markModalSeen(modalId) {
  const response = await api.post('/users/me/preferences/seen-modal', { modal_id: modalId })
  return response.data
}

export async function getOAuthProviders() {
  const response = await api.get('/auth/oauth/providers')
  return response.data
}

export async function getCrmHealthOverview() {
  const response = await api.get('/admin/crm/health-overview')
  return response.data
}

export async function getTenantCrmHealth(tenantId) {
  const response = await api.get(`/admin/crm/tenants/${tenantId}/health`)
  return response.data
}

export async function getTenantCrmNotes(tenantId) {
  const response = await api.get(`/admin/crm/tenants/${tenantId}/notes`)
  return response.data
}

export async function addTenantCrmNote(tenantId, body, noteType = 'general') {
  const response = await api.post(`/admin/crm/tenants/${tenantId}/notes`, { body, note_type: noteType })
  return response.data
}

export async function getTenantCrmNps(tenantId) {
  const response = await api.get(`/admin/crm/tenants/${tenantId}/nps`)
  return response.data
}

export async function getTenantBranding() {
  const response = await api.get('/branding/me')
  return response.data
}

export async function updateTenantBranding(data) {
  const response = await api.put('/branding/me', data)
  return response.data
}

export async function submitNps(score, comment) {
  const response = await api.post('/nps', { score, comment })
  return response.data
}

export async function getDashboardSummary() {
  const res = await api.get('/dashboard/summary')
  return res.data
}

export async function getDashboardActivity() {
  const res = await api.get('/dashboard/activity')
  return res.data
}

// ─── JD Skill Tags ──────────────────────────────────────────────────────────

export async function getJDSkillTags(jdId) {
  const res = await api.get(`/jd/${jdId}/skill-tags`)
  return res.data
}

// ─── JD Candidates & Shortlisting ──────────────────────────────────────────

export async function getJDCandidates(jdId, { sortBy = 'fit_score', sortOrder = 'desc', status = '' } = {}) {
  const params = { sort_by: sortBy, sort_order: sortOrder }
  if (status) params.status = status
  const res = await api.get(`/jd/${jdId}/candidates`, { params })
  return res.data
}

export async function bulkUpdateStatus(jdId, resultIds, status) {
  const res = await api.post(`/jd/${jdId}/shortlist`, { result_ids: resultIds, status })
  return res.data
}

export async function getAllJDStats() {
  const res = await api.get('/jd/stats/batch')
  return res.data
}

// ─── Skill Trends Analytics ─────────────────────────────────────────────────

export async function getSkillTrends(params) {
  const res = await api.get('/analytics/skill-trends', { params })
  return res.data
}

export async function computeSkillTrends() {
  const res = await api.post('/analytics/skill-trends/compute')
  return res.data
}

// ─── Screening Analytics ────────────────────────────────────────────────────

export async function getScreeningAnalytics(params = {}) {
  const response = await api.get('/analytics/screening', { params })
  return response.data
}

export async function getAnalyticsHub(params = {}) {
  const response = await api.get('/analytics/hub', { params })
  return response.data
}

export async function getReportTemplates() {
  const response = await api.get('/analytics/reports/templates')
  return response.data
}

export async function runAnalyticsReport(body) {
  const response = await api.post('/analytics/reports/run', body)
  return response.data
}

export async function getBiExportManifest() {
  const response = await api.get('/analytics/reports/bi-manifest')
  return response.data
}

export async function getAnalyticsOverview(params = {}) {
  const response = await api.get('/analytics/overview', { params })
  return response.data
}

export async function getAnalyticsMetrics() {
  const response = await api.get('/analytics/metrics')
  return response.data
}

export async function listAnalyticsViews() {
  const response = await api.get('/analytics/views')
  return response.data
}

export async function createAnalyticsView(body) {
  const response = await api.post('/analytics/views', body)
  return response.data
}

export async function updateAnalyticsView(viewId, body) {
  const response = await api.put(`/analytics/views/${viewId}`, body)
  return response.data
}

export async function deleteAnalyticsView(viewId) {
  const response = await api.delete(`/analytics/views/${viewId}`)
  return response.data
}

export async function getReportFieldCatalog() {
  const response = await api.get('/analytics/reports/fields')
  return response.data
}

export async function runCustomReport(body) {
  const response = await api.post('/analytics/reports/custom/run', body)
  return response.data
}

export async function listSavedReports() {
  const response = await api.get('/analytics/reports/saved')
  return response.data
}

export async function createSavedReport(body) {
  const response = await api.post('/analytics/reports/saved', body)
  return response.data
}

export async function updateSavedReport(reportId, body) {
  const response = await api.put(`/analytics/reports/saved/${reportId}`, body)
  return response.data
}

export async function deleteSavedReport(reportId) {
  const response = await api.delete(`/analytics/reports/saved/${reportId}`)
  return response.data
}

export async function shareSavedReport(reportId) {
  const response = await api.post(`/analytics/reports/saved/${reportId}/share`)
  return response.data
}

export async function unshareSavedReport(reportId) {
  const response = await api.delete(`/analytics/reports/saved/${reportId}/share`)
  return response.data
}

export async function listScheduledReports() {
  const response = await api.get('/analytics/reports/scheduled')
  return response.data
}

export async function createScheduledReport(body) {
  const response = await api.post('/analytics/reports/scheduled', body)
  return response.data
}

export async function updateScheduledReport(scheduleId, body) {
  const response = await api.put(`/analytics/reports/scheduled/${scheduleId}`, body)
  return response.data
}

export async function deleteScheduledReport(scheduleId) {
  const response = await api.delete(`/analytics/reports/scheduled/${scheduleId}`)
  return response.data
}

// ─── HM Handoff Package ──────────────────────────────────────────────────────

export async function getHandoffPackage(reqId) {
  const response = await api.get(`/requisitions/${reqId}/handoff-package`)
  return response.data
}

export async function createHandoffShareLink(reqId, options = {}) {
  const response = await api.post(`/requisitions/${reqId}/share-links`, options)
  return response.data
}

export async function listHandoffShareLinks(reqId) {
  const response = await api.get(`/requisitions/${reqId}/share-links`)
  return response.data
}

export async function revokeHandoffShareLink(reqId, linkId) {
  const response = await api.delete(`/requisitions/${reqId}/share-links/${linkId}`)
  return response.data
}

export async function syncATSRequisitions(connectionId) {
  const response = await api.post(`/ats/connections/${connectionId}/sync-requisitions`)
  return response.data
}

export async function addCandidatesToRequisition(reqId, candidateIds, screeningResultIds = null) {
  const response = await api.post(`/requisitions/${reqId}/candidates`, {
    candidate_ids: candidateIds,
    screening_result_ids: screeningResultIds,
  })
  return response.data
}

export async function getPublicHandoff(token, options = {}) {
  const headers = {}
  if (options?.passcode) {
    headers['X-Handoff-Passcode'] = options.passcode
  }
  const response = await api.get(`/public/handoff/${token}`, { headers })
  return response.data
}

export async function getTenantAuditLogs(params = {}) {
  const response = await api.get('/audit-logs', { params })
  return response.data
}

// ─── Notification Admin API ──────────────────────────────────────

export async function getNotificationConfig() {
  const response = await api.get('/admin/notifications/config')
  return response.data
}

export async function sendTestEmail(email) {
  const response = await api.post('/admin/notifications/test', { email })
  return response.data
}

// ─── Tenant Email Configuration ───────────────────────────────────────────────

export async function getEmailConfig() {
  const response = await api.get('/admin/email-config')
  return response.data
}

export async function saveEmailConfig(data) {
  const response = await api.post('/admin/email-config', data)
  return response.data
}

export async function testEmailConfig() {
  const response = await api.post('/admin/email-config/test')
  return response.data
}

export async function deleteEmailConfig() {
  const response = await api.delete('/admin/email-config')
  return response.data
}

// ─── Enterprise Platform Admin API (Phase 1-4) ────────────────────────────────

export async function getSecurityEvents(params = {}) {
  const response = await api.get('/admin/security-events', { params })
  return response.data
}

export async function impersonateUser(userId, { ticket, mfaCode } = {}) {
  const headers = {}
  if (ticket) headers['X-Support-Ticket'] = ticket
  if (mfaCode) headers['X-MFA-Code'] = mfaCode
  const response = await api.post(`/admin/impersonate/${userId}`, {}, { headers })
  return response.data
}

export async function listImpersonationSessions() {
  const response = await api.get('/admin/impersonate/sessions')
  return response.data
}

export async function revokeImpersonationSession(sessionId) {
  const response = await api.delete(`/admin/impersonate/sessions/${sessionId}`)
  return response.data
}

export async function requestErasure(tenantId) {
  const response = await api.post(`/admin/tenants/${tenantId}/anonymize`, { confirm: true })
  return response.data
}

export async function getErasureLogs(tenantId) {
  const response = await api.get(`/admin/tenants/${tenantId}/anonymize`)
  return response.data
}

export async function getAdminPlanFeatures(planId) {
  const response = await api.get(`/admin/plans/${planId}/features`)
  return response.data
}

// ─── Rate Limit Admin API ──────────────────────────────────────────

export async function getAdminRateLimits(params = {}) {
  const response = await api.get('/admin/rate-limits', { params })
  return response.data
}

export async function getTenantRateLimit(tenantId) {
  // @future-use — imported in AdminDashboardPage but not yet wired to UI
  const response = await api.get(`/admin/tenants/${tenantId}/rate-limit`)
  return response.data
}

export async function updateTenantRateLimit(tenantId, data) {
  const response = await api.put(`/admin/tenants/${tenantId}/rate-limit`, data)
  return response.data
}

export async function deleteTenantRateLimit(tenantId) {
  const response = await api.delete(`/admin/tenants/${tenantId}/rate-limit`)
  return response.data
}

// ─── Tenant CRUD Admin API ──────────────────────────────────────────

export async function createTenant(data) {
  const response = await api.post('/admin/tenants', data)
  return response.data
}

export async function updateTenant(tenantId, data) {
  const response = await api.put(`/admin/tenants/${tenantId}`, data)
  return response.data
}

// @future-use — imported in AdminDashboardPage but not yet wired to UI
export async function deleteTenant(tenantId) {
  const response = await api.delete('/admin/tenants/' + tenantId, { params: { confirm: true } })
  return response.data
}

// ─── SSO Configuration ───────────────────────────────────────────────────────

export async function getSSOConfig(tenantSlug) {
  const response = await api.get(`/sso/config/${tenantSlug}`)
  return response.data
}

export async function getTenantSSO(tenantId) {
  const response = await api.get(`/admin/tenants/${tenantId}/sso`)
  return response.data
}

export async function updateTenantSSO(tenantId, data) {
  const response = await api.put(`/admin/tenants/${tenantId}/sso`, data)
  return response.data
}

export async function deleteTenantSSO(tenantId) {
  const response = await api.delete(`/admin/tenants/${tenantId}/sso`)
  return response.data
}

export async function testTenantSSO(tenantId) {
  const response = await api.post(`/admin/tenants/${tenantId}/sso/test`)
  return response.data
}

// ─── Tenant User Management API ─────────────────────────────────────

// @future-use — imported in AdminDashboardPage but not yet wired to UI
export async function addUserToTenant(tenantId, data) {
  const response = await api.post(`/admin/tenants/${tenantId}/users`, data)
  return response.data
}

// @future-use — imported in AdminDashboardPage but not yet wired to UI
export async function removeUserFromTenant(tenantId, userId) {
  const response = await api.delete(`/admin/tenants/${tenantId}/users/${userId}`)
  return response.data
}

export async function setTenantUserStatus(tenantId, userId, isActive) {
  const response = await api.patch(`/admin/tenants/${tenantId}/users/${userId}/status`, {
    is_active: isActive,
  })
  return response.data
}

export async function adminResetUserPassword(tenantId, userId) {
  const response = await api.post(`/admin/tenants/${tenantId}/users/${userId}/reset-password`)
  return response.data
}

export async function adminInviteUser(tenantId, userId) {
  const response = await api.post(`/admin/tenants/${tenantId}/users/${userId}/invite`)
  return response.data
}

export async function getAdminUserActivity(tenantId, userId) {
  const response = await api.get(`/admin/tenants/${tenantId}/users/${userId}/activity`)
  return response.data
}

export async function adminResetTenantApiKeys(tenantId) {
  const response = await api.post(`/admin/tenants/${tenantId}/reset-api-keys`)
  return response.data
}

export async function adminExportTenant(tenantId) {
  const response = await api.get(`/admin/tenants/${tenantId}/export`)
  return response.data
}

export async function adminNotifyTenant(tenantId, message, title) {
  const response = await api.post(`/admin/tenants/${tenantId}/notify`, { message, title })
  return response.data
}

export async function updatePlanFeature(planId, featureFlagId, enabled) {
  const response = await api.put(`/admin/plans/${planId}/features/${featureFlagId}`, { enabled })
  return response.data
}

export async function deletePlanFeature(planId, featureFlagId) {
  const response = await api.delete(`/admin/plans/${planId}/features/${featureFlagId}`)
  return response.data
}

// ─── Outcome Feedback API ─────────────────────────────────────────────────────

export async function recordOutcome(candidateId, data) {
  const res = await api.post(`/candidates/${candidateId}/outcome`, data)
  return res.data
}

export async function recordOutcomeFeedback(outcomeId, data) {
  const res = await api.post(`/candidates/outcomes/${outcomeId}/feedback`, data)
  return res.data
}

// ─── Voice Screening API ─────────────────────────────────────────────────────

export async function getVoiceSettings() {
  const res = await api.get('/voice/settings')
  return res.data
}

export async function updateVoiceSettings(data) {
  const res = await api.put('/voice/settings', data)
  return res.data
}

export async function suggestInterviewOpening(data = {}) {
  const res = await api.post('/voice/settings/suggest-opening', data)
  return res.data
}

export async function scheduleVoiceCall(candidateId, phoneNumber, jdId = null, scheduledAt = null) {
  const res = await api.post('/voice/schedule', {
    candidate_id: candidateId,
    phone_number: phoneNumber,
    jd_id: jdId,
    scheduled_at: scheduledAt,
  })
  return res.data
}

export async function getVoiceSessions(params = {}) {
  const res = await api.get('/voice/sessions', { params })
  return res.data
}

export async function getVoiceSession(sessionId) {
  const res = await api.get(`/voice/sessions/${sessionId}`)
  return res.data
}

export async function updateVoiceSession(sessionId, data) {
  const res = await api.patch(`/voice/sessions/${sessionId}`, data)
  return res.data
}

export async function rescheduleVoiceCall(sessionId, data) {
  const res = await api.post(`/voice/sessions/${sessionId}/reschedule`, data)
  return res.data
}

export async function cancelVoiceSession(sessionId) {
  const res = await api.post(`/voice/sessions/${sessionId}/cancel`)
  return res.data
}

export async function getVoiceAnalytics() {
  const res = await api.get('/voice/sessions/analytics')
  return res.data
}

export async function bulkCancelVoiceSessions(sessionIds) {
  const res = await api.post('/voice/sessions/bulk-cancel', { session_ids: sessionIds })
  return res.data
}

export async function exportVoiceSessions(params = {}) {
  const res = await api.get('/voice/sessions/export', { params, responseType: 'blob' })
  const url = window.URL.createObjectURL(new Blob([res.data]))
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `voice_sessions_${new Date().toISOString().slice(0, 10)}.csv`)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export async function getNextAvailableSlot() {
  const res = await api.get('/voice/next-slot')
  return res.data
}

export async function getOutcomePatterns(params) {
  const res = await api.get('/candidates/analytics/outcome-patterns', { params })
  return res.data
}

// ============ AI Recruiter ============

export async function getRecruiterSessions(params = {}) {
  const { data } = await api.get('/recruiter/sessions', { params });
  return data;
}

export async function getRecruiterSession(sessionId) {
  const { data } = await api.get(`/recruiter/sessions/${sessionId}`);
  return data;
}

export async function initiateRecruiterInterview(payload) {
  const { data } = await api.post('/recruiter/sessions', payload);
  return data;
}

export async function getRecruiterTranscript(sessionId) {
  const { data } = await api.get(`/recruiter/sessions/${sessionId}/transcript`);
  return data?.questions ?? [];
}

export async function getRecruiterScorecard(sessionId) {
  const { data } = await api.get(`/recruiter/sessions/${sessionId}/scorecard`);
  return data;
}

export async function cancelRecruiterSession(sessionId) {
  const { data } = await api.post(`/recruiter/sessions/${sessionId}/cancel`);
  return data;
}

export async function retryRecruiterSession(sessionId) {
  const { data } = await api.post(`/recruiter/sessions/${sessionId}/retry`);
  return data;
}

export async function getRecruiterConfig() {
  const { data } = await api.get('/recruiter/config');
  return data;
}

export async function updateRecruiterConfig(payload) {
  const { data } = await api.put('/recruiter/config', payload);
  return data;
}

export async function getCandidateRecruiterSessions(candidateId) {
  const { data } = await api.get(`/recruiter/candidates/${candidateId}/sessions`);
  return data;
}

export async function getRecruiterAnalytics() {
  const { data } = await api.get('/recruiter/analytics');
  return data;
}

export async function exportRecruiterSessions(params = {}) {
  const { data } = await api.post('/recruiter/sessions/export', null, { 
    params, responseType: 'blob' 
  });
  return data;
}

// ============ Unified AI Interview API ============
// These functions call the unified /api/interviews/* backend routes.
// They replace the legacy voice.py and recruiter.py endpoints.

export async function createInterviewSession(payload) {
  const { data } = await api.post('/interviews/sessions', payload)
  return data
}

export async function getInterviewSessions(params = {}) {
  const { data } = await api.get('/interviews/sessions', { params })
  return data
}

export async function getInterviewSession(sessionId) {
  const { data } = await api.get(`/interviews/sessions/${sessionId}`)
  return data
}

export async function getInterviewTranscript(sessionId) {
  const { data } = await api.get(`/interviews/sessions/${sessionId}/transcript`)
  return data
}

export async function getInterviewScorecard(sessionId) {
  const { data } = await api.get(`/interviews/sessions/${sessionId}/scorecard`)
  return data
}

export async function cancelInterviewSession(sessionId) {
  const { data } = await api.post(`/interviews/sessions/${sessionId}/cancel`)
  return data
}

export async function retryInterviewSession(sessionId) {
  const { data } = await api.post(`/interviews/sessions/${sessionId}/retry`)
  return data
}

export async function getInterviewConfigUnified() {
  const { data } = await api.get('/interviews/config')
  return data
}

export async function updateInterviewConfigUnified(payload) {
  const { data } = await api.put('/interviews/config', payload)
  return data
}

export async function getInterviewAnalytics() {
  const { data } = await api.get('/interviews/analytics')
  return data
}

export async function exportInterviewSessions(params = {}) {
  const { data } = await api.post('/interviews/sessions/export', null, {
    params,
    responseType: 'blob',
  })
  return data
}

export async function compareInterviewScores(jdId, candidateIds) {
  const { data } = await api.get('/interviews/compare', {
    params: { jd_id: jdId, candidate_ids: candidateIds.join(',') },
  })
  return data
}

// Legacy helpers retained for backward compatibility.
// Prefer the unified functions above for new code.

export async function getInterviewConfig() {
  const [voiceSettings, recruiterConfig] = await Promise.all([
    getVoiceSettings().catch(() => null),
    getRecruiterConfig().catch(() => null),
  ])
  return { voice: voiceSettings, recruiter: recruiterConfig }
}

export async function updateInterviewConfig({ voice, recruiter }) {
  const results = {}
  if (voice) results.voice = await updateVoiceSettings(voice)
  if (recruiter) results.recruiter = await updateRecruiterConfig(recruiter)
  return results
}

