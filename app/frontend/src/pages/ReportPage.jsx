import { useLocation, useNavigate, Link } from 'react-router-dom'
import { useEffect, useState, useRef, useMemo } from 'react'
import {
  ArrowLeft, CheckCircle, Check,
  ThumbsUp, ThumbsDown, Loader2, Pencil, X as XIcon, Upload, Eye, FileText, Clock,
  PhoneCall, Mic, ExternalLink, Sparkles, Calendar, BarChart2,
} from 'lucide-react'
import html2pdf from 'html2pdf.js'
import ScoreGauge from '../components/ScoreGauge'
import ResultCard from '../components/ResultCard'
import InterviewScorecard from '../components/InterviewScorecard'
import Timeline from '../components/Timeline'
import { labelTrainingExample, updateResultStatus, updateCandidateName, getCandidateAuditLog, viewCandidateResume, downloadCandidateResume, downloadPdfReport, getScreeningResult, getInterviewSessions, getInterviewScorecard, downloadAdverseAction, submitScoreFeedback } from '../lib/api'
import AnimatedScore from '../components/AnimatedScore'
import StreamingText from '../components/StreamingText'
import Skeleton from '../components/Skeleton'
import EvaluationChecklist from '../components/EvaluationChecklist'
import InterviewInitiateModal from '../components/InterviewInitiateModal'
import { EnrichmentBanner, ReportActionBar, AnalysisStageTracker, RescoreSheet, LiveScreenCallShell, LiveScreenKitReadinessGate, ConsolidatedScoreHero } from '../components/patterns'
import { useLiveScreenMode } from '../contexts/LiveScreenModeContext'
import { useEnrichmentPolling } from '../hooks/useEnrichmentPolling'
import { useNotification } from '../contexts/NotificationContext'
import { useOnboarding } from '../contexts/OnboardingContext'
import usePermissions from '../hooks/usePermissions'
import { useSubscription } from '../hooks/useSubscription'
import { ViewerReadOnlyBanner } from '../components/RequireWriteAccess'
import { showSuccess, showError } from '../lib/toast'
import { getFitTierLabel, getScoreHexColor } from '../lib/constants'
import { getKitReadiness, resolveInterviewKit } from '../lib/liveScreenKitUtils'
import { hasNarrativeContent, needsEnrichmentRefetch, isReportCacheable } from '../lib/enrichmentUtils'
import { INTERVIEW } from '../lib/uxLabels'
import api from '../lib/api'

/**
 * ResumeTextRenderer — formats raw resume plain-text into readable structured HTML.
 * Detects section headings, bullet points, dates, and blank-line paragraphs.
 */
function ResumeTextRenderer({ text }) {
  if (!text) return null

  // Patterns
  const SECTION_RE = /^([A-Z][A-Z\s&/\-]{2,40}):?\s*$/
  const BULLET_RE  = /^[\u2022\u2023\u25E6\u2043\u2219\-\*\+]\s+/
  const DATE_RE    = /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)[\s,]+\d{4}/i

  const lines = text.split('\n')
  const nodes = []
  let i = 0

  // First non-empty line is likely the candidate name
  let nameEmitted = false

  while (i < lines.length) {
    const raw = lines[i]
    const trimmed = raw.trim()

    if (!trimmed) {
      i++
      continue
    }

    // Candidate name — first meaningful line
    if (!nameEmitted) {
      nameEmitted = true
      nodes.push(
        <h1 key={`name-${i}`} className="text-lg font-bold text-slate-900 mb-0.5 leading-tight">
          {trimmed}
        </h1>
      )
      i++
      // Collect contact/meta lines right after name (short lines without bullets)
      const meta = []
      while (i < lines.length && lines[i].trim() && !SECTION_RE.test(lines[i].trim()) && lines[i].trim().length < 80) {
        meta.push(lines[i].trim())
        i++
      }
      if (meta.length > 0) {
        nodes.push(
          <p key={`meta-${i}`} className="text-xs text-slate-500 mb-3 leading-relaxed">
            {meta.join('  ·  ')}
          </p>
        )
      }
      continue
    }

    // Section heading (ALL CAPS line)
    if (SECTION_RE.test(trimmed)) {
      nodes.push(
        <div key={`sec-${i}`} className="mt-4 mb-1.5 pb-1 border-b border-slate-200">
          <span className="text-[10px] font-bold uppercase tracking-widest text-brand-600">
            {trimmed.replace(/:$/, '')}
          </span>
        </div>
      )
      i++
      continue
    }

    // Bullet point
    if (BULLET_RE.test(trimmed)) {
      const bullets = []
      while (i < lines.length && BULLET_RE.test(lines[i].trim())) {
        bullets.push(lines[i].trim().replace(BULLET_RE, ''))
        i++
      }
      nodes.push(
        <ul key={`ul-${i}`} className="mb-2 space-y-0.5">
          {bullets.map((b, bi) => (
            <li key={bi} className="flex items-start gap-2 text-xs text-slate-700 leading-relaxed">
              <span className="mt-1.5 w-1 h-1 rounded-full bg-brand-400 shrink-0" />
              {b}
            </li>
          ))}
        </ul>
      )
      continue
    }

    // Line with a date — treat as a job/education title row
    if (DATE_RE.test(trimmed)) {
      nodes.push(
        <div key={`date-${i}`} className="flex items-start justify-between gap-2 mb-0.5">
          <span className="text-xs font-semibold text-slate-700 leading-snug flex-1">{trimmed}</span>
        </div>
      )
      i++
      continue
    }

    // Short bold-looking line (likely a company/role name — non-caps, < 60 chars, no sentence punctuation)
    if (trimmed.length < 60 && !/[.?!;]$/.test(trimmed) && !/^[a-z]/.test(trimmed)) {
      nodes.push(
        <p key={`title-${i}`} className="text-xs font-semibold text-slate-800 leading-snug mb-0.5">
          {trimmed}
        </p>
      )
      i++
      continue
    }

    // Regular paragraph line
    nodes.push(
      <p key={`p-${i}`} className="text-xs text-slate-600 leading-relaxed mb-1">
        {trimmed}
      </p>
    )
    i++
  }

  return <div className="font-sans">{nodes}</div>
}

/** Coerce any value to a render-safe string. Objects become JSON; null/undefined → '' */
function safeStr(v) {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try { return JSON.stringify(v) } catch { return String(v) }
}

function InlineNameEditor({ initialName, candidateId, onSaved }) {
  const [editing, setEditing]   = useState(false)
  const [value, setValue]       = useState(initialName || '')
  const [saving, setSaving]     = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  const save = async () => {
    if (!value.trim()) { setEditing(false); return }
    setSaving(true)
    try {
      if (candidateId) await updateCandidateName(candidateId, value.trim())
      onSaved?.(value.trim())
    } catch { /* silent */ } finally {
      setSaving(false)
      setEditing(false)
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
          className="text-xl font-extrabold text-brand-900 tracking-tight bg-brand-50 border border-brand-300 rounded-xl px-3 py-1 w-full focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <button onClick={save} disabled={saving} className="p-1.5 rounded-lg bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
        </button>
        <button onClick={() => setEditing(false)} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100">
          <XIcon className="w-4 h-4" />
        </button>
      </div>
    )
  }

  const hasName = value && value.trim()

  return (
    <div className="flex items-center gap-2 group">
      {hasName ? (
        <h1 className="text-xl font-extrabold text-brand-900 tracking-tight">{value}</h1>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-xl font-extrabold text-brand-300 tracking-tight hover:text-brand-500 transition-colors italic"
          title="Click to add candidate name"
        >
          Add name…
        </button>
      )}
      {hasName && (
        <button
          onClick={() => setEditing(true)}
          className="opacity-0 group-hover:opacity-100 p-1 rounded-lg text-slate-400 hover:text-brand-600 hover:bg-brand-50 transition-all"
          title="Edit candidate name"
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  )
}

// ─── AI Interview Results Section (inline component) ──────────────────────
function RecruiterScorecardSection({ candidateId, sessions, scorecard, loading }) {
  if (loading) {
    return (
      <div className="rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <Mic className="w-5 h-5 text-neutral-400" />
          <h3 className="text-lg font-bold text-neutral-900">AI Interview Results</h3>
        </div>
        <div className="flex items-center justify-center py-6">
          <Loader2 className="w-5 h-5 text-brand-500 animate-spin" />
          <span className="ml-2 text-sm text-neutral-500">Loading interview data...</span>
        </div>
      </div>
    )
  }

  // No sessions — show CTA
  if (!sessions || sessions.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-neutral-300 bg-neutral-50 p-6 text-center">
        <div className="w-12 h-12 rounded-2xl bg-brand-50 flex items-center justify-center mx-auto mb-3">
          <Sparkles className="w-6 h-6 text-brand-600" />
        </div>
        <h4 className="text-sm font-bold text-neutral-900 mb-1">Start AI Interview</h4>
        <p className="text-xs text-neutral-500 mb-4 max-w-sm mx-auto">
          Schedule an AI-powered interview to get a comprehensive assessment with dimension scores and recommendations.
        </p>
        <Link
          to="/ai-interviews"
          className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700 transition-all duration-200 shadow-sm"
        >
          <Mic className="w-4 h-4" />
          Start AI Interview
        </Link>
      </div>
    )
  }

  // Has sessions — show scorecard summary
  const mostRecent = sessions[0]
  const isQuick = (mostRecent.interview_depth || 'quick') === 'quick'

  // Quick depth: scorecard is { session_id, depth: "quick", assessment: {...} }
  // Standard/Deep: scorecard is full RecruiterScorecardOut with dimension_scores
  const dimensionScores = isQuick
    ? (scorecard?.assessment?.dimensions || {})
    : (scorecard?.dimension_scores || scorecard?.dimensions || {})
  const recommendation = isQuick
    ? (scorecard?.assessment?.recommendation || '')
    : (scorecard?.recommendation || scorecard?.overall_recommendation || '')
  const overallScore = isQuick
    ? (scorecard?.assessment?.overall_score ?? null)
    : (scorecard?.overall_score ?? null)

  const statusColors = {
    completed: 'bg-green-50 text-green-700 ring-green-200',
    done: 'bg-green-50 text-green-700 ring-green-200',
    in_progress: 'bg-blue-50 text-blue-700 ring-blue-200',
    pending: 'bg-amber-50 text-amber-700 ring-amber-200',
    failed: 'bg-red-50 text-red-700 ring-red-200',
    cancelled: 'bg-red-50 text-red-700 ring-red-200',
  }

  const depthColors = {
    quick: 'bg-blue-50 text-blue-700 ring-blue-200',
    standard: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
    deep: 'bg-purple-50 text-purple-700 ring-purple-200',
  }
  const depthLabel = mostRecent.interview_depth || 'quick'

  return (
    <div className="rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-2">
          <Mic className="w-5 h-5 text-brand-600" />
          <h3 className="text-lg font-bold text-neutral-900">AI Interview Results</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ring-1 ${depthColors[depthLabel] || depthColors.quick}`}>
            {depthLabel.charAt(0).toUpperCase() + depthLabel.slice(1)}
          </span>
          <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ring-1 ${statusColors[mostRecent.status] || 'bg-neutral-50 text-neutral-600 ring-neutral-200'}`}>
            {mostRecent.status === 'in_progress' ? 'In Progress' :
             mostRecent.status === 'completed' || mostRecent.status === 'done' ? 'Completed' :
             mostRecent.status || 'Unknown'}
          </span>
        </div>
      </div>

      {/* Session meta */}
      <div className="flex items-center gap-4 text-xs text-neutral-500 mb-5">
        {mostRecent.created_at && (
          <span className="flex items-center gap-1">
            <Calendar className="w-3.5 h-3.5" />
            {new Date(mostRecent.created_at).toLocaleDateString()}
          </span>
        )}
        {mostRecent.duration_seconds && (
          <span className="flex items-center gap-1">
            <Clock className="w-3.5 h-3.5" />
            {Math.round(mostRecent.duration_seconds / 60)} min
          </span>
        )}
        {overallScore != null && (
          <span className="flex items-center gap-1">
            <BarChart2 className="w-3.5 h-3.5" />
            Score: {Math.round(overallScore)}/100
          </span>
        )}
      </div>

      {/* Quick depth: show assessment summary; Standard/Deep: show dimension scores */}
      {isQuick && scorecard?.assessment ? (
        <div className="space-y-3 mb-5">
          {scorecard.assessment.summary && (
            <div className="rounded-xl bg-neutral-50 p-3">
              <p className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-1">Assessment Summary</p>
              <p className="text-sm text-neutral-800 leading-relaxed">{safeStr(scorecard.assessment.summary)}</p>
            </div>
          )}
          {Object.keys(dimensionScores).length > 0 && (
            <div className="space-y-3">
              {Object.entries(dimensionScores).slice(0, 5).map(([dim, score]) => {
                const pct = typeof score === 'number' ? score : (score?.score ?? 0)
                const barColor = pct >= 70 ? 'bg-green-500' : pct >= 50 ? 'bg-amber-500' : pct >= 30 ? 'bg-orange-400' : 'bg-red-400'
                return (
                  <div key={dim}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-neutral-700 capitalize">{dim.replace(/_/g, ' ')}</span>
                      <span className="text-xs font-bold text-neutral-900">{Math.round(pct)}</span>
                    </div>
                    <div className="w-full h-2 bg-neutral-100 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      ) : (
        <>
          {/* Dimension scores — 5 mini bars */}
          {Object.keys(dimensionScores).length > 0 && (
            <div className="space-y-3 mb-5">
              {Object.entries(dimensionScores).slice(0, 5).map(([dim, score]) => {
                const pct = typeof score === 'number' ? score : (score?.score ?? 0)
                const barColor = pct >= 70 ? 'bg-green-500' : pct >= 50 ? 'bg-amber-500' : pct >= 30 ? 'bg-orange-400' : 'bg-red-400'
                return (
                  <div key={dim}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-neutral-700 capitalize">{dim.replace(/_/g, ' ')}</span>
                      <span className="text-xs font-bold text-neutral-900">{Math.round(pct)}</span>
                    </div>
                    <div className="w-full h-2 bg-neutral-100 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* Recommendation */}
      {recommendation && (
        <div className="rounded-xl bg-neutral-50 p-3 mb-5">
          <p className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-1">Recommendation</p>
          <p className="text-sm text-neutral-800 leading-relaxed">{recommendation}</p>
        </div>
      )}

      {/* Link to full interview */}
      <Link
        to={`/ai-interviews/${mostRecent.id}`}
        className="inline-flex items-center gap-2 text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors"
      >
        <ExternalLink className="w-4 h-4" />
        View Full Interview
      </Link>
    </div>
  )
}

export default function ReportPage() {
  const location = useLocation()
  const navigate  = useNavigate()
  const { completeChecklistItem } = useOnboarding()
  const { canWrite } = usePermissions()
  const { isFeatureAvailable } = useSubscription()
  const hasAiInterviews = isFeatureAvailable('ai_interviews')
  const [copied, setCopied]           = useState(false)
  const [result, setResult]           = useState(location.state?.result || null)
  const [labelStatus, setLabelStatus]   = useState(null)
  const [labelLoading, setLabelLoading] = useState(false)
  const [labelDone, setLabelDone] = useState(false)
  const [scoreFeedback, setScoreFeedback] = useState(null)
  const [scoreFeedbackLoading, setScoreFeedbackLoading] = useState(false)
  const [narrativePolling, setNarrativePolling] = useState(false)
  const [jdContext, setJdContext] = useState(null)
  const [resumeActionLoading, setResumeActionLoading] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [noResult, setNoResult] = useState(false)
  const [loading, setLoading] = useState(true)
  const [auditLogs, setAuditLogs] = useState([])
  const [auditExpanded, setAuditExpanded] = useState(false)

  // ── AI Screen Call modal state ───────────────────────────────────────────
  const [interviewModalOpen, setInterviewModalOpen] = useState(false)
  const [rescoreOpen, setRescoreOpen] = useState(false)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const { trackEnrichmentJob, updateEnrichmentJob, completeEnrichmentJob, addNotification } = useNotification()
  const enrichmentJobIdRef = useRef(null)

  // ── AI Recruiter sessions state ─────────────────────────────────────────
  const [recruiterSessions, setRecruiterSessions] = useState([])
  const [recruiterScorecard, setRecruiterScorecard] = useState(null)
  const [recruiterLoading, setRecruiterLoading] = useState(false)

  // ── Phone Screen split-view state ─────────────────────────────────────────
  const [screenMode, setScreenMode] = useState(false)
  const [readinessGateOpen, setReadinessGateOpen] = useState(false)
  const [forceFallbackKit, setForceFallbackKit] = useState(false)
  const { setActive: setLiveScreenActive } = useLiveScreenMode()
  const [scorecardKey, setScorecardKey] = useState(0)
  const [resumeBlobUrl, setResumeBlobUrl] = useState(null)
  const [resumeLoading, setResumeLoading] = useState(false)
  const [resumeIsText, setResumeIsText] = useState(false)
  const [resumeText, setResumeText] = useState('')

  /** Resolve name from all possible result paths — returns null if unknown */
  const resolveName = (r) =>
    (r?.candidate_name || '').trim() ||
    (r?.contact_info?.name || '').trim() ||
    (r?.candidate_profile?.name || '').trim() ||
    null

  // Initialize candidate name from result
  const [candidateName, setCandidateName] = useState(resolveName(location.state?.result))

  // Load active JD context from sessionStorage
  useEffect(() => {
    const raw = sessionStorage.getItem('aria_active_jd')
    if (raw) {
      try {
        setJdContext(JSON.parse(raw))
      } catch { /* ignore */ }
    }
  }, [])

  useEffect(() => {
    const applyFetchedResult = (data) => {
      setResult(data)
      setCandidateName(resolveName(data))
      try {
        if (isReportCacheable(data)) {
          const rid = data?.result_id || data?.id
          if (rid) sessionStorage.setItem(`report_${rid}`, JSON.stringify(data))
        }
      } catch { /* ignore */ }
    }

    // Check if result from location.state is a COMPLETE result (not just a partial card summary)
    const isCompleteResult = result && (
      result.analysis_result ||
      result.strengths !== undefined ||
      result.contact_info ||
      result.candidate_profile ||
      result.score_breakdown
    )

    const params = new URLSearchParams(location.search)
    const urlId = params.get('id')
    const currentId = result?.id ?? result?.result_id
    const id = urlId || currentId

    if (urlId && currentId != null && String(urlId) !== String(currentId)) {
      setLoading(true)
      getScreeningResult(urlId)
        .then(applyFetchedResult)
        .catch(() => {
          setNoResult(true)
        })
        .finally(() => {
          setLoading(false)
        })
      return
    }

    if (isCompleteResult) {
      setCandidateName(resolveName(result))
      setLoading(false)
      if (id && needsEnrichmentRefetch(result)) {
        getScreeningResult(id)
          .then(applyFetchedResult)
          .catch(() => {})
      }
      return
    }

    // Extract ID from either the partial result or URL params
    if (!id) {
      setNoResult(true)
      setLoading(false)
      return
    }

    // Try sessionStorage first — refetch if narrative or kit is not fully hydrated
    try {
      const stored = sessionStorage.getItem(`report_${id}`)
      if (stored) {
        const parsed = JSON.parse(stored)
        setResult(parsed)
        setCandidateName(resolveName(parsed))
        setLoading(false)
        if (needsEnrichmentRefetch(parsed)) {
          getScreeningResult(id)
            .then(applyFetchedResult)
            .catch(() => {})
        }
        return
      }
    } catch { /* ignore */ }

    // Fetch full data from API
    setLoading(true)
    getScreeningResult(id)
      .then(applyFetchedResult)
      .catch(() => {
        setNoResult(true)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [location.search])

  // Fetch audit log when candidate_id is available
  useEffect(() => {
    const cid = result?.candidate_id
    if (!cid) return
    getCandidateAuditLog(cid)
      .then(setAuditLogs)
      .catch(() => setAuditLogs([]))
  }, [result?.candidate_id])

  // Fetch AI Interview sessions for this candidate (unified API)
  useEffect(() => {
    const cid = result?.candidate_id
    if (!cid) return
    setRecruiterLoading(true)
    getInterviewSessions({ candidate_id: cid })
      .then(async (data) => {
        const list = Array.isArray(data) ? data : (data?.sessions || [])
        setRecruiterSessions(list)
        // Find most recent completed session and load its scorecard
        const completed = list.find(s => s.status === 'completed' || s.status === 'done')
        if (completed?.id) {
          try {
            const sc = await getInterviewScorecard(completed.id)
            setRecruiterScorecard(sc)
          } catch { setRecruiterScorecard(null) }
        }
      })
      .catch(() => { setRecruiterSessions([]); setRecruiterScorecard(null) })
      .finally(() => setRecruiterLoading(false))
  }, [result?.candidate_id])

  // Poll for narrative + kit via shared hook
  useEnrichmentPolling(result, (updater) => {
    setResult((prev) => {
      const next = updater(prev)
      const rid = next?.result_id || next?.id
      if (rid && isReportCacheable(next)) {
        try { sessionStorage.setItem(`report_${rid}`, JSON.stringify(next)) } catch {}
      }
      return next
    })
  }, {
    onKitReady: () => {
      addNotification({
        type: 'success',
        title: 'Interview kit ready',
        message: `${candidateName || 'Candidate'} — screen questions are ready`,
        href: `/report?id=${result?.result_id || result?.id}`,
      })
      if (enrichmentJobIdRef.current) {
        completeEnrichmentJob(enrichmentJobIdRef.current, { phase: 'Complete', status: 'ready' })
      }
      const rid = result?.result_id || result?.id
      if (rid) {
        getScreeningResult(rid)
          .then((data) => {
            setResult(data)
            setCandidateName(resolveName(data))
            try {
              if (isReportCacheable(data)) {
                sessionStorage.setItem(`report_${rid}`, JSON.stringify(data))
              }
            } catch { /* ignore */ }
          })
          .catch(() => {})
      }
    },
    onComplete: () => setNarrativePolling(false),
  })

  useEffect(() => {
    const rid = result?.result_id || result?.analysis_id
    if (!rid) return
    if (result.narrative_status === 'pending' || result.narrative_status === 'processing') {
      setNarrativePolling(true)
      const jobId = `enrich-${rid}`
      enrichmentJobIdRef.current = jobId
      trackEnrichmentJob({
        id: jobId,
        label: candidateName || `Report #${rid}`,
        status: 'processing',
        phase: 'AI enrichment',
        href: `/report?id=${rid}`,
      })
    }
  }, [result?.result_id, result?.analysis_id, result?.narrative_status, candidateName, trackEnrichmentJob])

  useEffect(() => {
    if (!result || !enrichmentJobIdRef.current) return
    updateEnrichmentJob(enrichmentJobIdRef.current, {
      phase: result.interview_kit_status === 'processing'
        ? 'Interview kit generating'
        : result.narrative_status === 'processing'
          ? 'AI insights generating'
          : result.voice_strategy_status === 'processing'
            ? 'Voice plan building'
            : 'Enriching',
      status: ['ready', 'fallback', 'skipped'].includes(result.interview_kit_status) &&
        !['pending', 'processing'].includes(result.narrative_status || '')
        ? 'ready'
        : 'processing',
    })
  }, [result?.narrative_status, result?.interview_kit_status, result?.voice_strategy_status, updateEnrichmentJob])

  const handleAdverseAction = async () => {
    const resultId = result?.result_id || result?.id
    if (!resultId) return
    try {
      const response = await downloadAdverseAction(resultId)
      const blob = new Blob([response.data], { type: 'application/pdf' })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${candidateName || 'Candidate'}_Adverse_Action.pdf`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
      showSuccess('Adverse action letter downloaded')
    } catch {
      showSuccess('Could not download adverse action letter')
    }
  }

  // Load resume blob when entering screen mode
  useEffect(() => {
    if (!screenMode || !result?.candidate_id) return
    if (resumeBlobUrl || resumeIsText) return // already loaded
    let objectUrl = null
    const loadResume = async () => {
      setResumeLoading(true)
      try {
        const resp = await api.get(`/candidates/${result.candidate_id}/resume?inline=true`, { responseType: 'blob' })
        const blob = resp.data
        const contentType = blob.type || resp.headers['content-type'] || ''
        if (contentType.includes('application/pdf')) {
          objectUrl = URL.createObjectURL(blob)
          setResumeBlobUrl(objectUrl)
          setResumeIsText(false)
        } else if (contentType.startsWith('text/')) {
          const text = await blob.text()
          setResumeText(text)
          setResumeIsText(true)
        } else {
          // Fallback: use raw_resume_text from result data
          setResumeText(result?.raw_resume_text || result?.resume_text || 'Resume preview not available for this file format.')
          setResumeIsText(true)
        }
      } catch (e) {
        console.warn('PhoneScreen: failed to load resume blob', e)
      } finally {
        setResumeLoading(false)
      }
    }
    loadResume()
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [screenMode, result?.candidate_id])

  // Revoke blob URL when exiting screen mode
  useEffect(() => {
    if (!screenMode) {
      if (resumeBlobUrl) {
        URL.revokeObjectURL(resumeBlobUrl)
        setResumeBlobUrl(null)
      }
      setResumeIsText(false)
      setResumeText('')
    }
  }, [screenMode])

  // Kit readiness hooks must run before any early return (Rules of Hooks)
  const interviewQsForKit = useMemo(
    () => result?.interview_questions || result?.analysis_result?.interview_questions || null,
    [result],
  )

  const analysisData = useMemo(() => ({
    missing_skills: result?.analysis_result?.missing_skills || result?.missing_skills || [],
    matched_skills: result?.analysis_result?.matched_skills || result?.matched_skills || [],
    work_experience: result?.work_experience || result?.candidate_profile?.work_experience || [],
    candidate_profile: result?.candidate_profile,
  }), [result])

  const roleForKit = useMemo(() => {
    const r = result?.jd_name || result?.job_role
    return (r && r !== 'Not specified') ? safeStr(r) : ''
  }, [result?.jd_name, result?.job_role])

  const roleDomain = useMemo(() => {
    const d = result?.analysis_result?.jd_analysis?.domain || result?.jd_analysis?.domain
    return d && d !== 'other' && d !== 'general' ? safeStr(d) : ''
  }, [result])

  const kitReadiness = useMemo(
    () => getKitReadiness(
      result?.interview_kit_status,
      interviewQsForKit,
      analysisData,
      roleForKit,
    ),
    [result?.interview_kit_status, interviewQsForKit, analysisData, roleForKit],
  )

  const activeKit = useMemo(() => {
    if (forceFallbackKit) {
      return resolveInterviewKit(null, analysisData, roleForKit)
    }
    if (kitReadiness.kit) {
      return {
        kit: kitReadiness.kit,
        isFallback: kitReadiness.isFallback,
        totalQ: kitReadiness.totalQ,
      }
    }
    return resolveInterviewKit(
      interviewQsForKit,
      analysisData,
      roleForKit,
      result?.interview_kit_status,
    )
  }, [forceFallbackKit, kitReadiness, interviewQsForKit, analysisData, roleForKit, result?.interview_kit_status])

  useEffect(() => {
    setLiveScreenActive(screenMode)
    return () => setLiveScreenActive(false)
  }, [screenMode, setLiveScreenActive])

  if (!result) {
    if (loading) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] text-brand-700">
          <Loader2 className="w-8 h-8 animate-spin mb-3" />
          <p className="text-sm font-medium">Loading report…</p>
        </div>
      )
    }
    if (noResult) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] text-slate-500">
          <p className="text-lg font-medium mb-2">No analysis results found</p>
          <p className="text-sm mb-4">The analysis may have expired or was not completed.</p>
          <button onClick={() => navigate('/analyze')} className="px-4 py-2 bg-brand-500 text-white rounded-lg hover:bg-brand-600">
            Start New Analysis
          </button>
        </div>
      )
    }
    return null
  }

  const hasDeterministicData = result.fit_score != null
  const isNarrativeReady = result.narrative_status === 'ready'
  const isReportComplete = isNarrativeReady || result.narrative_status === 'fallback' || result.narrative_status === 'failed' || !result.narrative_status

  const role      = roleForKit
  const timestamp = result.analyzed_at
    ? new Date(result.analyzed_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
    : new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })

  const handleShare = () => {
    const resultId = result?.result_id || result?.id
    let shareUrl
    if (resultId) {
      // Permanent per-candidate URL — works for anyone with app access
      shareUrl = `${window.location.origin}/report?id=${resultId}`
    } else {
      // Fallback for unsaved results — session-only
      const id = crypto.randomUUID()
      sessionStorage.setItem(`report_${id}`, JSON.stringify(result))
      shareUrl = `${window.location.origin}/report?id=${id}`
    }
    navigator.clipboard.writeText(shareUrl).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    }).catch(() => window.prompt('Copy this report link:', shareUrl))
  }

  const fallbackClientSidePdf = async () => {
    if (isDownloading) return

    setIsDownloading(true)
    try {
      // Get the report content element
      const element = document.querySelector('.report-content')
      if (!element) {
        showError('Report content not found. Please try again.')
        setIsDownloading(false)
        return
      }

      // PDF options
      const opt = {
        margin: [10, 10, 10, 10],
        filename: `${candidateName || 'Candidate'}_Screening_Report.pdf`,
        image: { type: 'jpeg', quality: 0.98 },
        html2canvas: {
          scale: 2,
          useCORS: true,
          logging: false,
          letterRendering: true
        },
        jsPDF: {
          unit: 'mm',
          format: 'a4',
          orientation: 'portrait'
        },
        pagebreak: { mode: ['avoid-all', 'css', 'legacy'] }
      }

      // Generate and download PDF
      await html2pdf().set(opt).from(element).save()
    } catch (error) {
      console.error('PDF generation error:', error)
      showError('Failed to generate PDF. Please try again or use the browser print option (Ctrl+P).')
    } finally {
      setIsDownloading(false)
    }
  }

  const handleDownload = async () => {
    if (isDownloading) return

    const resultId = result?.result_id || result?.id || new URLSearchParams(location.search).get('id')
    if (!resultId) {
      fallbackClientSidePdf()
      return
    }

    setIsDownloading(true)
    try {
      const response = await downloadPdfReport(resultId)
      const blob = new Blob([response.data], { type: 'application/pdf' })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${candidateName || 'Candidate'}_Assessment_Report.pdf`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    } catch (error) {
      console.error('Server PDF generation failed, falling back to client-side:', error)
      fallbackClientSidePdf()
    } finally {
      setIsDownloading(false)
    }
  }

  const handleLabel = async (outcome) => {
    const resultId = result?.result_id
    if (!resultId || labelLoading) return
    setLabelLoading(true)
    try {
      await labelTrainingExample(resultId, outcome)
      await updateResultStatus(resultId, outcome)
      setLabelStatus(outcome)
      setLabelDone(true)
    } catch { /* silently fail */ } finally {
      setLabelLoading(false)
    }
  }

  // ── Phone Screen split-view layout ────────────────────────────────────────
  const interviewQs = interviewQsForKit
  const fitScore = result?.fit_score ?? null
  const fitTier = getFitTierLabel(fitScore)

  const handleStartLiveScreen = () => {
    if (kitReadiness.state === 'loading' || kitReadiness.state === 'empty') {
      setReadinessGateOpen(true)
      return
    }
    setForceFallbackKit(kitReadiness.state === 'fallback')
    setScreenMode(true)
  }

  if (screenMode) {
    return (
      <LiveScreenCallShell
        candidateName={candidateName}
        roleTitle={role ? safeStr(role) : ''}
        roleSubtitle={roleDomain}
        fitScore={fitScore}
        result={result}
        kit={activeKit.kit}
        kitIsFallback={activeKit.isFallback}
        resumeLoading={resumeLoading}
        resumeIsText={resumeIsText}
        resumeText={resumeText}
        resumeBlobUrl={resumeBlobUrl}
        ResumeTextRenderer={ResumeTextRenderer}
        onExit={() => {
          setScreenMode(false)
          setForceFallbackKit(false)
        }}
        onDebriefGenerated={async (debrief) => {
          setScorecardKey((prev) => prev + 1)
          const rid = result?.result_id
          if (!rid) return
          try {
            const data = await getScreeningResult(rid)
            setResult((prev) => ({ ...prev, ...data }))
          } catch { /* outcome refresh optional */ }
          completeChecklistItem('sharedWithHM')
        }}
      />
    )
  }

  return (
    <div className="flex h-full min-h-0 print:block text-slate-900 dark:text-slate-100">

      {/* ── Left sidebar: score + candidate info + labels ── */}
        <aside className="w-72 shrink-0 border-r border-brand-100 dark:border-slate-700 overflow-y-auto flex flex-col gap-5 p-5 print:hidden">

        {/* Back button */}
        <button
          onClick={() => {
            const searchParams = new URLSearchParams(location.search)
            const fromParam = searchParams.get('from')
            const destination = location.state?.from ||
              (fromParam === 'batch' ? '/analyze' :
               fromParam === 'analyze' ? '/analyze' : null)

            if (destination) {
              if (destination === '/analyze') {
                navigate('/analyze?restored=true', { replace: false })
              } else {
                navigate(destination, { state: { from: '/analyze' } })
              }
            } else {
              window.history.length > 1 ? navigate(-1) : navigate('/')
            }
          }}
          className="flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-brand-700 transition-colors self-start"
        >
          <ArrowLeft className="w-4 h-4" />
          {(() => {
            const searchParams = new URLSearchParams(location.search)
            const fromParam = searchParams.get('from')
            return location.state?.from === '/batch' || fromParam === 'batch'
              ? 'Back to Batch Results'
              : 'Back to Results'
          })()}
        </button>

        {/* Report badge + candidate name */}
        <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-4">
          <span className="inline-block text-xs font-bold text-brand-600 uppercase tracking-widest mb-2 bg-brand-50 px-2.5 py-1 rounded-full ring-1 ring-brand-200">
            Screening Report
          </span>
          <InlineNameEditor
            initialName={candidateName}
            candidateId={result.candidate_id}
            onSaved={(newName) => {
              setCandidateName(newName)
              setResult(prev => ({
                ...prev,
                analysis_result: { ...prev?.analysis_result, candidate_name: newName },
                candidate_name: newName,
                contact_info: { ...prev?.contact_info, name: newName },
                candidate_profile: { ...prev?.candidate_profile, name: newName },
              }))
              // Re-fetch audit log to include the new edit
              if (result.candidate_id) {
                getCandidateAuditLog(result.candidate_id).then(setAuditLogs).catch(() => {})
              }
              // Re-fetch full result so updated fit_summary / narrative is reflected
              const rid = result?.result_id || result?.id
              if (rid) {
                getScreeningResult(rid)
                  .then(data => {
                    setResult(data)
                    setCandidateName(resolveName(data))
                    try { sessionStorage.setItem(`report_${rid}`, JSON.stringify(data)) } catch {}
                  })
                  .catch(() => {})
              }
            }}
          />
          {/* Audit trail: "Last edited" indicator */}
          {auditLogs.length > 0 && (
            <div className="mt-1">
              <button
                onClick={() => setAuditExpanded(!auditExpanded)}
                className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-brand-600 transition-colors"
              >
                <Clock className="w-3 h-3" />
                Last edited {new Date(auditLogs[0].changed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                by {auditLogs[0].changed_by_email?.split('@')[0] || 'user'}
              </button>
              {auditExpanded && (
                <div className="mt-2 space-y-1.5 max-h-40 overflow-y-auto">
                  {auditLogs.map((log) => (
                    <div key={log.id} className="text-[10px] text-slate-400 bg-slate-50 rounded-lg px-2 py-1.5 ring-1 ring-slate-100">
                      <span className="font-semibold text-slate-600">{log.field_name}</span>
                      {': '}
                      <span className="line-through">{log.old_value || '(empty)'}</span>
                      {' → '}
                      <span className="text-brand-700 font-medium">{log.new_value || '(empty)'}</span>
                      <div className="mt-0.5 text-slate-400">
                        {log.changed_by_email?.split('@')[0] || 'User'} · {new Date(log.changed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {role && <p className="text-slate-500 text-xs mt-1 font-medium">{safeStr(role)}</p>}
          <div className="mt-2 border-t border-brand-50 pt-2">
            <p className="text-xs text-slate-400">Analyzed on</p>
            <p className="text-xs font-bold text-brand-900">{timestamp}</p>
            <p className="text-xs text-slate-400 mt-0.5">Powered by ARIA · ThetaLogics</p>
          </div>
        </div>

        <AnalysisStageTracker result={result} className="mb-1" />

        {result?.parse_confidence && (
          <div className={`rounded-2xl ring-1 px-4 py-3 text-sm ${
            result.parse_confidence.level === 'high'
              ? 'bg-emerald-50 ring-emerald-200 text-emerald-800'
              : result.parse_confidence.level === 'medium'
                ? 'bg-amber-50 ring-amber-200 text-amber-800'
                : 'bg-red-50 ring-red-200 text-red-800'
          }`}>
            <p className="font-semibold">
              Parse confidence: {result.parse_confidence.level} ({Math.round((result.parse_confidence.overall || 0) * 100)}%)
            </p>
            <p className="text-xs mt-1 opacity-90">{result.parse_confidence.evidence_hint}</p>
          </div>
        )}

        {(result?.skill_evidence || result?.analysis_result?.skill_evidence)?.length > 0 && (
          <div className="rounded-2xl ring-1 ring-brand-100 bg-white/90 px-4 py-3">
            <p className="text-xs font-bold text-brand-700 uppercase tracking-wider mb-2">Skill evidence</p>
            <ul className="space-y-2 text-sm">
              {(result.skill_evidence || result.analysis_result?.skill_evidence || []).slice(0, 8).map((item) => (
                <li key={item.skill} className="border-l-2 border-brand-300 pl-3">
                  <span className="font-semibold text-brand-900">{item.skill}</span>
                  <p className="text-slate-600 text-xs mt-0.5">{item.snippet}</p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Score gauge */}
        {hasDeterministicData && (
          <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-4 flex flex-col items-center">
            <ScoreGauge score={result.fit_score} />
            {role && (
              <p className="mt-2 text-xs font-semibold text-brand-800 text-center truncate w-full" title={role}>
                {role}{roleDomain ? ` · ${roleDomain}` : ''}
              </p>
            )}
            {result.final_recommendation && (
              <div className="mt-3 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-brand-50 ring-1 ring-brand-200 text-brand-700 text-xs font-bold">
                <CheckCircle className="w-3.5 h-3.5" />
                {safeStr(result.effective_recommendation || result.final_recommendation)}
                {(result.override_indicator || (result.ai_recommendation && result.ai_recommendation !== result.effective_recommendation)) ? (
                  <span className="ml-1 font-semibold">AI: {safeStr(result.ai_recommendation || result.final_recommendation)}</span>
                ) : null}
              </div>
            )}
          </div>
        )}

        {/* Score accuracy feedback */}
        {result.result_id && canWrite && (
          <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-4">
            <p className="text-xs font-bold text-brand-900 mb-0.5">Fit score accuracy</p>
            <p className="text-xs text-slate-400 mb-3">Help calibrate scoring for this role.</p>
            {(scoreFeedback || result?.analysis_result?.score_feedback?.sentiment) ? (
              <p className="text-xs font-semibold text-emerald-700 text-center py-2">
                Thanks — score marked as{' '}
                {(scoreFeedback || result.analysis_result.score_feedback.sentiment).replace('_', ' ')}
              </p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {[
                  { id: 'right', label: 'Feels right' },
                  { id: 'high', label: 'Too high' },
                  { id: 'low', label: 'Too low' },
                ].map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    disabled={scoreFeedbackLoading}
                    onClick={async () => {
                      setScoreFeedbackLoading(true)
                      try {
                        await submitScoreFeedback(result.result_id, opt.id)
                        setScoreFeedback(opt.id)
                        showSuccess('Score feedback saved')
                      } catch {
                        showError('Could not save feedback')
                      }
                      setScoreFeedbackLoading(false)
                    }}
                    className="px-3 py-2 rounded-xl text-xs font-semibold ring-1 ring-brand-200 text-brand-700 hover:bg-brand-50 disabled:opacity-50"
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Training label */}
        {result.result_id && canWrite && (
          <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-4">
            <p className="text-xs font-bold text-brand-900 mb-0.5">Help ARIA Learn</p>
            <p className="text-xs text-slate-400 mb-3">Label this outcome to improve accuracy.</p>
            {labelDone ? (
              <div className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-bold ring-1 justify-center ${
                labelStatus === 'hired'
                  ? 'bg-green-100 text-green-700 ring-green-200'
                  : 'bg-red-100 text-red-700 ring-red-200'
              }`}>
                <Check className="w-3.5 h-3.5" />
                Marked as {labelStatus === 'hired' ? 'Hired' : 'Rejected'}
              </div>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={() => handleLabel('hired')}
                  disabled={labelLoading}
                  className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-green-50 ring-1 ring-green-200 text-green-700 text-xs font-bold hover:bg-green-100 disabled:opacity-50 transition-colors"
                >
                  {labelLoading && labelStatus !== 'rejected'
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <ThumbsUp className="w-3.5 h-3.5" />}
                  Hired
                </button>
                <button
                  onClick={() => handleLabel('rejected')}
                  disabled={labelLoading}
                  className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-red-50 ring-1 ring-red-200 text-red-700 text-xs font-bold hover:bg-red-100 disabled:opacity-50 transition-colors"
                >
                  {labelLoading && labelStatus !== 'hired'
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <ThumbsDown className="w-3.5 h-3.5" />}
                  Rejected
                </button>
              </div>
            )}
          </div>
        )}

        {/* Analyze Another Resume */}
        {jdContext && canWrite && (
          <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-4">
            <p className="text-xs font-bold text-brand-900 mb-0.5">Same Job, New Candidate?</p>
            <p className="text-xs text-slate-400 mb-3">
              {jdContext.jd_mode === 'file'
                ? `Analyze another resume using the same JD file (${jdContext.file_name}).`
                : 'Analyze another resume using the same JD and weights.'}
            </p>
            <button
              onClick={() => {
                const saved = JSON.parse(sessionStorage.getItem('aria_active_jd') || '{}')
                sessionStorage.setItem('aria_analyze_another', 'true')
                navigate('/analyze', {
                  state: {
                    ...jdContext,
                    skillOverrides: jdContext?.skillOverrides || saved.skillOverrides,
                    jdParseResult: jdContext?.jdParseResult || saved.jdParseResult,
                    skillsConfirmed: jdContext?.skillsConfirmed ?? false
                  }
                })
              }}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-brand-50 ring-1 ring-brand-200 text-brand-700 text-xs font-bold hover:bg-brand-100 transition-colors"
            >
              <Upload className="w-3.5 h-3.5" />
              Analyze Another Resume
            </button>
          </div>
        )}
      </aside>

      {/* ── Right panel: action bar + scrollable content ── */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">

        {/* Report actions */}
        <div className="bg-white/80 backdrop-blur-xl border-b border-brand-100/60 shrink-0 z-10 print:hidden px-6 py-3">
          <ReportActionBar
            result={result}
            onAiScreenCall={canWrite && hasAiInterviews && result?.candidate_id ? () => setInterviewModalOpen(true) : undefined}
            onLiveScreenKit={canWrite && hasAiInterviews && hasDeterministicData ? handleStartLiveScreen : undefined}
            onViewResume={result?.candidate_id ? async () => {
              setResumeActionLoading(true)
              try { await viewCandidateResume(result.candidate_id) } catch { showError('Resume not available') }
              finally { setResumeActionLoading(false) }
            } : undefined}
            onDownloadResume={result?.candidate_id ? async () => {
              setResumeActionLoading(true)
              try {
                await downloadCandidateResume(
                  result.candidate_id,
                  result?.candidate_name ? `${result.candidate_name}_resume.pdf` : `resume_${result.candidate_id}.pdf`
                )
              } catch { showError('Resume not available') }
              finally { setResumeActionLoading(false) }
            } : undefined}
            onDownloadPdf={handleDownload}
            onDownloadAdverseAction={handleAdverseAction}
            onShare={handleShare}
            onRescore={canWrite ? () => setRescoreOpen(true) : undefined}
            isDownloading={isDownloading}
            resumeLoading={resumeActionLoading}
            copied={copied}
            actionsReady={isReportComplete}
            liveScreenKitAvailable={hasDeterministicData}
            kitReadiness={kitReadiness}
          />
        </div>

        {/* Scrollable result content */}
        <div className="report-content flex-1 overflow-y-auto p-6 space-y-5 print:overflow-visible print:p-4">
          {!bannerDismissed && (
            <EnrichmentBanner result={result} onDismiss={() => setBannerDismissed(true)} />
          )}

          {hasDeterministicData && (
            <ConsolidatedScoreHero
              analysisScore={result?.deterministic_score ?? result?.fit_score}
              callScore={result?.call_fit_score}
              callSource={result?.call_source}
              consolidatedRecommendation={result?.consolidated_recommendation}
              consolidatedReasoning={result?.consolidated_reasoning}
            />
          )}

          {/* Score Summary — resume-only view; hero covers screened candidates */}
          {!hasDeterministicData ? (
            <Skeleton variant="card" />
          ) : result?.call_fit_score == null ? (
            <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-5 flex items-center gap-6">
              <div className="flex flex-col items-center justify-center w-24 h-24 rounded-full bg-brand-50 ring-4 ring-brand-100 shrink-0">
                <AnimatedScore score={result.fit_score} size="lg" animate={!isReportComplete} />
                <span className="text-[10px] font-bold text-brand-400 uppercase tracking-wider">/ 100</span>
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="px-3 py-1 rounded-full text-xs font-bold text-white"
                    style={{ backgroundColor: getScoreHexColor(result.fit_score) }}
                  >
                    {fitTier.label}
                  </span>
                  {result.final_recommendation && (
                    <span className="px-3 py-1 rounded-full text-xs font-bold bg-brand-100 text-brand-700 ring-1 ring-brand-200">
                      {safeStr(result.final_recommendation)}
                    </span>
                  )}
                  {result.risk_level && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold ${
                      result.risk_level === 'Low' ? 'bg-green-100 text-green-700' :
                      result.risk_level === 'Medium' ? 'bg-amber-100 text-amber-700' :
                      'bg-red-100 text-red-700'
                    }`}>
                      {safeStr(result.risk_level)} Risk
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-500">Overall fit score based on skills, experience, education, and timeline analysis.</p>
              </div>
            </div>
          ) : (
            <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-5 flex flex-wrap items-center gap-2">
              {result.final_recommendation && (
                <span className="px-3 py-1 rounded-full text-xs font-bold bg-brand-100 text-brand-700 ring-1 ring-brand-200">
                  {safeStr(result.final_recommendation)}
                </span>
              )}
              {result.risk_level && (
                <span className={`px-3 py-1 rounded-full text-xs font-bold ${
                  result.risk_level === 'Low' ? 'bg-green-100 text-green-700' :
                  result.risk_level === 'Medium' ? 'bg-amber-100 text-amber-700' :
                  'bg-red-100 text-red-700'
                }`}>
                  {safeStr(result.risk_level)} Risk
                </span>
              )}
            </div>
          )}

          {/* Loading skeletons for narrative and skills areas */}
          {!hasDeterministicData && (
            <div className="space-y-5">
              <Skeleton variant="text" count={4} />
              <div className="space-y-3">
                <Skeleton variant="bar" count={5} />
              </div>
            </div>
          )}

          {/* Candidate Information Section - Visible in both screen and print */}
          <div className="mb-6 pb-4 border-b-2 border-brand-200 bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand-sm p-5 print:shadow-none print:ring-0 print:bg-transparent print:rounded-none">
            <div className="flex items-start justify-between mb-3">
              <div>
                <span className="text-xs font-bold text-brand-600 uppercase tracking-widest">Screening Report</span>
                <h1 className="text-2xl font-extrabold text-brand-900 mt-1">{safeStr(candidateName) || 'Unknown Candidate'}</h1>
                {role && <p className="text-base font-semibold text-slate-700 mt-1">Position: {safeStr(role)}</p>}
              </div>
              <div className="text-right text-sm">
                <p className="text-slate-400">Analyzed on {timestamp}</p>
                <p className="text-xs text-slate-400 mt-1">Powered by ARIA · ThetaLogics</p>
              </div>
            </div>
            {/* Candidate Contact Information */}
            {(result?.contact_info?.email || result?.contact_info?.phone) && (
              <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-brand-100">
                {result?.contact_info?.email && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Email</span>
                    <p className="text-sm text-slate-700 font-medium">{safeStr(result.contact_info.email)}</p>
                  </div>
                )}
                {result?.contact_info?.phone && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Phone</span>
                    <p className="text-sm text-slate-700 font-medium">{safeStr(result.contact_info.phone)}</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Print-only header with candidate information (DEPRECATED - keeping for backward compatibility) */}
          <div className="hidden print:hidden mb-6 pb-4 border-b-2 border-brand-200">
            <div className="flex items-start justify-between mb-3">
              <div>
                <span className="text-xs font-bold text-brand-600 uppercase tracking-widest">Screening Report</span>
                <h1 className="text-2xl font-extrabold text-brand-900 mt-1">{safeStr(candidateName) || 'Unknown Candidate'}</h1>
                {role && <p className="text-base font-semibold text-slate-700 mt-1">Position: {safeStr(role)}</p>}
              </div>
              <div className="text-right text-sm">
                <p className="text-slate-400">Analyzed on {timestamp}</p>
                <p className="text-xs text-slate-400 mt-1">Powered by ARIA · ThetaLogics</p>
              </div>
            </div>
            {/* Candidate Contact Information */}
            {(result?.contact_info?.email || result?.contact_info?.phone) && (
              <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-brand-100">
                {result?.contact_info?.email && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Email</span>
                    <p className="text-sm text-slate-700 font-medium">{safeStr(result.contact_info.email)}</p>
                  </div>
                )}
                {result?.contact_info?.phone && (
                  <div>
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Phone</span>
                    <p className="text-sm text-slate-700 font-medium">{safeStr(result.contact_info.phone)}</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {hasDeterministicData && <ResultCard result={result} defaultExpandEducation skipNarrativePolling />}

          {/* Evaluation Checklist */}
          {hasDeterministicData && <EvaluationChecklist result={result} />}

          {/* AI Recruiter Scorecard Section */}
          {result?.candidate_id && (
            <RecruiterScorecardSection
              candidateId={result.candidate_id}
              sessions={recruiterSessions}
              scorecard={recruiterScorecard}
              loading={recruiterLoading}
            />
          )}

          {/* Phone Screen CTA — inline banner */}
          {(kitReadiness.totalQ > 0 || kitReadiness.state === 'fallback' || kitReadiness.state === 'empty') && (
            <div className="rounded-2xl ring-1 ring-brand-200 bg-gradient-to-r from-brand-50 to-indigo-50 p-5 flex items-center justify-between gap-4">
              <div>
                <p className="font-bold text-brand-800 text-sm">{INTERVIEW.liveScreenKit}</p>
                <p className="text-xs text-slate-500 mt-0.5">{INTERVIEW.liveScreenKitHint}</p>
              </div>
              <button
                onClick={handleStartLiveScreen}
                className="shrink-0 flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold text-white bg-brand-600 hover:bg-brand-700 shadow-brand-sm transition-colors"
              >
                <PhoneCall className="w-4 h-4" />
                Open kit
              </button>
            </div>
          )}

          {/* Manual Interview Scorecard */}
          {result?.interview_questions && result.result_id && (
            <div className="mt-6">
              <InterviewScorecard key={scorecardKey} resultId={result.result_id} showHeading />
            </div>
          )}

          <Timeline
            workExperience={result.work_experience || []}
            gaps={result.employment_gaps || []}
          />
        </div>
      </div>

      {/* Voice Screening Schedule Modal */}
      {interviewModalOpen && result?.candidate_id && (
        <InterviewInitiateModal
          onClose={() => setInterviewModalOpen(false)}
          onSuccess={() => setInterviewModalOpen(false)}
          initialCandidateId={result.candidate_id}
          initialJdId={result?.jd_id || jdContext?.jd_id || ''}
          initialPhone={result?.contact_info?.phone || ''}
          screeningResultId={result?.result_id}
        />
      )}

      <RescoreSheet
        isOpen={rescoreOpen}
        onClose={() => setRescoreOpen(false)}
        result={result}
        onRescoreComplete={(updated) => {
          setResult((prev) => ({ ...prev, ...updated, fit_score: updated.fit_score ?? prev.fit_score }))
        }}
      />

      <LiveScreenKitReadinessGate
        open={readinessGateOpen}
        onClose={() => setReadinessGateOpen(false)}
        state={kitReadiness.state === 'empty' ? 'empty' : 'loading'}
        onStartWithFallback={
          kitReadiness.state === 'empty'
            ? () => {
                setForceFallbackKit(true)
                setReadinessGateOpen(false)
                setScreenMode(true)
              }
            : undefined
        }
      />
    </div>
  )
}
