import { useState, useCallback, useEffect, useRef } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useDropzone } from 'react-dropzone'
import {
  analyzeResumeStream,
  analyzeBatchStream,
  submitBatchToQueue,
  extractJdFromUrl,
  getRequisitionsForPicker,
  getRequisitionSettings,
  createRequisition,
  createRequisitionFromFile,
  updateRequisition,
  checkRequisitionIntakeGate,
  getNarrative,
  checkHealth,
  parseJdPreview,
  parseJdPreviewFromFile,
} from '../../lib/api'
import { storeJdFile, getJdFile, clearJdFile } from '../../lib/jdFileCache'
import { useUsageCheck, useSubscription } from '../../hooks/useSubscription'
import { usePlanLimits } from '../usePlanLimits'
import { useNotification } from '../../contexts/useNotification'
import { useOnboarding } from '../../contexts/useOnboarding'
import useFeatureGuide from '../../hooks/useFeatureGuide'
import { isValidWeightTotal } from '../universalWeights'
import { showSuccess } from '../../lib/toast'
import { mergeNarrativePollResult, isNarrativePending, isKitPending, isReportCacheable } from '../../lib/enrichmentUtils'
import {
  buildSetupSummary,
  buildRequisitionTitle,
  buildRequisitionTags,
  extractRoleTitle,
  getActiveAnalyzeStep,
  getEffectiveBatchTotal,
} from '../../lib/analyzeBatchUtils'
import {
  buildSkillStateFromRequisition,
  canUseAdHocPath,
  requiresRequisitionSelection,
} from '../../lib/analyzeRequisitionUtils'
import { ANALYZE } from '../../lib/uxLabels'
import {
  DEFAULT_WEIGHTS, WEIGHT_PRESETS,
  BACKGROUND_BATCH_MIN, BACKGROUND_BATCH_AUTO,
} from './analyzeConstants'

export default function useAnalyzePage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { checkBeforeAnalysis, getRemainingAnalyses } = useUsageCheck()
  const { subscription, refreshAfterAnalysis, isFeatureAvailable } = useSubscription()
  const { batchSize: planBatchLimit } = usePlanLimits()
  const hasRequisitions = isFeatureAvailable('requisitions')
  const hasCustomWeights = isFeatureAvailable('custom_weights')
  const {
    startBatchAnalysis,
    updateProgress,
    completeBatchAnalysis,
    trackEnrichmentJob,
    updateEnrichmentJob,
    completeEnrichmentJob,
    addNotification,
    trackQueueBatch,
  } = useNotification()
  const { completeChecklistItem } = useOnboarding()
  const analyzeGuide = useFeatureGuide('analyze')

  // Step 1: Job Description
  const [jdText, setJdText] = useState('')
  const [jdMode, setJdMode] = useState('text')
  const [jdFile, setJdFile] = useState(null)
  const [urlInput, setUrlInput] = useState('')
  const [urlLoading, setUrlLoading] = useState(false)
  const [urlError, setUrlError] = useState('')

  // Weights (now optional, inside collapsible Advanced section in Step 2)
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)
  const [roleCategory, setRoleCategory] = useState('general')
  const [showAiSuggestion, setShowAiSuggestion] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [weightsManuallySet, setWeightsManuallySet] = useState(false)
  const [weightPreset, setWeightPreset] = useState('balanced')

  // Step 2: Resume Upload & Analyze
  const [files, setFiles] = useState([])
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [error, setError] = useState('')

  // Streaming analysis state for batch analysis
  const [streamingResults, setStreamingResults] = useState([])
  const [streamingFailed, setStreamingFailed] = useState([])
  const [analysisProgress, setAnalysisProgress] = useState({ completed: 0, total: 0 })
  const [analysisDone, setAnalysisDone] = useState(false)
  const [fileStatuses, setFileStatuses] = useState([])
  const [batchStartTime, setBatchStartTime] = useState(null)
  const [runInBackground, setRunInBackground] = useState(false)
  const [batchStuckError, setBatchStuckError] = useState(null)
  const [setupSummaryExpanded, setSetupSummaryExpanded] = useState(false)
  const [queuedBatchInfo, setQueuedBatchInfo] = useState(null)

  useEffect(() => {
    if (files.length >= BACKGROUND_BATCH_AUTO) {
      setRunInBackground(true)
    }
  }, [files.length])
  const [currentStep, setCurrentStep] = useState(1)
  const [draftSaved, setDraftSaved] = useState(false)
  const [requisitions, setRequisitions] = useState([])
  const [requisitionsLoading, setRequisitionsLoading] = useState(false)
  const [requisitionSearch, setRequisitionSearch] = useState('')
  const [hasLoadedRequisition, setHasLoadedRequisition] = useState(false)
  const [loadedRequisitionId, setLoadedRequisitionId] = useState(null)
  const [loadedRequisition, setLoadedRequisition] = useState(null)
  const [screeningMode, setScreeningMode] = useState('requisition_required')
  const [adHocMode, setAdHocMode] = useState(false)
  const [intakeGateStatus, setIntakeGateStatus] = useState(null)
  const [showJdModal, setShowJdModal] = useState(false)

  // Skill Classification state (mandatory review before analysis)
  const [jdParseResult, setJdParseResult]     = useState(null)
  const [skillOverrides, setSkillOverrides]    = useState(null)
  const [parsingJd, setParsingJd]             = useState(false)
  const [skillsConfirmed, setSkillsConfirmed] = useState(false)
  const [parseError, setParseError]           = useState(null)
  const [roleName, setRoleName]               = useState('')
  const [streamStage, setStreamStage]         = useState(null)
  const [singleFileName, setSingleFileName]   = useState(null)
  const debounceRef = useRef(null)
  const skipAutoParseRef = useRef(false)
  const roleNameTouchedRef = useRef(false)
  const streamingResultsRef = useRef([])
  const streamingFailedRef = useRef([])
  const sessionRestoredRef = useRef(false)
  const draftRestoredRef = useRef(false)
  const [savedDraft] = useState(() => localStorage.getItem('aria_draft_jd'))

  // Reset analysis state on fresh mount
  useEffect(() => {
    setError('')
    return () => {
      // Cleanup on unmount
      setError('')
    }
  }, [])

  // Lightweight health check on mount
  useEffect(() => {
    checkHealth().catch(() => {
      setError('Backend service may be unavailable. Please try again shortly.')
    })
  }, [])

  // Clean up old report entries in sessionStorage
  useEffect(() => {
    const keysToRemove = []
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i)
      if (key && key.startsWith('report_')) {
        keysToRemove.push(key)
      }
    }
    // Keep only the 10 most recent
    if (keysToRemove.length > 10) {
      keysToRemove.slice(0, keysToRemove.length - 10).forEach(k => sessionStorage.removeItem(k))
    }
  }, [])

  // Load requisitions for picker (Growth+ only)
  useEffect(() => {
    if (!hasRequisitions) {
      setRequisitions([])
      setScreeningMode('allow_ad_hoc')
      return
    }
    setRequisitionsLoading(true)
    getRequisitionsForPicker()
      .then((res) => {
        const arr = Array.isArray(res) ? res : res?.templates || []
        setRequisitions(arr)
      })
      .catch(() => setRequisitions([]))
      .finally(() => setRequisitionsLoading(false))

    getRequisitionSettings()
      .then((s) => setScreeningMode(s?.screening_mode || 'requisition_required'))
      .catch(() => setScreeningMode('requisition_required'))
  }, [hasRequisitions])

  useEffect(() => {
    if (!loadedRequisitionId) {
      setIntakeGateStatus(null)
      return
    }
    checkRequisitionIntakeGate(loadedRequisitionId)
      .then(setIntakeGateStatus)
      .catch(() => setIntakeGateStatus(null))
  }, [loadedRequisitionId])

  // Load requisition from URL query (?requisition_id=)
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const reqId = params.get('requisition_id')
    if (!reqId || !requisitions.length) return
    const req = requisitions.find((r) => String(r.id) === reqId)
    if (req) handleLoadRequisition(req)
  }, [location.search, requisitions])

  // Auto-save draft to localStorage
  useEffect(() => {
    if (jdText || Object.keys(weights).length > 0) {
      const draft = {
        jd_text: jdText,
        weights: weights,
        role_category: roleCategory,
        timestamp: new Date().toISOString()
      }
      localStorage.setItem('aria_draft_jd', JSON.stringify(draft))
      setDraftSaved(true)
      setTimeout(() => setDraftSaved(false), 2000)
    }
  }, [jdText, weights, roleCategory])

  // Restore draft on mount (skip when requisition-first flow is enforced)
  useEffect(() => {
    if (draftRestoredRef.current) return
    if (hasRequisitions && requiresRequisitionSelection(hasRequisitions, screeningMode)) return
    draftRestoredRef.current = true
    if (location.state?.jd_text || location.state?.jd_mode) return
    const draft = savedDraft
    if (draft) {
      try {
        const { jd_text, weights: savedWeights, role_category } = JSON.parse(draft)
        if (jd_text) setJdText(jd_text)
        if (savedWeights) setWeights(savedWeights)
        if (role_category) setRoleCategory(role_category)
      } catch (e) {
        console.error('Failed to restore draft:', e)
      }
    }
  }, [hasRequisitions, screeningMode, savedDraft, location.state?.jd_text, location.state?.jd_mode])

  useEffect(() => {
    if (!hasRequisitions) return
    if (requiresRequisitionSelection(hasRequisitions, screeningMode)) {
      setAdHocMode(false)
    }
  }, [hasRequisitions, screeningMode])

  // Restore active session from sessionStorage on fresh mount
  // ONLY auto-advance when coming from "Analyze Another Resume" (flag set in ReportPage)
  useEffect(() => {
    if (location.state?.jd_text || location.state?.jd_mode) return

    const params = new URLSearchParams(location.search)
    const returningFromReport = params.get('restored') === 'true' || location.state?.from === '/analyze'

    if (returningFromReport && !sessionRestoredRef.current) {
      sessionRestoredRef.current = true
      const savedBatch = sessionStorage.getItem('aria_batch_results')
      if (savedBatch) {
        try {
          const batch = JSON.parse(savedBatch)
          // Only restore if less than 30 minutes old
          if (batch.timestamp && (Date.now() - batch.timestamp) < 30 * 60 * 1000) {
            const results = batch.results || []
            const failed = batch.failed || []
            if (results.length === 0 && failed.length === 0) {
              sessionStorage.removeItem('aria_batch_results')
              return
            }
            setStreamingResults(results)
            setStreamingFailed(failed)
            setAnalysisProgress(batch.progress || { completed: results.length + failed.length, total: results.length + failed.length })
            setAnalysisDone(true)
            setCurrentStep(3)
            // Also restore batch context (JD text, skill overrides, etc.)
            const savedContext = sessionStorage.getItem('aria_batch_context')
            if (savedContext) {
              try {
                const ctx = JSON.parse(savedContext)
                if (ctx.jdText) setJdText(ctx.jdText)
                if (ctx.skillOverrides) setSkillOverrides(ctx.skillOverrides)
                if (ctx.skillsConfirmed !== undefined) setSkillsConfirmed(ctx.skillsConfirmed)
                if (ctx.jdParseResult) setJdParseResult(ctx.jdParseResult)
                if (ctx.jdMode) setJdMode(ctx.jdMode)
                if (ctx.weights) setWeights(ctx.weights)
                if (ctx.roleCategory) setRoleCategory(ctx.roleCategory)
                // Skip auto-parse so it doesn't clobber restored jdParseResult
                skipAutoParseRef.current = true
              } catch {}
            }
            // Clean URL param without triggering navigation
            window.history.replaceState({}, '', '/analyze')
            return  // Skip the rest of session restoration logic
          }
        } catch {}
      }
    }

    if (sessionRestoredRef.current) return

    const isAnalyzeAnother = sessionStorage.getItem('aria_analyze_another')

    if (!isAnalyzeAnother && !returningFromReport) {
      // Fresh navigation (Dashboard, nav menu, etc.) — clear stale session data
      sessionStorage.removeItem('aria_active_jd')
      sessionStorage.removeItem('aria_batch_results')  // Also clear batch results
      sessionRestoredRef.current = true
      return
    }

    // Clear the one-time flag immediately
    sessionStorage.removeItem('aria_analyze_another')

    const savedSession = sessionStorage.getItem('aria_active_jd')
    if (!savedSession) {
      sessionRestoredRef.current = true
      return
    }
    try {
      const ctx = JSON.parse(savedSession)
      if (ctx.jd_text) setJdText(ctx.jd_text)
      if (ctx.weights) setWeights(ctx.weights)
      if (ctx.role_category) setRoleCategory(ctx.role_category)
      if (ctx.jd_mode) setJdMode(ctx.jd_mode)
      if (ctx.skillOverrides) setSkillOverrides(ctx.skillOverrides)
      if (ctx.jdParseResult) setJdParseResult(ctx.jdParseResult)
      if (ctx.skillsConfirmed) setSkillsConfirmed(ctx.skillsConfirmed)
      // Auto-skip to upload if JD context is complete
      if ((ctx.jd_text || ctx.jd_mode === 'file') && ctx.weights) {
        setCurrentStep(2)
      }
    } catch (e) {
      console.error('Failed to restore session:', e)
    }
    sessionRestoredRef.current = true
  }, [location.search, location.state?.from, location.state?.jd_mode, location.state?.jd_text])

  // Load JD from location state (from Requisitions or ReportPage)
  useEffect(() => {
    if (location.state?.jd_text) {
      setJdText(location.state.jd_text)
      if (location.state.weights) {
        setWeights(location.state.weights)
      }
      if (location.state.role_category) {
        setRoleCategory(location.state.role_category)
      }
      // Restore skill overrides if available
      if (location.state.skillOverrides) {
        setSkillOverrides(location.state.skillOverrides)
        setSkillsConfirmed(location.state.skillsConfirmed ?? false)
      }
      if (location.state.jdParseResult) {
        setJdParseResult(location.state.jdParseResult)
      }
      // Mark as loaded requisition to prevent duplicate creation on analyze
      if (location.state.requisition_id || location.state.template_id) {
        setHasLoadedRequisition(true)
        setLoadedRequisitionId(location.state.requisition_id || location.state.template_id)
      }
      if (location.state.requisition_title || location.state.template_name) {
        setRoleName(location.state.requisition_title || location.state.template_name)
        roleNameTouchedRef.current = true
      }
    }
  }, [location.state])

  // Load file JD from IndexedDB and auto-skip when returning from ReportPage
  useEffect(() => {
    if (location.state?.jd_mode === 'file') {
      getJdFile().then(file => {
        if (file) {
          setJdFile(file)
          setJdMode('file')
          if (location.state.weights) {
            setWeights(location.state.weights)
          }
          if (location.state.role_category) {
            setRoleCategory(location.state.role_category)
          }
          // Restore skill overrides if available
          if (location.state.skillOverrides) {
            setSkillOverrides(location.state.skillOverrides)
            setSkillsConfirmed(location.state.skillsConfirmed ?? false)
          }
          if (location.state.jdParseResult) {
            setJdParseResult(location.state.jdParseResult)
          }
          setCurrentStep(2)
        }
      }).catch((err) => {
        console.warn('Failed to load from IndexedDB:', err)
        setError('Failed to load saved data. You can continue with a fresh analysis.')
      })
    }
  }, [location.state])

  // Auto-skip to upload when returning with text JD context
  useEffect(() => {
    if (location.state?.jd_text && location.state?.weights) {
      setCurrentStep(2)
    }
  }, [location.state])

  // ── Auto-parse JD text with debounce (1.5s after user stops typing) ──
  useEffect(() => {
    if (jdMode !== 'text') return

    // Skip auto-parse when we just restored overrides from a template —
    // the restored jdParseResult should not be clobbered by a fresh parse.
    if (skipAutoParseRef.current) {
      skipAutoParseRef.current = false
      return
    }

    if (debounceRef.current) clearTimeout(debounceRef.current)

    const words = (jdText || '').trim().split(/\s+/).filter(Boolean).length
    if (words < 80) {
      setJdParseResult(null)
      setParseError(null)
      return
    }

    debounceRef.current = setTimeout(async () => {
      setParsingJd(true)
      setParseError(null)
      try {
        const data = await parseJdPreview(jdText)
        setJdParseResult(data)
      } catch (err) {
        console.warn('JD auto-parse failed:', err)
        setParseError(err.message || 'Failed to parse job description')
      } finally {
        setParsingJd(false)
      }
    }, 1500)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [jdText, jdMode])

  // Auto-fill role name from parsed JD title until recruiter edits it
  useEffect(() => {
    if (roleNameTouchedRef.current) return
    const extracted = extractRoleTitle(jdParseResult, roleCategory, '')
    if (extracted) setRoleName(extracted)
  }, [jdParseResult, roleCategory])

  // ── Auto-parse JD file on upload ──
  useEffect(() => {
    if (jdMode !== 'file' || !jdFile) return

    let cancelled = false
    const parseFile = async () => {
      setParsingJd(true)
      setParseError(null)
      setSkillsConfirmed(false)
      try {
        const data = await parseJdPreviewFromFile(jdFile)
        if (!cancelled) setJdParseResult(data)
      } catch (err) {
        if (!cancelled) {
          console.warn('JD file auto-parse failed:', err)
          setParseError(err.message || 'Failed to parse job description file')
        }
      } finally {
        if (!cancelled) setParsingJd(false)
      }
    }

    parseFile()
    return () => { cancelled = true }
  }, [jdMode, jdFile])

  // ── Auto-select weight preset based on JD role detection ──
  useEffect(() => {
    if (jdParseResult && !weightsManuallySet) {
      const seniority = jdParseResult.seniority || ''
      const jobFunction = jdParseResult.job_function || ''

      if (seniority.toLowerCase().includes('senior') || seniority.toLowerCase().includes('lead') || seniority.toLowerCase().includes('principal')) {
        setWeightPreset('skill-heavy')
        setWeights(WEIGHT_PRESETS['skill-heavy'])
      } else if (seniority.toLowerCase().includes('manager') || seniority.toLowerCase().includes('director') || seniority.toLowerCase().includes('vp')) {
        setWeightPreset('experience-heavy')
        setWeights(WEIGHT_PRESETS['experience-heavy'])
      } else if (jobFunction.toLowerCase().includes('research') || jobFunction.toLowerCase().includes('data')) {
        setWeightPreset('domain-focused')
        setWeights(WEIGHT_PRESETS['domain-focused'])
      } else {
        setWeightPreset('balanced')
        setWeights(WEIGHT_PRESETS['balanced'])
      }
    }
  }, [jdParseResult, weightsManuallySet])

  // ── Auto-advance from Step 1 to Step 2 when skills are confirmed ──
  useEffect(() => {
    if (skillsConfirmed) setCurrentStep(step => step === 1 ? 2 : step)
  }, [skillsConfirmed])

  // Handle weights change — marks as manually set
  const handleWeightsChange = (newWeights) => {
    setWeights(newWeights)
    setWeightsManuallySet(true)
  }

  useEffect(() => { streamingResultsRef.current = streamingResults }, [streamingResults])

  const pendingEnrichmentIds = JSON.stringify(analysisDone ? streamingResults
    .filter(item => item.screeningResultId && (isNarrativePending(item.result) || isKitPending(item.result)))
    .map(item => item.screeningResultId) : [])

  // Poll for narrative completion on batch results
  useEffect(() => {
    let pendingIds = JSON.parse(pendingEnrichmentIds)
    if (!pendingIds.length) return
    let cancelled = false
    let timer
    let pollCount = 0
    const maxPolls = 120 // 6 minutes max

    const poll = async () => {
      pollCount++
      if (pollCount > maxPolls) return

      const stillPending = []

      for (const id of pendingIds) {
        const labelFor = (rid) =>
          streamingResultsRef.current.find((r) => r.screeningResultId === rid)?.filename || `Report #${rid}`

        try {
          const data = await getNarrative(id)
          if (cancelled) return
          if (data.status === 'ready' || data.status === 'fallback' || data.status === 'failed') {
            const kitStatus = data.interview_kit_status
            const kitPending = kitStatus === 'pending' || kitStatus === 'processing'
            setStreamingResults(prev => prev.map(item => {
              if (item.screeningResultId !== id) return item
              const updatedResult = mergeNarrativePollResult(item.result, data)
              try {
                if (isReportCacheable(updatedResult)) {
                  sessionStorage.setItem(`report_${id}`, JSON.stringify(updatedResult))
                }
              } catch {}
              try {
                const currentResults = JSON.parse(sessionStorage.getItem('aria_batch_results') || '{}')
                if (currentResults.results) {
                  currentResults.results = currentResults.results.map(batchItem =>
                    batchItem.screeningResultId === id ? { ...batchItem, result: updatedResult } : batchItem
                  )
                  sessionStorage.setItem('aria_batch_results', JSON.stringify(currentResults))
                }
              } catch {}
              return { ...item, result: updatedResult }
            }))
            if (kitPending) {
              stillPending.push(id)
              updateEnrichmentJob(`enrich-${id}`, {
                phase: kitStatus === 'processing' ? 'Interview kit generating' : 'AI insights ready, kit pending',
                status: 'processing',
              })
            } else {
              completeEnrichmentJob(`enrich-${id}`, {
                phase: 'Complete',
                status: data.status === 'ready' ? 'ready' : 'fallback',
              })
              addNotification({
                type: 'success',
                title: 'Report enrichment complete',
                message: `${labelFor(id)} — interview kit ready`,
                href: `/report?id=${id}`,
              })
            }
          } else if (data.interview_kit_status === 'ready' || data.interview_kit_status === 'fallback') {
            setStreamingResults(prev => prev.map(item => {
              if (item.screeningResultId !== id) return item
              const updatedResult = mergeNarrativePollResult(item.result, data)
              return { ...item, result: updatedResult }
            }))
            completeEnrichmentJob(`enrich-${id}`, { phase: 'Complete', status: 'ready' })
          } else {
            stillPending.push(id)
          }
        } catch (e) {
          stillPending.push(id)
        }
      }

      pendingIds = stillPending
      if (!cancelled && stillPending.length && pollCount < maxPolls) {
        timer = setTimeout(poll, 3000)
      }
    }

    timer = setTimeout(poll, 2000)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [pendingEnrichmentIds, addNotification, completeEnrichmentJob, updateEnrichmentJob])

  // Handle URL extraction
  const handleExtractUrl = async () => {
    if (!urlInput.trim()) return
    setUrlLoading(true)
    setUrlError('')
    try {
      const result = await extractJdFromUrl(urlInput.trim())
      setJdText(result.jd_text)
      setJdMode('text')
      setShowAiSuggestion(true)
      // Reset skill confirmation since JD changed
      setSkillsConfirmed(false)
      setSkillOverrides(null)
      setJdParseResult(null)
    } catch (err) {
      setUrlError(err.response?.data?.detail || 'Failed to extract JD from URL')
    } finally {
      setUrlLoading(false)
    }
  }

  // Handle JD file upload
  const onJdDrop = useCallback((acceptedFiles) => {
    if (acceptedFiles.length > 0) {
      setJdFile(acceptedFiles[0])
      setJdMode('file')
      // Reset skill confirmation since JD source changed
      setSkillsConfirmed(false)
      setSkillOverrides(null)
      setJdParseResult(null)
    }
  }, [])

  const { getRootProps: getJdRootProps, getInputProps: getJdInputProps, isDragActive: isJdDragActive } = useDropzone({
    onDrop: onJdDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/msword': ['.doc'],
      'text/plain': ['.txt'],
    },
    maxFiles: 1,
    maxSize: 5 * 1024 * 1024
  })

  // Handle resume file upload (respect plan batch limit)
  const onResumeDrop = useCallback((acceptedFiles, rejectedFiles) => {
    setFiles((prev) => {
      const room = planBatchLimit - prev.length
      return [...prev, ...acceptedFiles.slice(0, Math.max(0, room))]
    })
    if (rejectedFiles?.length || acceptedFiles.length + files.length > planBatchLimit) {
      addNotification({
        type: 'warning',
        title: 'File limit reached',
        message: `Your plan allows up to ${planBatchLimit} resumes per batch.`,
      })
    }
  }, [planBatchLimit, files.length, addNotification])

  const { getRootProps: getResumeRootProps, getInputProps: getResumeInputProps, isDragActive: isResumeDragActive } = useDropzone({
    onDrop: onResumeDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/msword': ['.doc']
    },
    maxFiles: planBatchLimit,
    maxSize: 10 * 1024 * 1024
  })

  const removeFile = (index) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
  }

  // Handle AI weight acceptance
  const handleWeightsAccepted = (suggestedWeights) => {
    setWeights(suggestedWeights)
    setWeightsManuallySet(true)
    setShowAiSuggestion(false)
  }

  // Load requisition from picker or deep link
  const handleLoadRequisition = (requisition) => {
    if (!requisition) return
    setJdText(requisition.jd_text || '')
    setJdMode('text')
    setAdHocMode(false)
    setRoleName(requisition.name || requisition.title || '')
    roleNameTouchedRef.current = Boolean(requisition.name || requisition.title)
    setLoadedRequisition(requisition)
    setHasLoadedRequisition(true)
    setLoadedRequisitionId(requisition.id)

    const skillState = buildSkillStateFromRequisition(requisition)
    setSkillOverrides(skillState.skillOverrides)
    setSkillsConfirmed(skillState.skillsConfirmed)
    setJdParseResult(skillState.jdParseResult)
    skipAutoParseRef.current = skillState.skipAutoParse

    let hasWeights = false
    if (requisition.scoring_weights) {
      try {
        const savedWeights = typeof requisition.scoring_weights === 'string'
          ? JSON.parse(requisition.scoring_weights)
          : requisition.scoring_weights
        if (savedWeights && Object.keys(savedWeights).length > 0) {
          setWeights(savedWeights)
          hasWeights = true
        }
      } catch (e) {
        console.error('Failed to parse weights:', e)
      }
    }
    if (!hasWeights) {
      setShowAiSuggestion(true)
    }
  }

  const clearRequisitionSelection = () => {
    setHasLoadedRequisition(false)
    setLoadedRequisitionId(null)
    setLoadedRequisition(null)
    setIntakeGateStatus(null)
    setJdText('')
    setJdMode('text')
    setRoleName('')
    roleNameTouchedRef.current = false
    setSkillOverrides(null)
    setSkillsConfirmed(false)
    setJdParseResult(null)
    skipAutoParseRef.current = false
    setAdHocMode(false)
    navigate('/analyze', { replace: true })
  }

  const enableAdHocMode = () => {
    setHasLoadedRequisition(false)
    setLoadedRequisitionId(null)
    setLoadedRequisition(null)
    setIntakeGateStatus(null)
    setJdText('')
    setJdMode('text')
    setRoleName('')
    roleNameTouchedRef.current = false
    setSkillOverrides(null)
    setSkillsConfirmed(false)
    setJdParseResult(null)
    skipAutoParseRef.current = false
    setAdHocMode(true)
    navigate('/analyze', { replace: true })
  }

  // Handle analysis
  const handleAnalyze = async () => {
    // Validation
    const requisitionRequired = requiresRequisitionSelection(hasRequisitions, screeningMode)
    if (requisitionRequired && !hasLoadedRequisition && !adHocMode) {
      setError(ANALYZE.selectRequisitionError)
      return
    }
    const effectiveJd = jdMode === 'text' ? jdText : jdFile
    if (!effectiveJd) {
      setError('Please provide a job description')
      return
    }
    if (files.length === 0) {
      setError('Please upload at least one resume')
      return
    }
    if (!isValidWeightTotal(weights)) {
      setError('Scoring weights must sum to 100% (98–102% allowed). Adjust weights in Advanced settings.')
      return
    }

    // Check usage limits
    const check = await checkBeforeAnalysis(files.length)
    if (!check.allowed) {
      setError(check.message || 'Usage limit exceeded. Please upgrade your plan.')
      return
    }

    setError('')

    // Persist JD context so ReportPage can offer "Analyze Another"
    if (jdMode === 'text' && jdText) {
      sessionStorage.setItem('aria_active_jd', JSON.stringify({
        jd_text: jdText,
        weights,
        role_category: roleCategory,
        jd_mode: 'text',
        skillOverrides,
        jdParseResult,
        skillsConfirmed
      }))
      clearJdFile().catch(() => {})
    } else if (jdMode === 'file' && jdFile) {
      try {
        await storeJdFile(jdFile)
        sessionStorage.setItem('aria_active_jd', JSON.stringify({
          weights,
          role_category: roleCategory,
          jd_mode: 'file',
          file_name: jdFile.name,
          skillOverrides,
          jdParseResult,
          skillsConfirmed
        }))
      } catch { /* ignore */ }
    }

    sessionStorage.removeItem('aria_batch_results')
    // Save full batch context for back-navigation restoration
    try {
      sessionStorage.setItem('aria_batch_context', JSON.stringify({
        jdText,
        skillOverrides,
        skillsConfirmed,
        jdParseResult,
        jdMode,
        weights,
        roleCategory,
        fileNames: files.map(f => f.name),
        timestamp: Date.now()
      }))
    } catch {}
    setIsAnalyzing(true)
    if (files.length > 1) {
      setAnalysisProgress({ completed: 0, total: files.length })
      setFileStatuses(files.map((f, i) => ({
        filename: f.name,
        status: 'queued',
        index: i + 1,
      })))
    }

    let activeReqId = loadedRequisitionId

    try {
      if (hasRequisitions && !hasLoadedRequisition && (adHocMode || screeningMode === 'allow_ad_hoc')) {
        const reqTitle = buildRequisitionTitle(roleName, jdParseResult, roleCategory)
        const reqTags = buildRequisitionTags(jdParseResult, roleCategory)
        let created
        if (jdMode === 'text') {
          created = await createRequisition({
            title: reqTitle,
            jd_text: jdText,
            scoring_weights: weights,
            tags: reqTags,
            required_skills_override: skillOverrides?.required_skills ?? null,
            nice_to_have_skills_override: skillOverrides?.nice_to_have_skills ?? null,
            status: 'draft',
          })
        } else {
          created = await createRequisitionFromFile(reqTitle, jdFile, reqTags, weights)
        }
        activeReqId = created.id
        setLoadedRequisitionId(created.id)
        setHasLoadedRequisition(true)
      } else if (hasRequisitions && loadedRequisitionId && skillOverrides) {
        try {
          await updateRequisition(loadedRequisitionId, {
            required_skills_override: skillOverrides.required_skills,
            nice_to_have_skills_override: skillOverrides.nice_to_have_skills,
          })
        } catch (err) {
          console.warn('Failed to update requisition overrides before analysis:', err)
        }
      }

      if (activeReqId) {
        const gate = await checkRequisitionIntakeGate(activeReqId)
        if (gate.blocks) {
          setError(gate.warning || 'Complete HM intake and calibration before screening.')
          setIsAnalyzing(false)
          return
        }
      }

      // Run analysis - auto-detect single vs batch
      if (files.length === 1) {
        setStreamStage('parsing')
        setSingleFileName(files[0].name)
        const result = await analyzeResumeStream(
          files[0],
          jdMode === 'text' ? jdText : null,
          jdMode === 'file' ? jdFile : null,
          weights,
          (event) => {
            if (event.stage === 'parsing') setStreamStage('parsing')
            else if (event.stage === 'scoring') setStreamStage('scoring')
            else if (event.stage === 'complete') setStreamStage('complete')
          },
          null,
          skillOverrides,
          activeReqId,
        )
        setStreamStage(null)
        setSingleFileName(null)
        const resultId = result?.result_id || result?.analysis_id
        if (resultId) {
          trackEnrichmentJob({
            id: `enrich-${resultId}`,
            label: files[0].name,
            status: 'processing',
            phase: 'AI enrichment',
            href: `/report?id=${resultId}`,
          })
        }
        navigate('/report', { state: { result } })
        completeChecklistItem('analyzedResume')
      } else if (runInBackground && files.length >= BACKGROUND_BATCH_MIN) {
        const batch = await submitBatchToQueue(
          files,
          jdMode === 'text' ? jdText : null,
          jdMode === 'file' ? jdFile : null,
          weights,
          null,
          skillOverrides,
          8,
          activeReqId,
        )
        trackQueueBatch(batch)
        showSuccess(`Queued ${batch.queued} resume${batch.queued !== 1 ? 's' : ''} for background analysis`)
        addNotification({
          type: 'success',
          title: 'Batch queued',
          message: `${batch.queued} resume${batch.queued !== 1 ? 's' : ''} scoring in the background. Open Activity in the nav to track progress.`,
        })
        if (batch.failed > 0) {
          addNotification({
            type: 'warning',
            title: 'Some files could not be queued',
            message: `${batch.failed} file(s) failed to enqueue. Check file sizes and formats.`,
          })
        }
        setQueuedBatchInfo({ count: batch.queued || files.length })
        setFiles([])
        setCurrentStep(2)
        await refreshAfterAnalysis(batch.queued || files.length)
      } else {
        // Batch analysis with SSE streaming
        setBatchStuckError(null)
        setCurrentStep(3)
        startBatchAnalysis(files.length)
        setStreamingResults([])
        setStreamingFailed([])
        setAnalysisDone(false)
        setAnalysisProgress({ completed: 0, total: files.length })
        setBatchStartTime(Date.now())
        // Initialize per-file status tracking immediately
        setFileStatuses(files.map((f, i) => ({
          filename: f.name,
          status: 'queued',
          index: i + 1,
        })))

        await analyzeBatchStream(
          files,
          jdMode === 'text' ? jdText : null,
          jdMode === 'file' ? jdFile : null,
          weights,
          {
            onProcessing: (index, total, filename) => {
              setIsAnalyzing(true)
              setAnalysisProgress(prev => ({ ...prev, total }))
              updateProgress(filename, 'processing')
              setFileStatuses(prev => prev.map(fs =>
                fs.filename === filename
                  ? { ...fs, status: 'processing', startTime: Date.now() }
                  : fs.status === 'queued' ? fs : fs
              ))
            },
            onResult: (index, total, filename, result, screeningResultId) => {
              setIsAnalyzing(true)
              setAnalysisProgress({ completed: index, total })
              setStreamingResults(prev => {
                const updated = [...prev, { filename, result, screeningResultId }]
                updated.sort((a, b) => (b.result?.fit_score || 0) - (a.result?.fit_score || 0))
                streamingResultsRef.current = updated
                return updated
              })
              setFileStatuses(prev => prev.map(fs =>
                fs.filename === filename
                  ? { ...fs, status: 'completed', result, screeningResultId, endTime: Date.now() }
                  : fs
              ))
              if (screeningResultId) {
                try {
                  if (isReportCacheable(result)) {
                    sessionStorage.setItem(`report_${screeningResultId}`, JSON.stringify(result))
                  }
                } catch {}
                trackEnrichmentJob({
                  id: `enrich-${screeningResultId}`,
                  label: filename,
                  status: 'processing',
                  phase: 'AI insights generating',
                  href: `/report?id=${screeningResultId}`,
                })
              }
              updateProgress(filename, 'completed')
              // Safety net: persist batch results on every new result
              try {
                const currentResults = streamingResultsRef.current
                const currentFailed = streamingFailedRef.current
                if (currentResults.length > 0) {
                  sessionStorage.setItem('aria_batch_results', JSON.stringify({
                    results: currentResults,
                    failed: currentFailed || [],
                    progress: { completed: index, total },
                    timestamp: Date.now()
                  }))
                }
              } catch {}
            },
            onFailed: (index, total, filename, error) => {
              setIsAnalyzing(true)
              setAnalysisProgress(prev => ({ completed: prev.completed, total: total || prev.total }))
              setStreamingFailed(prev => {
                const updated = [...prev, { filename, error }]
                streamingFailedRef.current = updated
                return updated
              })
              setFileStatuses(prev => prev.map(fs =>
                fs.filename === filename
                  ? { ...fs, status: 'failed', error, endTime: Date.now() }
                  : fs
              ))
              updateProgress(filename, 'error')
              try {
                const currentResults = streamingResultsRef.current
                const currentFailed = streamingFailedRef.current
                sessionStorage.setItem('aria_batch_results', JSON.stringify({
                  results: currentResults,
                  failed: currentFailed || [],
                  progress: { completed: index, total: total || 0 },
                  timestamp: Date.now()
                }))
              } catch {}
            },
            onDone: (total, successful, failedCount) => {
              setAnalysisDone(true)
              completeChecklistItem('analyzedResume')
              // Persist batch results for back-navigation (use refs to avoid stale closure)
              try {
                sessionStorage.setItem('aria_batch_results', JSON.stringify({
                  results: streamingResultsRef.current,
                  failed: streamingFailedRef.current,
                  progress: { completed: total, total },
                  timestamp: Date.now()
                }))
              } catch {}
              setIsAnalyzing(false)
              setAnalysisProgress({ completed: total, total })
              completeBatchAnalysis()
              showSuccess(`${successful} of ${total} resumes analyzed`)
            },
          },
          null,
          skillOverrides,
          activeReqId,
        )
      }

      // Clear draft
      localStorage.removeItem('aria_draft_jd')

      // Refresh subscription usage
      await refreshAfterAnalysis(files.length)

    } catch (err) {
      const detail = err.response?.data?.detail
      setError(
        (Array.isArray(detail) ? 'Validation error — check file types or JD format.' : detail) ||
        err.message ||
        'Analysis failed'
      )
      if (files.length > 1) {
        setBatchStuckError('Analysis failed before completing. You can retry or start a new batch.')
      }
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleRetryBatch = () => {
    setBatchStuckError(null)
    setError('')
    if (files.length > 0 && skillsConfirmed) {
      handleAnalyze()
      return
    }
    handleNewBatch()
  }

  const requisitionRequired = requiresRequisitionSelection(hasRequisitions, screeningMode)
  const showRequisitionFirst = hasRequisitions && !hasLoadedRequisition && !adHocMode
  const showLoadedRequisition = hasLoadedRequisition && !adHocMode
  const showAdHocInput = !hasRequisitions || adHocMode || (screeningMode === 'allow_ad_hoc' && !hasLoadedRequisition)
  const hasJdInput = jdMode === 'text' ? jdText.trim().length > 50 : jdFile !== null
  const isStep1Complete = showRequisitionFirst && requisitionRequired
    ? false
    : hasJdInput && skillsConfirmed && (!requisitionRequired || hasLoadedRequisition || adHocMode)
  const isStep2Complete = files.length > 0

  const remainingAnalyses = getRemainingAnalyses()

  // Detect batch start stuck state (no SSE progress after 15s) — batch only
  useEffect(() => {
    if (!isAnalyzing || analysisProgress.total > 0 || files.length <= 1) {
      return undefined
    }
    const timer = setTimeout(() => {
      setBatchStuckError('The analysis service did not respond. Check your connection and try again.')
      setIsAnalyzing(false)
    }, 15000)
    return () => clearTimeout(timer)
  }, [isAnalyzing, analysisProgress.total, files.length])

  // Determine if results area should be visible
  const showResults = isAnalyzing || analysisDone || streamingResults.length > 0 || streamingFailed.length > 0 || Boolean(batchStuckError)

  const activeStep = getActiveAnalyzeStep(showResults, currentStep)
  const setupSummary = buildSetupSummary({
    roleCategory,
    roleName,
    jdParseResult,
    skillOverrides,
    fileCount: getEffectiveBatchTotal(analysisProgress, fileStatuses),
    jdMode,
    jdFile,
  })
  const topCandidate = streamingResults[0]
  const batchPreparing = isAnalyzing && analysisProgress.total <= 0 && fileStatuses.length === 0

  // Reset for new batch
  const handleNewBatch = () => {
    setStreamingResults([])
    setStreamingFailed([])
    setAnalysisDone(false)
    setIsAnalyzing(false)
    setAnalysisProgress({ completed: 0, total: 0 })
    setFileStatuses([])
    setBatchStartTime(null)
    setBatchStuckError(null)
    setSetupSummaryExpanded(false)
    setFiles([])
    setCurrentStep(2)
    sessionStorage.removeItem('aria_batch_context')
    sessionStorage.removeItem('aria_batch_results')
  }


  return {
    hasRequisitions, remainingAnalyses, isStep1Complete, isStep2Complete, showResults,
    analysisDone, activeStep, isAnalyzing, setCurrentStep, draftSaved, error, setError,
    currentStep, screeningMode, showRequisitionFirst, adHocMode, enableAdHocMode,
    clearRequisitionSelection, requisitions, requisitionsLoading, requisitionSearch,
    setRequisitionSearch, handleLoadRequisition, navigate, requisitionRequired,
    showLoadedRequisition, loadedRequisition, intakeGateStatus, setShowJdModal,
    showAdHocInput, jdMode, setJdMode, hasLoadedRequisition, jdText, setJdText,
    skillsConfirmed, setSkillsConfirmed, setSkillOverrides, jdParseResult, setJdParseResult,
    showAiSuggestion, setShowAiSuggestion, roleName, setRoleName, roleNameTouchedRef,
    roleCategory, parsingJd, setParsingJd, parseError, setParseError, jdFile, setJdFile,
    getJdRootProps, getJdInputProps, isJdDragActive, urlInput, setUrlInput, handleExtractUrl,
    urlLoading, urlError, loadedRequisitionId, skillOverrides, queuedBatchInfo, setQueuedBatchInfo,
    files, getResumeRootProps, getResumeInputProps, isResumeDragActive, planBatchLimit, removeFile,
    weightPreset, weightsManuallySet, hasCustomWeights, showAdvanced, setShowAdvanced,
    handleWeightsAccepted, weights, handleWeightsChange, streamStage, singleFileName,
    runInBackground, setRunInBackground, handleAnalyze, setupSummary, setupSummaryExpanded,
    setSetupSummaryExpanded, handleNewBatch, topCandidate, streamingResults, streamingFailed,
    analysisProgress, fileStatuses, batchStartTime, batchPreparing, batchStuckError,
    handleRetryBatch, streamingResultsRef, streamingFailedRef, analyzeGuide, showJdModal,
  }
}
