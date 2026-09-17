import api from './client'

// ─── Candidates ───────────────────────────────────────────────────────────────

export async function getCandidates(params = {}) {
  const res = await api.get('/candidates', { params })
  return res.data
}

export async function getCandidate(id) {
  const res = await api.get(`/candidates/${id}`)
  return res.data
}

export async function getCandidateTimeline(id) {
  const res = await api.get(`/candidates/${id}/timeline`)
  return res.data
}

export async function getCandidatePipeline(jdId = null) {
  const params = {}
  if (jdId) params.jd_id = jdId
  const res = await api.get('/candidates/pipeline', { params })
  return res.data
}

export async function updateCandidateName(candidateId, name) {
  const response = await api.put(`/candidates/${candidateId}/name`, { name })
  return response.data
}

export async function getCandidateAuditLog(candidateId) {
  const response = await api.get(`/candidates/${candidateId}/audit-log`)
  return response.data
}

export async function getScreeningResult(resultId) {
  const response = await api.get(`/candidates/results/${resultId}`)
  return response.data
}

// ─── Candidate Notes ─────────────────────────────────────────────────────────

export async function getCandidateNotes(candidateId) {
  const res = await api.get(`/candidates/${candidateId}/notes`)
  return res.data
}

export async function addCandidateNote(candidateId, text) {
  const res = await api.post(`/candidates/${candidateId}/notes`, { text })
  return res.data
}

export async function deleteCandidateNote(candidateId, noteId) {
  const res = await api.delete(`/candidates/${candidateId}/notes/${noteId}`)
  return res.data
}

// ─── Email Generation ─────────────────────────────────────────────────────────

export async function generateEmail(candidateId, type) {
  const res = await api.post('/email/generate', { candidate_id: candidateId, type })
  return res.data
}

// ─── JD Parse Preview ───────────────────────────────────────────────────────

/**
 * Parse JD text and return skill classification preview (must-have, nice-to-have, etc.)
 * Used by SkillClassificationEditor before analysis.
 */
export async function parseJdPreview(jobDescription) {
  const formData = new FormData()
  formData.append('job_description', jobDescription)
  const res = await api.post('/jd/parse-preview', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data
}

/**
 * Parse JD file upload and return skill classification preview.
 * Same endpoint as parseJdPreview but accepts a File object.
 */
export async function parseJdPreviewFromFile(file) {
  const formData = new FormData()
  formData.append('job_file', file)
  const res = await api.post('/jd/parse-preview', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data
}

// ─── JD URL Extraction ────────────────────────────────────────────────────────

export async function extractJdFromUrl(url) {
  const res = await api.post('/jd/extract-url', { url })
  return res.data
}

// ─── Team Skill Profiles ──────────────────────────────────────────────────

export async function getTeamProfiles() {
  const response = await api.get('/team/profiles')
  return response.data
}

export async function createTeamProfile(data) {
  const response = await api.post('/team/profiles', data)
  return response.data
}

export async function updateTeamProfile(profileId, data) {
  const response = await api.put(`/team/profiles/${profileId}`, data)
  return response.data
}

export async function deleteTeamProfile(profileId) {
  const response = await api.delete(`/team/profiles/${profileId}`)
  return response.data
}

// ─── Team (Members) ────────────────────────────────────────────────────────

export async function getTeamMembers() {
  const res = await api.get('/team')
  return res.data
}

export async function inviteTeamMember(email, role) {
  const res = await api.post('/invites', { email, role })
  return res.data
}

export async function addComment(resultId, text) {
  const res = await api.post(`/results/${resultId}/comments`, { text })
  return res.data
}

export async function updateResultStatus(resultId, status) {
  const res = await api.put(`/results/${resultId}/status`, { status })
  return res.data
}

// ─── Training ────────────────────────────────────────────────────────────────

export async function labelTrainingExample(resultId, outcome, feedback = '') {
  const res = await api.post('/training/label', { screening_result_id: resultId, outcome, feedback })
  return res.data
}

export async function startTraining() {
  const res = await api.post('/training/train')
  return res.data
}

export async function getTrainingStatus() {
  const res = await api.get('/training/status')
  return res.data
}

// ─── Video Analysis ──────────────────────────────────────────────────────────

export async function analyzeVideo(videoFile, candidateId = null, onUploadProgress = null) {
  const formData = new FormData()
  formData.append('video', videoFile)
  if (candidateId) formData.append('candidate_id', candidateId)
  const res = await api.post('/analyze/video', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
    onUploadProgress: onUploadProgress
      ? (e) => {
          if (e.total) onUploadProgress(Math.round((e.loaded / e.total) * 100))
        }
      : undefined,
  })
  return res.data
}

export async function analyzeVideoFromUrl(url, candidateId = null) {
  const res = await api.post('/analyze/video-url', { url, candidate_id: candidateId }, {
    timeout: 600000,  // 10 min — download + analysis
  })
  return res.data
}

// ─── Transcript Analysis ─────────────────────────────────────────────────────

export async function analyzeTranscript(
  transcriptFile,
  transcriptText,
  candidateId,
  roleTemplateId,
  sourcePlatform,
  requisitionId = null,
) {
  const formData = new FormData()
  if (transcriptFile) {
    formData.append('transcript_file', transcriptFile)
  } else if (transcriptText) {
    formData.append('transcript_text', transcriptText)
  }
  if (candidateId) formData.append('candidate_id', candidateId)
  if (requisitionId) {
    formData.append('requisition_id', requisitionId)
  } else if (roleTemplateId) {
    formData.append('role_template_id', roleTemplateId)
  }
  if (sourcePlatform) formData.append('source_platform', sourcePlatform)

  const res = await api.post('/transcript/analyze', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return res.data
}

// ─── Queue Management & Monitoring ────────────────────────────────────────────

/**
 * Get queue statistics (job counts by status, avg processing time, etc.)
 */
export async function getQueueStats() {
  const response = await api.get('/queue/stats')
  return response.data
}

/**
 * List jobs with optional filters
 */
export async function listJobs(status = null, limit = 50, offset = 0) {
  const params = { limit, offset }
  if (status) params.status = status
  const response = await api.get('/queue/jobs', { params })
  return response.data
}

/**
 * Retry a failed job
 */
export async function retryJob(jobId) {
  const response = await api.post(`/queue/retry/${jobId}`)
  return response.data
}

/**
 * Cancel a queued or processing job
 */
export async function cancelJob(jobId) {
  const response = await api.delete(`/queue/cancel/${jobId}`)
  return response.data
}

/**
 * Get performance metrics
 */
export async function getQueueMetrics() {
  const response = await api.get('/queue/metrics/performance')
  return response.data
}

