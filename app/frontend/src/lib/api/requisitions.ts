import api from './client'

// ─── Requisitions ─────────────────────────────────────────────────────────────

export async function listRequisitions(status = null, mineOnly = false) {
  const params = {}
  if (status) params.status = status
  if (mineOnly) params.mine_only = true
  const response = await api.get('/requisitions', { params })
  return response.data
}

export async function getRequisition(reqId) {
  const response = await api.get(`/requisitions/${reqId}`)
  return response.data
}

export async function createRequisition(data) {
  const response = await api.post('/requisitions', data)
  return response.data
}

export async function updateRequisition(reqId, data) {
  const response = await api.put(`/requisitions/${reqId}`, data)
  return response.data
}

export async function deleteRequisition(reqId) {
  await api.delete(`/requisitions/${reqId}`)
}

export async function suggestRequisitionIntake(reqId) {
  const response = await api.post(`/requisitions/${reqId}/intake/suggest`)
  return response.data
}

export async function updateRequisitionIntake(reqId, intakeJson, intakeStatus = null) {
  const response = await api.put(`/requisitions/${reqId}/intake`, {
    intake_json: intakeJson,
    intake_status: intakeStatus,
  })
  return response.data
}

export async function calibrateRequisition(reqId, criteriaJson = null) {
  const response = await api.post(`/requisitions/${reqId}/calibrate`, {
    criteria_json: criteriaJson,
    merge_jd_parse: true,
  })
  return response.data
}

export async function updateRequisitionCriteria(reqId, criteria) {
  const response = await api.put(`/requisitions/${reqId}/criteria`, criteria)
  return response.data
}

export async function hmApproveRequisition(reqId, approved, notes = null, intakeJson = null) {
  const response = await api.post(`/requisitions/${reqId}/hm-approval`, {
    approved,
    notes,
    intake_json: intakeJson,
  })
  return response.data
}

export async function getRequisitionPipeline(reqId) {
  const response = await api.get(`/requisitions/${reqId}/pipeline`)
  return response.data
}

export async function updateRequisitionCandidateStatus(reqId, candidateId, pipelineStatus) {
  const response = await api.put(`/requisitions/${reqId}/candidates/${candidateId}`, {
    pipeline_status: pipelineStatus,
  })
  return response.data
}

export async function submitCandidateToHm(reqId, candidateId, submissionJson = {}) {
  const response = await api.post(`/requisitions/${reqId}/candidates/${candidateId}/submit`, {
    submission_json: submissionJson,
  })
  return response.data
}

export async function recordHmOutcome(reqId, candidateId, outcome, reasonCode = null, notes = null) {
  const response = await api.put(`/requisitions/${reqId}/candidates/${candidateId}/outcome`, {
    hm_outcome: outcome,
    outcome_reason_code: reasonCode,
    outcome_notes: notes,
  })
  return response.data
}

export async function getRequisitionAnalytics(reqId) {
  const response = await api.get(`/requisitions/${reqId}/analytics`)
  return response.data
}

export async function getRequisitionSettings() {
  const response = await api.get('/requisitions/settings')
  return response.data
}

export async function updateRequisitionSettings(data) {
  const response = await api.put('/requisitions/settings', data)
  return response.data
}

export async function checkRequisitionIntakeGate(reqId) {
  const response = await api.get(`/requisitions/${reqId}/intake-gate`)
  return response.data
}

export async function requestRequisitionHm(reqId, { email, notes = null }) {
  const response = await api.post(`/requisitions/${reqId}/hm-request`, { email, notes })
  return response.data
}

export async function approveRequisitionHmRequest(reqId) {
  const response = await api.post(`/requisitions/${reqId}/hm-request/approve`)
  return response.data
}

export async function rejectRequisitionHmRequest(reqId, notes = null) {
  const response = await api.post(`/requisitions/${reqId}/hm-request/reject`, { notes })
  return response.data
}

export async function listPendingHmRequests() {
  const response = await api.get('/requisitions/hm-requests')
  return response.data
}

export async function assignRequisitionRecruiter(reqId, assignedRecruiterId) {
  const response = await api.put(`/requisitions/${reqId}/assign-recruiter`, {
    assigned_recruiter_id: assignedRecruiterId,
  })
  return response.data
}

export async function applyRequisitionFeedback(reqId, suggestions, recalibrate = false) {
  const response = await api.post(`/requisitions/${reqId}/apply-feedback`, {
    suggestions,
    recalibrate,
  })
  return response.data
}

export async function createRequisitionOpenRequest(payload) {
  const response = await api.post('/requisitions/open-requests', payload)
  return response.data
}

export async function listRequisitionOpenRequests() {
  const response = await api.get('/requisitions/open-requests')
  return response.data
}

export async function assignRequisitionOpenRequest(requestId, assignedRecruiterId, primaryHmId = null) {
  const response = await api.post(`/requisitions/open-requests/${requestId}/assign`, {
    assigned_recruiter_id: assignedRecruiterId,
    primary_hiring_manager_id: primaryHmId,
  })
  return response.data
}

export async function getOutcomeReasons() {
  const response = await api.get('/requisitions/outcome-reasons')
  return response.data
}

export async function getRequisitionCriteriaVersions(reqId) {
  const response = await api.get(`/requisitions/${reqId}/criteria-versions`)
  return response.data
}

// ─── Screening Projects (legacy — prefer requisitions) ─────────────────────────

export async function listProjects(status = null) {
  const params = status ? { status } : {}
  const response = await api.get('/projects', { params })
  return response.data
}

export async function getProject(projectId) {
  const response = await api.get(`/projects/${projectId}`)
  return response.data
}

export async function createProject(data) {
  const response = await api.post('/projects', data)
  return response.data
}

export async function updateProject(projectId, data) {
  const response = await api.put(`/projects/${projectId}`, data)
  return response.data
}

export async function deleteProject(projectId) {
  await api.delete(`/projects/${projectId}`)
}

export async function getProjectPipeline(projectId) {
  const response = await api.get(`/projects/${projectId}/pipeline`)
  return response.data
}

export async function addCandidatesToProject(projectId, candidateIds, screeningResultIds = null) {
  const response = await api.post(`/projects/${projectId}/candidates`, {
    candidate_ids: candidateIds,
    screening_result_ids: screeningResultIds,
  })
  return response.data
}

export async function updateProjectCandidateStatus(projectId, candidateId, status) {
  const response = await api.put(`/projects/${projectId}/candidates/${candidateId}`, { status })
  return response.data
}

export async function removeCandidateFromProject(projectId, candidateId) {
  await api.delete(`/projects/${projectId}/candidates/${candidateId}`)
}

// ─── Transcript Analysis ──────────────────────────────────────────────────────

export async function getTranscriptAnalyses() {
  const res = await api.get('/transcript/analyses')
  return res.data
}

export async function getTranscriptAnalysis(id) {
  const res = await api.get(`/transcript/analyses/${id}`)
  return res.data
}

// ─── Narrative Polling ────────────────────────────────────────────────────────

export async function getNarrative(analysisId) {
  const response = await api.get(`/analysis/${analysisId}/narrative`)
  return response.data
}

// ─── Rescore & Re-analyze ─────────────────────────────────────────────────────

export async function rescoreAnalysis(resultId, { required_skills, nice_to_have_skills }) {
  const response = await api.post(`/analyze/${resultId}/rescore`, {
    required_skills,
    nice_to_have_skills: nice_to_have_skills || [],
  })
  return response.data
}

export async function analyzeCandidateJd(candidateId, { job_description, requisition_id = null, scoring_weights = null }) {
  const response = await api.post(`/candidates/${candidateId}/analyze-jd`, {
    job_description,
    requisition_id,
    scoring_weights,
  })
  return response.data
}

export async function getTeamGapAnalysis(profileId) {
  const response = await api.get(`/team/profiles/${profileId}/gap-analysis`)
  return response.data
}

export async function verifyEmail(token) {
  const response = await api.post('/auth/verify-email', { token })
  return response.data
}

export async function changePassword(currentPassword, newPassword) {
  const response = await api.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
  return response.data
}

export async function setupMfa() {
  const response = await api.post('/auth/mfa/setup')
  return response.data
}

export async function enableMfa(code) {
  const response = await api.post('/auth/mfa/enable', { code })
  return response.data
}

export async function disableMfa(code) {
  const response = await api.post('/auth/mfa/disable', { code })
  return response.data
}

export async function resendVerificationEmail(email) {
  const response = await api.post('/auth/resend-verification', { email })
  return response.data
}

export async function createBillingCheckout(plan, successUrl, cancelUrl) {
  const response = await api.post('/billing/checkout', {
    plan,
    success_url: successUrl,
    cancel_url: cancelUrl,
  })
  return response.data
}

export async function downloadAdverseAction(resultId) {
  const response = await api.get(`/export/${resultId}/adverse-action`, { responseType: 'blob' })
  return response
}

export async function gdprExportCandidate(candidateId) {
  const response = await api.get(`/candidates/${candidateId}/gdpr-export`)
  return response.data
}

export async function gdprDeleteCandidate(candidateId, reason = 'gdpr_request') {
  const response = await api.delete(`/candidates/${candidateId}/gdpr-delete`, { params: { reason } })
  return response.data
}

