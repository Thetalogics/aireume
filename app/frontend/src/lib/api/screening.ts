import axios from 'axios'
import api, { API_URL, getCsrfToken } from './client'
import { createRequisition, listRequisitions, updateRequisition } from './requisitions'

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
  const { uploadMultipleFiles } = await import('../uploadChunked')

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

  const { uploadMultipleFiles } = await import('../uploadChunked')

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

