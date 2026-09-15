import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true,  // Send httpOnly cookies with every request
})

// Attach JWT token to every request (for backward compatibility with API clients)
// Browser clients will use httpOnly cookies automatically
api.interceptors.request.use((config) => {
  // Token storage is now handled via httpOnly cookies only
  // No localStorage token handling for security
  return config
})

// Shared CSRF token reader
function getCsrfToken() {
  return document.cookie
    .split('; ')
    .find(row => row.startsWith('csrf_token='))
    ?.split('=')[1] || null
}

// Paths that are CSRF-exempt on the backend (no CSRF token needed)
const CSRF_EXEMPT_PATHS = [
  '/auth/login',
  '/auth/register',
  '/auth/refresh',
  '/auth/logout',
  '/auth/forgot-password',
  '/auth/reset-password',
  '/auth/resend-verification',
  '/auth/verify-email',
  '/auth/test/verify-email',
  '/billing/webhook',
  '/sso/callback',
  '/auth/oauth',
]

function isCsrfExempt(url) {
  const path = url?.split('?')[0] || ''
  return CSRF_EXEMPT_PATHS.some(p => path === p || path.startsWith(p + '/'))
}

// Add CSRF token to state-mutating requests for browser clients
api.interceptors.request.use(async (config) => {
  const safeMethod = ['get', 'head', 'options'].includes(config.method?.toLowerCase())
  if (!safeMethod && !isCsrfExempt(config.url)) {
    let csrfToken = getCsrfToken()
    if (!csrfToken) {
      // Try to refresh CSRF cookie by calling /auth/me (sets new CSRF cookie)
      try {
        await axios.get(`${API_URL}/auth/me`, { withCredentials: true })
        csrfToken = getCsrfToken()
      } catch {
        // Ignore refresh failure; backend will reject with 403 if CSRF is required
      }
    }
    if (csrfToken) {
      config.headers['X-CSRF-Token'] = csrfToken
    } else {
      console.warn('[CSRF] Token missing for', config.method?.toUpperCase(), config.url)
    }
  }
  return config
})

// Auto-refresh on 401 - uses httpOnly cookies only
// Only skip refresh for endpoints that cannot benefit from a refresh attempt.
// /auth/me is intentionally NOT excluded — a 401 there means the access token
// expired but the refresh token may still be valid.
const NON_REFRESHABLE_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']
const PUBLIC_PATHS = ['/login', '/register']

let isRefreshing = false
let refreshFailedQueue = []
let lastSuccessfulAuthTime = 0

api.interceptors.response.use(
  (res) => {
    // Track last successful authenticated response
    if (res.status >= 200 && res.status < 300) {
      lastSuccessfulAuthTime = Date.now()
    }
    return res
  },
  async (error) => {
    const original = error.config
    const reqPath = original?.url || ''
    const isNonRefreshable = NON_REFRESHABLE_PATHS.some(p => reqPath.includes(p))

    if (error.response?.status === 401 && !original._retry && !isNonRefreshable) {
      original._retry = true

      // If a refresh is already in progress, queue this request
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          refreshFailedQueue.push({ resolve, reject })
        }).then(() => api(original)).catch((err) => Promise.reject(err))
      }

      try {
        isRefreshing = true
        // Refresh endpoint reads from cookie - browser sends cookie automatically
        await axios.post(`${API_URL}/auth/refresh`, {}, { withCredentials: true })
        // Refresh succeeded - drain queue and retry original request
        refreshFailedQueue.forEach(({ resolve }) => resolve())
        refreshFailedQueue = []
        return api(original)
      } catch (refreshError) {
        // Refresh failed - reject all queued requests
        refreshFailedQueue.forEach(({ reject }) => reject(refreshError))
        refreshFailedQueue = []
        // Only force logout if session is genuinely expired (no recent successful auth)
        const timeSinceLastAuth = Date.now() - lastSuccessfulAuthTime
        if (timeSinceLastAuth > 10000) {
          window.dispatchEvent(new CustomEvent('auth:logout', { detail: { reason: 'refresh_failed' } }))
        }
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false  // ALWAYS reset, even on success or failure
      }
    }
    if (error.response?.status === 403) {
      const detail = error.response?.data?.detail
      const errorCode = typeof detail === 'object' ? detail?.error_code : null
      if (errorCode === 'ROLE_FORBIDDEN') {
        window.dispatchEvent(new CustomEvent('rbac:forbidden', {
          detail: {
            message: typeof detail === 'object' ? detail.message : detail,
          },
        }))
      }
    }
    return Promise.reject(error)
  }
)

// Retry configuration for transient errors
const MAX_RETRIES = 3
const RETRY_DELAYS = [1000, 2000, 4000] // Exponential backoff

// Add retry interceptor (must be after 401 refresh interceptor)
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config

    // Only retry on 5xx errors and network errors (not 4xx)
    const isRetryable = !error.response || (error.response.status >= 500)

    // Don't retry POST requests that might not be idempotent (except specific ones)
    const isIdempotent = config.method === 'get' || config._isRetryable

    if (!isRetryable || !isIdempotent) {
      return Promise.reject(error)
    }

    config._retryCount = config._retryCount || 0
    if (config._retryCount >= MAX_RETRIES) {
      return Promise.reject(error)
    }

    config._retryCount++
    const delay = RETRY_DELAYS[config._retryCount - 1] || 4000

    await new Promise(resolve => setTimeout(resolve, delay))
    return api(config)
  }
)

// ─── Queue-Based Analysis (Async) ────────────────────────────────────────────

/**
 * Submit a resume analysis job to the queue (returns immediately with job_id)
 */
export async function submitAnalysisJob(
  file, jobDescription, jobFile = null, scoringWeights = null, priority = 5,
  templateId = null, skillOverrides = null, requisitionId = null,
) {
  const formData = new FormData()
  formData.append('resume_file', file)
  if (jobFile) {
    formData.append('jd_file', jobFile)
  } else {
    formData.append('jd_text', jobDescription)
  }
  formData.append('priority', priority.toString())
  appendAnalyzeContext(formData, { requisitionId, templateId, skillOverrides, scoringWeights })
  
  const response = await api.post('/queue/submit-file', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return response.data // { job_id, status, queued_at }
}

/**
 * Submit multiple resumes to the background queue (50+ file batches).
 */
export async function submitBatchToQueue(
  files,
  jobDescription,
  jdFile = null,
  scoringWeights = null,
  templateId = null,
  skillOverrides = null,
  priority = 8,
  requisitionId = null,
) {
  const formData = new FormData()
  files.forEach((file) => formData.append('resume_files', file))
  if (jdFile) {
    formData.append('jd_file', jdFile)
  } else {
    formData.append('jd_text', jobDescription)
  }
  formData.append('priority', priority.toString())
  appendAnalyzeContext(formData, { requisitionId, templateId, skillOverrides, scoringWeights })

  const response = await api.post('/queue/submit-batch', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return response.data
}

/**
 * Check the status of a queued analysis job
 */
export async function getJobStatus(jobId) {
  const response = await api.get(`/queue/status/${jobId}`)
  return response.data // { job_id, status, progress_percent, processing_stage, ... }
}

/**
 * Get the completed analysis result for a job
 */
export async function getJobResult(jobId) {
  const response = await api.get(`/queue/result/${jobId}`)
  return response.data // { analysis: {...}, metadata: {...} }
}

/**
 * Poll a job until completion (helper function)
 * @param {string} jobId - Job ID to poll
 * @param {function} onProgress - Callback for progress updates (status, progress_percent)
 * @param {number} pollInterval - Milliseconds between polls (default 2000)
 * @param {number} timeout - Max wait time in ms (default 120000 = 2 min)
 * @returns {Promise<object>} - Completed analysis result
 */
export async function pollJobUntilComplete(jobId, onProgress = null, pollInterval = 2000, timeout = 120000) {
  const startTime = Date.now()
  
  while (true) {
    if (Date.now() - startTime > timeout) {
      throw new Error('Job polling timeout - analysis taking too long')
    }
    
    const status = await getJobStatus(jobId)
    
    if (onProgress) {
      onProgress(status)
    }
    
    if (status.status === 'completed') {
      return await getJobResult(jobId)
    }
    
    if (status.status === 'failed') {
      throw new Error(status.error_message || 'Analysis failed')
    }
    
    if (status.status === 'cancelled') {
      throw new Error('Job was cancelled')
    }
    
    // Wait before next poll
    await new Promise(resolve => setTimeout(resolve, pollInterval))
  }
}

/**
 * Submit job and wait for completion (convenience wrapper)
 */
export async function analyzeResumeAsync(file, jobDescription, jobFile = null, scoringWeights = null, onProgress = null) {
  const { job_id } = await submitAnalysisJob(file, jobDescription, jobFile, scoringWeights)
  const result = await pollJobUntilComplete(job_id, onProgress)
  return result.analysis
}

// ─── Resume Analysis (Legacy Synchronous) ─────────────────────────────────────

export async function analyzeResume(
  file, jobDescription, jobFile = null, scoringWeights = null,
  templateId = null, skillOverrides = null, requisitionId = null,
) {
  const formData = new FormData()
  formData.append('resume', file)
  if (jobFile) {
    formData.append('job_file', jobFile)
  } else {
    formData.append('job_description', jobDescription)
  }
  appendAnalyzeContext(formData, { requisitionId, templateId, skillOverrides, scoringWeights })
  const response = await api.post('/analyze', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return response.data
}

/**
 * Streaming version of analyzeResume — uses /api/analyze/stream (SSE).
 *
 * @param {File} file  - Resume file
 * @param {string} jobDescription  - JD text (or empty if jobFile provided)
 * @param {File|null} jobFile  - JD file upload
 * @param {object|null} scoringWeights  - Custom weight map
 * @param {function} onStageComplete  - Called with ({stage, result}) after each node
 * @returns {Promise<object>}  - Resolves with the complete assembled result
 */
export async function analyzeResumeStream(
  file,
  jobDescription,
  jobFile = null,
  scoringWeights = null,
  onStageComplete = null,
  templateId = null,
  skillOverrides = null,
  requisitionId = null,
) {
  const formData = new FormData()
  formData.append('resume', file)
  if (jobFile) {
    formData.append('job_file', jobFile)
  } else {
    formData.append('job_description', jobDescription)
  }
  appendAnalyzeContext(formData, { requisitionId, templateId, skillOverrides, scoringWeights })

  const baseURL = import.meta.env.VITE_API_URL || '/api'

  // Get CSRF token from cookie for the fetch call
  const csrfToken = getCsrfToken()

  const headers = {}
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken
  }

  const postStream = () => fetch(`${baseURL}/analyze/stream`, {
    method: 'POST',
    headers,
    body: formData,
    credentials: 'include',  // Send httpOnly cookies
  })

  let response = await postStream()
  if (response.status === 401) {
    await axios.post(`${API_URL}/auth/refresh`, {}, { withCredentials: true })
    response = await postStream()
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const err = await response.json()
      detail = err.detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }

  const reader  = response.body.getReader()
  const decoder = new TextDecoder()
  let   buffer  = ''
  let   finalResult = null
  let   streamDone = false

  while (true) {
    let readResult
    try {
      readResult = await reader.read()
    } catch (networkErr) {
      // Connection dropped mid-stream (Wi-Fi loss, proxy timeout, tab sleep).
      // We can't resume a POST upload stream, so surface a clear, retryable
      // error instead of a cryptic "network error".
      if (finalResult) break // we already have a usable result
      const e = new Error('Connection lost during analysis. Please check your network and try again.')
      e.retryable = true
      throw e
    }
    const { done, value } = readResult
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // SSE lines are separated by \n\n
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''   // last fragment (possibly incomplete)

    let streamError = null

    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data: ')) continue
      const raw = line.slice(6).trim()
      if (raw === '[DONE]') {
        streamDone = true
        break
      }

      try {
        const event = JSON.parse(raw)
        console.log('[SSE] Received event:', event.stage, event)
        
        if (event.stage === 'complete') {
          finalResult = event.result
          console.log('[SSE] Final result captured:', finalResult?.fit_score)
        } else if (event.stage === 'parsing') {
          // Parsing stage also contains the result - use as fallback
          if (!finalResult && event.result) {
            console.log('[SSE] Storing parsing result as fallback')
            finalResult = event.result
          }
          if (onStageComplete) {
            onStageComplete(event)
          }
        } else if (event.stage === 'error') {
          const msg = event.result?.message || event.message || 'Analysis failed'
          if (!finalResult) {
            streamError = new Error(msg)
          } else {
            console.warn('[SSE] Non-fatal error after result received:', msg)
          }
        } else if (onStageComplete) {
          onStageComplete(event)
        }
      } catch (parseError) {
        console.error('[SSE] Failed to parse event:', raw, parseError)
        // Don't throw - continue processing other events
      }
    }

    // Propagate server-side errors only when we never received a usable result
    if (streamError && !finalResult) throw streamError
    
    if (streamDone) break
  }

  if (!finalResult) {
    console.error('[SSE] Stream ended without final result')
    const e = new Error('The analysis was interrupted before completing. Please try again.')
    e.retryable = true
    throw e
  }
  return finalResult
}

export async function analyzeBatch(
  files, jobDescription, jobFile = null, scoringWeights = null,
  templateId = null, requisitionId = null,
) {
  const formData = new FormData()
  files.forEach((f) => formData.append('resumes', f))
  if (jobFile) {
    formData.append('job_file', jobFile)
  } else {
    formData.append('job_description', jobDescription)
  }
  appendAnalyzeContext(formData, { requisitionId, templateId, scoringWeights })
  const response = await api.post('/analyze/batch', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
  return response.data
}

/**
 * Analyze batch of resumes using chunked upload for large files.
 * This bypasses CDN/proxy upload limits by splitting files into chunks.
 *
 * @param {File[]} files - Array of resume files to analyze
 * @param {string} jobDescription - Job description text
 * @param {File|null} jobFile - Optional job description file
 * @param {Object|null} scoringWeights - Optional custom scoring weights
 * @param {Object} callbacks - Progress callbacks
 * @param {Function} callbacks.onFileProgress - Called with (filename, progress) for each file
 * @param {Function} callbacks.onOverallProgress - Called with overall upload progress
 * @returns {Promise} Analysis results
 */
export async function analyzeBatchChunked(
  files, jobDescription, jobFile = null, scoringWeights = null, callbacks = {},
  templateId = null, requisitionId = null,
) {
  const { uploadMultipleFiles } = await import('./uploadChunked')

  // Upload all files using chunked upload
  const uploadResults = await uploadMultipleFiles(files, {
    onFileProgress: callbacks.onFileProgress || (() => {}),
    onOverallProgress: callbacks.onOverallProgress || (() => {}),
    onFileComplete: callbacks.onFileComplete || (() => {}),
    onFileError: callbacks.onFileError || (() => {}),
  })

  // Check if any uploads failed
  if (uploadResults.failed.length > 0) {
    throw new Error(`Failed to upload ${uploadResults.failed.length} file(s): ${uploadResults.failed.map(f => f.file).join(', ')}`)
  }

  // Now call a new backend endpoint that processes the assembled files
  const formData = new FormData()

  // Send upload IDs instead of files - backend will read from assembled directory
  uploadResults.successful.forEach(({ file: filename, result }) => {
    formData.append('upload_ids', result.upload_id)
    formData.append('filenames', filename)
  })

  if (jobFile) {
    formData.append('job_file', jobFile)
  } else {
    formData.append('job_description', jobDescription)
  }

  appendAnalyzeContext(formData, { requisitionId, templateId, scoringWeights })

  const response = await api.post('/analyze/batch-chunked', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000, // 10 minutes - longer than Cloudflare's 100s timeout
  })

  return response.data
}

/**
 * Batch analyze resumes with progressive SSE streaming.
 * Uploads files first (reusing uploadMultipleFiles), then opens SSE stream
 * to /api/analyze/batch-stream for progressive results.
 *
 * @param {File[]} files - Array of resume files to analyze
 * @param {string} jobDescription - Job description text
 * @param {File|null} jdFile - Optional job description file
 * @param {Object|null} scoringWeights - Optional custom scoring weights
 * @param {Object} callbacks - Callbacks for upload and streaming events
 * @param {Function} callbacks.onFileProgress - Called with (filename, progress) during upload
 * @param {Function} callbacks.onOverallProgress - Called with overall upload progress
 * @param {Function} callbacks.onFileComplete - Called when a file upload completes
 * @param {Function} callbacks.onFileError - Called when a file upload fails
 * @param {Function} callbacks.onProcessing - Called with (index, total, filename) when a file starts processing
 * @param {Function} callbacks.onResult - Called with (index, total, filename, result, screeningResultId) for each successful analysis
 * @param {Function} callbacks.onFailed - Called with (index, total, filename, error) for each failed analysis
 * @param {Function} callbacks.onDone - Called with (total, successful, failedCount) when complete
 * @returns {Promise<void>}
 */
export async function analyzeBatchStream(
  files, jobDescription, jdFile = null, scoringWeights = null, callbacks = {},
  templateId = null, skillOverrides = null, requisitionId = null,
) {
  const {
    onFileProgress, onOverallProgress, onFileComplete, onFileError,  // upload callbacks
    onProcessing, // (index, total, filename) => void
    onResult,    // (index, total, filename, result, screeningResultId) => void
    onFailed,    // (index, total, filename, error) => void
    onDone,      // (total, successful, failedCount) => void
  } = callbacks || {}

  const { uploadMultipleFiles } = await import('./uploadChunked')

  // Phase 1: Upload files (reuse existing)
  const uploadResults = await uploadMultipleFiles(files, {
    onFileProgress: onFileProgress || (() => {}),
    onOverallProgress: onOverallProgress || (() => {}),
    onFileComplete: onFileComplete || (() => {}),
    onFileError: onFileError || (() => {}),
  })

  // Report upload failures
  if (uploadResults.failed?.length) {
    uploadResults.failed.forEach(({ file: filename, error }) => {
      if (onFailed) onFailed(0, files.length, filename, `Upload failed: ${error}`)
    })
  }

  if (!uploadResults.successful.length) {
    throw new Error('No files uploaded successfully')
  }

  // Phase 2: Build FormData (same as analyzeBatchChunked)
  const formData = new FormData()
  uploadResults.successful.forEach(({ file: filename, result }) => {
    formData.append('upload_ids', result.upload_id)
    formData.append('filenames', filename)
  })
  if (jobDescription) formData.append('job_description', jobDescription)
  if (jdFile) formData.append('job_file', jdFile)
  appendAnalyzeContext(formData, { requisitionId, templateId, skillOverrides, scoringWeights })

  // Phase 3: Open SSE stream
  const baseURL = import.meta.env.VITE_API_URL || '/api'

  // Get CSRF token from cookie for the fetch call
  const csrfToken = getCsrfToken()

  const headers = {}
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken
  }

  const response = await fetch(`${baseURL}/analyze/batch-stream`, {
    method: 'POST',
    body: formData,
    headers,
    credentials: 'include',  // Send httpOnly cookies
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `Analysis failed: ${response.status}`)
  }

  // Phase 4: Parse SSE stream
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // SSE lines are separated by \n\n
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''   // last fragment (possibly incomplete)

    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data: ')) continue
      const data = line.slice(6).trim()

      if (data === '[DONE]') return

      try {
        const evt = JSON.parse(data)

        if (evt.event === 'processing' && onProcessing) {
          onProcessing(evt.index, evt.total, evt.filename)
        } else if (evt.event === 'result' && onResult) {
          onResult(evt.index, evt.total, evt.filename, evt.result, evt.screening_result_id)
        } else if (evt.event === 'failed' && onFailed) {
          onFailed(evt.index, evt.total, evt.filename, evt.error)
        } else if (evt.event === 'done' && onDone) {
          onDone(evt.total, evt.successful, evt.failed_count)
        }
      } catch (e) {
        console.warn('Failed to parse SSE event:', data, e)
      }
    }
  }
}

// ─── History ──────────────────────────────────────────────────────────────────

export async function getHistory() {
  const response = await api.get('/history')
  return response.data
}

// ─── Compare ─────────────────────────────────────────────────────────────────

export async function compareResults(ids) {
  const response = await api.post('/compare', { candidate_ids: ids })
  return response.data
}

export async function compareCandidates(data) {
  const res = await api.post('/candidates/compare', data)
  return res.data
}

// ─── Export ──────────────────────────────────────────────────────────────────

export async function exportCsv(ids = []) {
  const idsParam = ids.length ? `?ids=${ids.join(',')}` : ''
  const res = await api.get(`/export/csv${idsParam}`, { responseType: 'blob' })
  _triggerDownload(res.data, `aria_export_${_ts()}.csv`, 'text/csv')
}

export async function exportExcel(ids = []) {
  const idsParam = ids.length ? `?ids=${ids.join(',')}` : ''
  const res = await api.get(`/export/excel${idsParam}`, { responseType: 'blob' })
  _triggerDownload(res.data, `aria_export_${_ts()}.xlsx`, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
}

export async function downloadPdfReport(resultId) {
  const response = await api.get(`/export/${resultId}/pdf-report`, {
    responseType: 'blob',
  })
  return response
}

function _triggerDownload(blob, filename, type) {
  const url = URL.createObjectURL(new Blob([blob], { type }))
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 100)
}

function _ts() {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
}

// ─── Resume File Download / View ─────────────────────────────────────────────

export async function downloadCandidateResume(candidateId, filename) {
  const res = await api.get(`/candidates/${candidateId}/resume`, { responseType: 'blob' })
  const type = res.headers['content-type'] || 'application/octet-stream'
  // If server converted .doc to PDF, ensure downloaded file has .pdf extension
  if (type === 'application/pdf' && filename && !filename.toLowerCase().endsWith('.pdf')) {
    filename = filename.replace(/\.[^.]+$/, '') + '.pdf'
  }
  _triggerDownload(res.data, filename, type)
}

export async function viewCandidateResume(candidateId) {
  const res = await api.get(`/candidates/${candidateId}/resume`, { responseType: 'blob' })
  const type = res.headers['content-type'] || 'application/octet-stream'
  const url = URL.createObjectURL(new Blob([res.data], { type }))
  window.open(url, '_blank')
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

// ─── Templates (legacy — prefer requisitions) ────────────────────────────────

/** @deprecated Use getRequisitionsForPicker */
export async function getTemplates() {
  return getRequisitionsForPicker()
}

/** Requisitions shaped for JD picker dropdowns (replaces Role Template library). */
export async function getRequisitionsForPicker() {
  const data = await listRequisitions()
  const arr = Array.isArray(data) ? data : []
    return arr.map((r) => ({
    id: r.id,
    name: r.title,
    title: r.title,
    jd_text: r.jd_text,
    scoring_weights: r.scoring_weights,
    required_skills_override: r.required_skills_override,
    nice_to_have_skills_override: r.nice_to_have_skills_override,
    calibrated_criteria_json: r.calibrated_criteria_json,
    is_calibrated: r.is_calibrated,
    intake_gate_warning: r.intake_gate_warning,
    intake_status: r.intake_status,
    current_criteria_version: r.current_criteria_version,
    candidate_count: r.candidate_count,
    status: r.status,
    client_name: r.client_name,
    location: r.location,
  }))
}

function appendAnalyzeContext(formData, { requisitionId, templateId, skillOverrides, scoringWeights, action, candidateId } = {}) {
  if (scoringWeights) {
    formData.append('scoring_weights', JSON.stringify(scoringWeights))
  }
  if (requisitionId) {
    formData.append('requisition_id', String(requisitionId))
  } else if (templateId) {
    formData.append('template_id', String(templateId))
  }
  if (skillOverrides) {
    formData.append('skill_overrides', JSON.stringify(skillOverrides))
  }
  if (action) {
    formData.append('action', action)
  }
  if (candidateId) {
    formData.append('candidate_id', String(candidateId))
  }
}

export async function createTemplate(data) {
  const created = await createRequisition({
    title: data.name,
    jd_text: data.jd_text,
    tags: data.tags,
    scoring_weights: data.scoring_weights,
    required_skills_override: typeof data.required_skills_override === 'string'
      ? JSON.parse(data.required_skills_override || '[]')
      : data.required_skills_override,
    nice_to_have_skills_override: typeof data.nice_to_have_skills_override === 'string'
      ? JSON.parse(data.nice_to_have_skills_override || '[]')
      : data.nice_to_have_skills_override,
    status: 'draft',
  })
  return { ...created, name: created.title }
}

export async function createRequisitionFromFile(title, file, tags, scoringWeights) {
  const formData = new FormData()
  formData.append('title', title)
  formData.append('jd_file', file)
  if (tags != null) {
    formData.append('tags', typeof tags === 'string' ? tags : JSON.stringify(tags))
  }
  if (scoringWeights) {
    formData.append('scoring_weights', JSON.stringify(scoringWeights))
  }
  formData.append('status', 'draft')
  const response = await api.post('/requisitions/from-file', formData)
  return response.data
}

/** @deprecated Use createRequisitionFromFile */
export async function createTemplateFromFile(name, file, tags, scoringWeights) {
  return createRequisitionFromFile(name, file, tags, scoringWeights)
}

export async function updateTemplate(id, data) {
  const payload = { ...data }
  if (payload.name) {
    payload.title = payload.name
    delete payload.name
  }
  if (typeof payload.required_skills_override === 'string') {
    try { payload.required_skills_override = JSON.parse(payload.required_skills_override) } catch { /* keep */ }
  }
  if (typeof payload.nice_to_have_skills_override === 'string') {
    try { payload.nice_to_have_skills_override = JSON.parse(payload.nice_to_have_skills_override) } catch { /* keep */ }
  }
  const updated = await updateRequisition(id, payload)
  return { ...updated, name: updated.title }
}

export async function deleteTemplate(id) {
  await deleteRequisition(id)
}

// ─── Skill Classification Templates ─────────────────────────────────────────

export async function getSkillTemplates() {
  const res = await api.get('/templates/skill-classifications')
  return res.data
}

export async function createSkillTemplate(data) {
  const res = await api.post('/templates/skill-classifications', data)
  return res.data
}

export async function updateSkillTemplate(templateId, data) {
  const res = await api.put(`/templates/skill-classifications/${templateId}`, data)
  return res.data
}

export async function deleteSkillTemplate(templateId) {
  const res = await api.delete(`/templates/skill-classifications/${templateId}`)
  return res.data
}

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

// ─── ATS Integrations ─────────────────────────────────────────────────────────

export async function listATSConnections() {
  const response = await api.get('/ats/connections')
  return response.data
}

export async function createATSConnection(data) {
  const response = await api.post('/ats/connections', data)
  return response.data
}

export async function updateATSConnection(id, data) {
  const response = await api.put(`/ats/connections/${id}`, data)
  return response.data
}

export async function deleteATSConnection(id) {
  await api.delete(`/ats/connections/${id}`)
}

export async function pushToATS(connectionId, data) {
  const response = await api.post(`/ats/connections/${connectionId}/push`, data)
  return response.data
}

export async function pullFromATS(connectionId, data) {
  const response = await api.post(`/ats/connections/${connectionId}/pull`, data)
  return response.data
}

export async function getATSSyncLogs(connectionId, limit = 50) {
  const response = await api.get(`/ats/connections/${connectionId}/logs`, { params: { limit } })
  return response.data
}

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

export default api
