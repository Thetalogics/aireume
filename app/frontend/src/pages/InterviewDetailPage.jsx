import { useState, useEffect } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Mic, Brain, ArrowLeft, Clock, FileText, Calendar, CalendarClock, User, Loader2,
  XCircle, RefreshCw, CheckCircle2, AlertTriangle,
  MessageSquare, ClipboardList, Target, Phone, Zap,
  Sparkles, TrendingUp, ShieldCheck,
} from 'lucide-react'
import {
  getRecruiterSession, getRecruiterTranscript, getRecruiterScorecard,
  cancelRecruiterSession, retryRecruiterSession,
  getVoiceSession, cancelVoiceSession, rescheduleVoiceCall,
} from '../lib/api'
import RecruiterScorecard from '../components/RecruiterScorecard'
import RecruiterTranscript from '../components/RecruiterTranscript'
import VoiceTranscriptViewer from '../components/VoiceTranscriptViewer'
import VoiceScheduleModal from '../components/VoiceScheduleModal'
import { parseVoiceKitTranscript } from '../lib/intakeFormUtils'
import { ConsolidatedScoreHero } from '../components/patterns'
import { formatDateTime } from '../lib/utils'
import useConfirm from '../hooks/useConfirm'

const DEPTH_CONFIG = {
  quick:    { label: 'Quick Screen',     color: 'bg-blue-100 text-blue-700' },
  standard: { label: 'Standard Interview', color: 'bg-purple-100 text-purple-700' },
  deep:     { label: 'Deep Assessment',  color: 'bg-amber-100 text-amber-700' },
}

const STATUS_CONFIG = {
  scheduled:        { label: 'Scheduled',   color: 'bg-neutral-100 text-neutral-700' },
  ringing:          { label: 'Ringing',     color: 'bg-amber-100 text-amber-700' },
  in_progress:      { label: 'In Progress', color: 'bg-blue-100 text-blue-700' },
  pending_strategy: { label: 'Preparing',   color: 'bg-neutral-100 text-neutral-700' },
  strategy_ready:   { label: 'Ready',       color: 'bg-blue-100 text-blue-700' },
  completed:        { label: 'Completed',   color: 'bg-emerald-100 text-emerald-700' },
  failed:           { label: 'Failed',      color: 'bg-red-100 text-red-700' },
  no_answer:        { label: 'No Answer',   color: 'bg-orange-100 text-orange-700' },
  cancelled:        { label: 'Cancelled',   color: 'bg-gray-100 text-gray-500' },
  expired:          { label: 'Expired',     color: 'bg-orange-100 text-orange-600' },
}

const RECOMMENDATION_COLORS = {
  strong_hire:    'bg-emerald-100 text-emerald-800',
  hire:           'bg-green-100 text-green-700',
  maybe:          'bg-amber-100 text-amber-700',
  no_hire:        'bg-red-100 text-red-700',
  strong_no_hire: 'bg-red-200 text-red-800',
}

const RECOMMENDATION_LABELS = {
  strong_hire:    'Strong Hire',
  hire:           'Hire',
  maybe:          'Maybe',
  no_hire:        'No Hire',
  strong_no_hire: 'Strong No Hire',
}

const CONFIDENCE_CONFIG = {
  high:   { label: 'High Confidence',   color: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200' },
  medium: { label: 'Medium Confidence', color: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200' },
  low:    { label: 'Low Confidence',    color: 'bg-red-50 text-red-700 ring-1 ring-red-200' },
}

function ExecutiveSummary({ scorecard }) {
  if (!scorecard) return null
  const {
    executive_summary,
    recommendation,
    confidence_level,
    recommendation_reasoning,
  } = scorecard

  const hasAny =
    executive_summary || recommendation || confidence_level || recommendation_reasoning
  if (!hasAny) return null

  const recColor = RECOMMENDATION_COLORS[recommendation]
  const recLabel = RECOMMENDATION_LABELS[recommendation] || (recommendation ? recommendation.replace(/_/g, ' ') : null)
  const conf = confidence_level ? CONFIDENCE_CONFIG[confidence_level] : null

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-brand-700 via-brand-600 to-brand-500 shadow-lg mb-6"
    >
      {/* Decorative accent */}
      <div className="pointer-events-none absolute -right-10 -top-10 w-40 h-40 rounded-full bg-white/10 blur-2xl" />
      <div className="pointer-events-none absolute -right-6 bottom-0 w-24 h-24 rounded-full bg-white/10 blur-xl" />

      <div className="relative p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-8 h-8 rounded-lg bg-white/20 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <h3 className="text-sm font-bold text-white tracking-wide uppercase">
            Executive Summary
          </h3>
        </div>

        {executive_summary && (
          <p className="text-sm leading-relaxed text-white/95 mb-4">
            {executive_summary}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {recommendation && (
            <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold ${
              recColor || 'bg-white/20 text-white'
            }`}>
              <CheckCircle2 className="w-3.5 h-3.5" />
              {recLabel}
            </span>
          )}
          {confidence_level && (
            <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
              conf ? conf.color : 'bg-white/20 text-white'
            }`}>
              <ShieldCheck className="w-3.5 h-3.5" />
              {conf ? conf.label : confidence_level}
            </span>
          )}
        </div>

        {recommendation_reasoning && (
          <div className="mt-4 pt-4 border-t border-white/20">
            <p className="text-xs font-semibold text-white/70 uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5" />
              Recommendation Reasoning
            </p>
            <p className="text-xs leading-relaxed text-white/90">
              {recommendation_reasoning}
            </p>
          </div>
        )}
      </div>
    </motion.div>
  )
}

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.scheduled
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

function DepthBadge({ depth }) {
  const cfg = DEPTH_CONFIG[depth] || DEPTH_CONFIG.standard
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

function InfoField({ label, value }) {
  return (
    <div className="p-3 bg-slate-50 rounded-xl">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      <p className="text-sm font-semibold text-slate-700">{value ?? '—'}</p>
    </div>
  )
}

export default function InterviewDetailPage() {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { confirm, dialog } = useConfirm()
  const sourceParam = searchParams.get('source')
  const depthParam = searchParams.get('depth') || 'standard'

  const [session, setSession] = useState(null)
  const [source, setSource] = useState(sourceParam || null)
  const [depth, setDepth] = useState(depthParam)
  const [transcript, setTranscript] = useState(null)
  const [scorecard, setScorecard] = useState(null)
  const [showRescheduleModal, setShowRescheduleModal] = useState(false)
  const [loading, setLoading] = useState(true)
  const [tabLoading, setTabLoading] = useState(false)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('transcript')

  useEffect(() => {
    async function load() {
      try {
        setLoading(true)
        let sessionData = null
        let detectedSource = sourceParam
        setDepth(depthParam)
        setTranscript(null)
        setScorecard(null)

        if (detectedSource === 'voice') {
          sessionData = await getVoiceSession(id)
          setDepth('quick')
        } else if (detectedSource === 'recruiter') {
          sessionData = await getRecruiterSession(id)
        } else {
          // Unknown source: try recruiter first, then voice
          try {
            sessionData = await getRecruiterSession(id)
            detectedSource = 'recruiter'
          } catch {
            try {
              sessionData = await getVoiceSession(id)
              detectedSource = 'voice'
              setDepth('quick')
            } catch {
              throw new Error('Session not found')
            }
          }
        }

        setSource(detectedSource)
        setSession(sessionData)

        // Smart default tab
        if (sessionData.status === 'completed' && detectedSource === 'recruiter') {
          setActiveTab('scorecard')
        } else if (['scheduled', 'pending_strategy', 'strategy_ready'].includes(sessionData.status) && detectedSource === 'recruiter') {
          setActiveTab('strategy')
        } else {
          setActiveTab('transcript')
        }

        // Pre-load transcript and scorecard for completed sessions
        if (sessionData.status === 'completed') {
          if (detectedSource === 'recruiter') {
            const [sc, tr] = await Promise.all([
              getRecruiterScorecard(id).catch(() => null),
              getRecruiterTranscript(id).catch(() => null),
            ])
            setScorecard(sc)
            setTranscript(tr)
          } else {
            // Voice sessions: speaker turns, or kit Q&A fallback
            if (sessionData.transcript?.length) {
              setTranscript(sessionData.transcript)
            } else {
              const kitTranscript = parseVoiceKitTranscript(sessionData)
              if (kitTranscript.length) setTranscript(kitTranscript)
            }
            if (sessionData.assessment_json) {
              const assessment = typeof sessionData.assessment_json === 'string'
                ? JSON.parse(sessionData.assessment_json) : sessionData.assessment_json
              setScorecard(assessment)
            }
          }
        }
      } catch (err) {
        setError(err.message || 'Failed to load session')
      } finally {
        setLoading(false)
      }
    }
    if (id) load()
  }, [id, sourceParam, depthParam])

  // Poll scorecard until ready after completed recruiter sessions
  useEffect(() => {
    if (!id || source !== 'recruiter' || session?.status !== 'completed' || scorecard) return

    let cancelled = false
    let attempts = 0
    let timer = null

    const poll = async () => {
      if (cancelled || attempts >= 24) return
      attempts += 1
      try {
        const sc = await getRecruiterScorecard(id)
        if (!cancelled && sc) {
          setScorecard(sc)
          return
        }
      } catch {
        /* scorecard still generating */
      }
      if (!cancelled) {
        timer = setTimeout(poll, 5000)
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [id, source, session?.status, scorecard])

  async function loadTab(tab) {
    setActiveTab(tab)
    if (tab === 'transcript' && !transcript) {
      try {
        setTabLoading(true)
        if (source === 'recruiter') {
          const tr = await getRecruiterTranscript(id)
          setTranscript(tr)
        } else if (session?.transcript) {
          setTranscript(session.transcript)
        }
      } catch { /* ignore */ }
      finally { setTabLoading(false) }
    }
    if (tab === 'scorecard' && !scorecard) {
      try {
        setTabLoading(true)
        if (source === 'recruiter') {
          const sc = await getRecruiterScorecard(id)
          setScorecard(sc)
        }
      } catch { /* ignore */ }
      finally { setTabLoading(false) }
    }
  }

  async function handleCancel() {
    const ok = await confirm({
      title: 'Cancel session',
      message: 'Cancel this interview session?',
      confirmLabel: 'Cancel session',
      danger: true,
    })
    if (!ok) return
    try {
      if (source === 'voice') {
        await cancelVoiceSession(id)
      } else {
        await cancelRecruiterSession(id)
      }
      // Reload session
      const updated = source === 'voice' ? await getVoiceSession(id) : await getRecruiterSession(id)
      setSession(updated)
    } catch (err) {
      setError(err.message || 'Failed to cancel')
    }
  }

  async function handleRetry() {
    try {
      if (source === 'recruiter') {
        await retryRecruiterSession(id)
        const updated = await getRecruiterSession(id)
        setSession(updated)
      }
    } catch (err) {
      setError(err.message || 'Failed to retry')
    }
  }

  function handleReschedule() {
    const voiceSessionId = source === 'voice' ? id : session?.voice_session_id
    if (!voiceSessionId) {
      setError('No voice session associated with this interview')
      return
    }
    setShowRescheduleModal(true)
  }

  async function handleRescheduleComplete() {
    setShowRescheduleModal(false)
    try {
      const updated = source === 'voice'
        ? await getVoiceSession(id)
        : await getRecruiterSession(id)
      setSession(updated)
    } catch (err) {
      setError(err.message || 'Failed to refresh session')
    }
  }

  function formatDuration(seconds) {
    if (!seconds) return '—'
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}m ${s}s`
  }

  if (loading) {
    return (
      <div className="h-screen bg-surface flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-brand-600 animate-spin" />
      </div>
    )
  }

  if (error || !session) {
    return (
      <div className="min-h-screen bg-surface flex items-center justify-center">
        <div className="text-center">
          <AlertTriangle className="w-12 h-12 text-red-300 mx-auto mb-4" />
          <p className="text-sm text-red-600 mb-4">{error || 'Session not found'}</p>
          <button onClick={() => navigate('/ai-interviews')} className="px-4 py-2 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700">
            Back to Sessions
          </button>
        </div>
      </div>
    )
  }

  // Determine what tabs to show
  const tabs = [
    { key: 'transcript', label: 'Transcript', icon: MessageSquare },
    ...(source === 'recruiter' ? [{ key: 'scorecard', label: 'Scorecard', icon: Target }] : []),
    ...(source === 'recruiter' ? [{ key: 'strategy', label: 'Strategy', icon: ClipboardList }] : []),
  ]

  // Candidate name
  const candidateName = session.candidate_name || session.candidate_email || `Candidate #${session.candidate_id}`
  const strategyData = session.interview_strategy_json || session.strategy || null
  const plannedQuestions = strategyData?.planned_questions || strategyData?.questions || []

  return (
    <div className="min-h-screen bg-surface">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
        {/* Back button */}
        <button
          onClick={() => navigate('/ai-interviews')}
          className="flex items-center gap-2 text-sm text-slate-500 hover:text-brand-600 mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Sessions
        </button>

        {/* Header */}
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className={`w-14 h-14 rounded-2xl flex items-center justify-center ${
                session.status === 'completed' ? 'bg-gradient-to-br from-emerald-500 to-green-400' :
                session.status === 'in_progress' ? 'bg-gradient-to-br from-blue-500 to-indigo-400' :
                session.status === 'failed' ? 'bg-gradient-to-br from-red-500 to-orange-400' :
                'bg-gradient-to-br from-brand-600 to-brand-400'
              } shadow-lg`}>
                {source === 'voice' ? <Phone className="w-7 h-7 text-white" /> : <Brain className="w-7 h-7 text-white" />}
              </div>
              <div>
                <h1 className="text-2xl font-extrabold text-brand-900 tracking-tight">
                  {candidateName}
                </h1>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  <DepthBadge depth={depth} />
                  <StatusBadge status={session.status} />
                  {session.jd_title && (
                    <span className="text-sm text-slate-500 flex items-center gap-1">
                      <FileText className="w-3.5 h-3.5" />
                      {session.jd_title}
                    </span>
                  )}
                  {session.recommendation && (
                    <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${RECOMMENDATION_COLORS[session.recommendation] || 'bg-slate-100 text-slate-600'}`}>
                      {session.recommendation.replace(/_/g, ' ')}
                    </span>
                  )}
                </div>
                {session.scheduled_at && ['scheduled', 'pending_strategy', 'strategy_ready'].includes(session.status) && (
                  <div className="flex items-center gap-1.5 text-sm text-brand-600 mt-2">
                    <Calendar className="w-4 h-4" />
                    <span className="font-semibold">
                      Scheduled: {formatDateTime(session.scheduled_at)}
                    </span>
                  </div>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {session.status === 'scheduled' && (source === 'voice' || session?.voice_session_id) && (
                <button
                  onClick={handleReschedule}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-blue-600 bg-blue-50 rounded-xl hover:bg-blue-100 transition-colors"
                >
                  <CalendarClock className="w-4 h-4" /> Reschedule
                </button>
              )}
              {['pending_strategy', 'strategy_ready', 'scheduled', 'failed', 'no_answer', 'ringing'].includes(session.status) && !['cancelled', 'completed'].includes(session.status) && (
                <button
                  onClick={handleCancel}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-red-600 bg-red-50 rounded-xl hover:bg-red-100 transition-colors"
                >
                  <XCircle className="w-4 h-4" /> Cancel
                </button>
              )}
              {['failed', 'expired'].includes(session.status) && source === 'recruiter' && (
                <button
                  onClick={handleRetry}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-brand-600 bg-brand-50 rounded-xl hover:bg-brand-100 transition-colors"
                >
                  <RefreshCw className="w-4 h-4" /> Retry
                </button>
              )}
            </div>
          </div>
        </motion.div>

        {scorecard && (
          <ConsolidatedScoreHero
            className="mb-6"
            analysisScore={scorecard.original_fit_score}
            callScore={scorecard.overall_score ?? scorecard.adjusted_fit_score}
            callSource="ai"
            consolidatedRecommendation={scorecard.recommendation}
            consolidatedReasoning={
              scorecard.recommendation_reasoning
              || scorecard.executive_summary
              || scorecard.adjustment_reasoning
            }
          />
        )}

        {/* Info grid */}
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
          {session.scheduled_at && (
            <InfoField
              label="Scheduled At"
              value={formatDateTime(session.scheduled_at)}
            />
          )}
          {session.phone_number && (
            <InfoField label="Phone" value={session.phone_number} />
          )}
          {session.jd_title && (
            <InfoField label="JD / Role" value={session.jd_title} />
          )}
          {session.status === 'completed' && (
            <InfoField label="Duration" value={formatDuration(session.duration_seconds)} />
          )}
          {source === 'recruiter' && session.completed_at && (
            <InfoField label="Completed" value={new Date(session.completed_at).toLocaleDateString('en-US')} />
          )}
          {session.trigger_type && (
            <InfoField label="Trigger" value={session.trigger_type.replace(/_/g, ' ')} />
          )}
          {session.overall_score != null && (
            <InfoField label="Overall Score" value={session.overall_score} />
          )}
          {session.match_score != null && (
            <InfoField label="Match Score" value={`${session.match_score}%`} />
          )}
          {session.question_count != null && (
            <InfoField label="Questions" value={session.question_count} />
          )}
        </motion.div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-white/60 backdrop-blur rounded-2xl p-1 ring-1 ring-brand-100 w-fit">
          {tabs.map(tab => (
            <button
              key={tab.key}
              onClick={() => loadTab(tab.key)}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition-all ${
                activeTab === tab.key
                  ? 'bg-brand-600 text-white shadow-sm'
                  : 'text-slate-500 hover:text-brand-700 hover:bg-brand-50'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>

        {tabLoading && (
          <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-brand-600 animate-spin" /></div>
        )}

        {/* Transcript Tab */}
        {!tabLoading && activeTab === 'transcript' && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            {source === 'voice' && Array.isArray(transcript) && transcript.some((t) => t?.speaker && t?.text) ? (
              <VoiceTranscriptViewer entries={transcript} />
            ) : (
              <RecruiterTranscript transcript={transcript} />
            )}
          </motion.div>
        )}

        {/* Scorecard Tab (recruiter sessions only) */}
        {!tabLoading && activeTab === 'scorecard' && source === 'recruiter' && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            {scorecard ? (
              <>
                <ExecutiveSummary scorecard={scorecard} />
                <RecruiterScorecard scorecard={scorecard} />
              </>
            ) : (
              <div className="text-center py-16 text-slate-400">
                <Target className="w-12 h-12 mx-auto mb-4 opacity-40" />
                <p className="text-sm">
                  {session.status === 'completed' ? 'No scorecard available' : 'Scorecard available after interview completion'}
                </p>
              </div>
            )}
          </motion.div>
        )}

        {/* Strategy Tab (recruiter sessions only) */}
        {!tabLoading && activeTab === 'strategy' && source === 'recruiter' && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            {strategyData ? (
              <div className="bg-white rounded-2xl ring-1 ring-slate-200 p-6">
                <h3 className="text-sm font-bold text-slate-800 mb-4 flex items-center gap-2">
                  <ClipboardList className="w-4 h-4 text-brand-600" />
                  Interview Kit Plan
                  {strategyData.source === 'interview_kit' && (
                    <span className="text-xs font-semibold text-brand-600 bg-brand-50 px-2 py-0.5 rounded-full">JD + Resume driven</span>
                  )}
                </h3>
                <div className="space-y-4">
                  {strategyData.objective && (
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Objective</h4>
                      <p className="text-sm text-slate-700">{strategyData.objective}</p>
                    </div>
                  )}
                  {strategyData.candidate_briefing?.profile_snapshot && (
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Briefing</h4>
                      <p className="text-sm text-slate-700">{strategyData.candidate_briefing.profile_snapshot}</p>
                    </div>
                  )}
                  {strategyData.focus_areas?.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Focus Areas</h4>
                      <div className="flex flex-wrap gap-2">
                        {strategyData.focus_areas.map((area, i) => (
                          <span key={i} className="px-2.5 py-1 bg-brand-50 text-brand-700 rounded-lg text-xs font-semibold">{area}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {plannedQuestions.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Planned Questions ({plannedQuestions.length})</h4>
                      <div className="space-y-2">
                        {plannedQuestions.map((q, i) => (
                          <div key={i} className="flex items-start gap-2 p-3 bg-slate-50 rounded-xl">
                            <span className="text-xs font-bold text-brand-600 mt-0.5">{i + 1}</span>
                            <div>
                              <p className="text-sm text-slate-700">{q.spoken_text || q.question_text || q.question || q.text || q}</p>
                              {q.intent && (
                                <p className="text-xs text-brand-700/80 mt-1">Intent: {q.intent}</p>
                              )}
                              {(q.category || q.stage) && (
                                <span className="text-xs text-slate-400 mt-1">{q.category || q.stage}</span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="text-center py-16 text-slate-400">
                <ClipboardList className="w-12 h-12 mx-auto mb-4 opacity-40" />
                <p className="text-sm">
                  {['pending_strategy', 'strategy_ready'].includes(session.status)
                    ? 'Interview kit plan is being prepared...'
                    : 'No interview kit plan available'}
                </p>
              </div>
            )}
          </motion.div>
        )}

        {/* Voice Assessment (inline for quick screens) */}
        {!tabLoading && activeTab === 'transcript' && source === 'voice' && session.assessment_json && !transcript?.length && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="mt-6">
            <div className="bg-white rounded-2xl ring-1 ring-slate-200 p-6">
              <h3 className="text-sm font-bold text-slate-800 mb-4 flex items-center gap-2">
                <Target className="w-4 h-4 text-brand-600" />
                Assessment Summary
              </h3>
              {(() => {
                const assessment = typeof session.assessment_json === 'string'
                  ? JSON.parse(session.assessment_json) : session.assessment_json
                return (
                  <div className="space-y-3">
                    {assessment.recommendation && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-slate-500">Recommendation:</span>
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                          assessment.recommendation === 'pass' || assessment.recommendation === 'hire'
                            ? 'bg-emerald-100 text-emerald-700'
                            : assessment.recommendation === 'maybe'
                              ? 'bg-amber-100 text-amber-700'
                              : 'bg-red-100 text-red-700'
                        }`}>
                          {assessment.recommendation}
                        </span>
                      </div>
                    )}
                    {assessment.summary && (
                      <p className="text-sm text-slate-700">{assessment.summary}</p>
                    )}
                    {assessment.overall_score != null && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-slate-500">Score:</span>
                        <span className="text-lg font-bold text-brand-600">{assessment.overall_score}</span>
                      </div>
                    )}
                  </div>
                )
              })()}
            </div>
          </motion.div>
        )}

        {/* Reschedule Modal */}
        {showRescheduleModal && session && (
          <VoiceScheduleModal
            onClose={() => setShowRescheduleModal(false)}
            onScheduled={handleRescheduleComplete}
            editSession={{
              id: source === 'voice' ? id : session.voice_session_id,
              scheduled_at: session.scheduled_at,
              phone_number: session.phone_number,
            }}
          />
        )}
      </div>
      {dialog}
    </div>
  )
}
