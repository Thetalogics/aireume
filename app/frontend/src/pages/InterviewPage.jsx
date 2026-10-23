import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Mic, Plus, Search,
  RefreshCw, Loader2,
  AlertTriangle, X, Download,
  Settings as SettingsIcon,
} from 'lucide-react'
import {
  getVoiceSessions,
  cancelVoiceSession, cancelRecruiterSession, exportInterviewSessions,
  getRecruiterSessions,
} from '../lib/api'
import InterviewInitiateModal from '../components/InterviewInitiateModal'
import { INTERVIEW } from '../lib/uxLabels'
import { PageHeader } from '../components/patterns'
import {
  InterviewSessionSection,
  InterviewEmptyState,
  InterviewSessionRow,
} from '../components/patterns/InterviewSessionList'
import { Button, Card, SegmentedControl } from '../components/ui'
import { bucketSessions, groupSessionsByCandidate, depthLabel } from '../lib/interviewHubUtils'
import useConfirm from '../hooks/useConfirm'

/* ── Session normalization ───────────────────────────── */

function normalizeVoiceSession(s) {
  return {
    id: `v-${s.id}`,
    rawId: s.id,
    source: 'voice',
    depth: 'quick',
    candidate_id: s.candidate_id,
    candidate_name: s.candidate_name || s.candidate_email || `Candidate #${s.candidate_id}`,
    jd_title: s.jd_title || null,
    status: s.status,
    duration_seconds: s.duration_seconds || null,
    score: s.match_score ?? null,
    recommendation: null,
    created_at: s.created_at,
    scheduled_at: s.scheduled_at,
    direction: s.direction,
    phone_number: s.phone_number,
    error_log: s.error_log || null,
  }
}

function normalizeRecruiterSession(s) {
  const isDeep = (s.duration_seconds || 0) > 900 ||
    (s.overall_score != null) ||
    s.recommendation ||
    s.status === 'completed'
  return {
    id: `r-${s.id}`,
    rawId: s.id,
    voice_session_id: s.voice_session_id,
    source: 'recruiter',
    depth: isDeep ? 'deep' : 'standard',
    candidate_id: s.candidate_id,
    candidate_name: s.candidate_name || `Candidate #${s.candidate_id}`,
    jd_title: s.jd_title || null,
    status: s.status,
    duration_seconds: s.duration_seconds || null,
    score: s.overall_score ?? null,
    recommendation: s.recommendation || null,
    created_at: s.created_at,
    scheduled_at: s.scheduled_at || null,
    direction: null,
    phone_number: s.phone_number || null,
    error_log: s.error_log || null,
  }
}

/* ── Main component ──────────────────────────────────── */

export default function InterviewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { confirm, dialog } = useConfirm()
  const initialDepth = searchParams.get('depth') || 'all'

  const [voiceSessions, setVoiceSessions] = useState([])
  const [recruiterSessions, setRecruiterSessions] = useState([])
  const [exporting, setExporting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('sessions')
  const [depthFilter, setDepthFilter] = useState(initialDepth)
  const [statusFilter, setStatusFilter] = useState('all')
  const [viewMode, setViewMode] = useState('session')
  const [searchQuery, setSearchQuery] = useState('')
  const [showInitiateModal, setShowInitiateModal] = useState(false)
  const [rescheduleSession, setRescheduleSession] = useState(null)

  // Merge + normalize sessions. Voice sessions that are linked to a recruiter
  // interview are rendered as part of that interview, not as standalone rows.
  const allSessions = useMemo(() => {
    const recruiter = recruiterSessions.map(normalizeRecruiterSession)
    const linkedVoiceIds = new Set(recruiter.map(s => s.voice_session_id).filter(Boolean))
    const voice = voiceSessions
      .filter(s => !linkedVoiceIds.has(s.id))
      .map(normalizeVoiceSession)
    return [...voice, ...recruiter].sort((a, b) =>
      new Date(b.created_at || 0) - new Date(a.created_at || 0)
    )
  }, [voiceSessions, recruiterSessions])

  // Apply filters
  const filteredSessions = useMemo(() => {
    let list = allSessions
    if (depthFilter && depthFilter !== 'all') list = list.filter(s => s.depth === depthFilter)
    if (statusFilter && statusFilter !== 'all') list = list.filter(s => s.status === statusFilter)
    if (searchQuery.length >= 2) {
      const q = searchQuery.toLowerCase()
      list = list.filter(s =>
        s.candidate_name?.toLowerCase().includes(q) ||
        s.jd_title?.toLowerCase().includes(q)
      )
    }
    return list
  }, [allSessions, depthFilter, statusFilter, searchQuery])

  // Depth counts
  const depthCounts = useMemo(() => ({
    all: allSessions.length,
    quick: allSessions.filter(s => s.depth === 'quick').length,
    standard: allSessions.filter(s => s.depth === 'standard').length,
    deep: allSessions.filter(s => s.depth === 'deep').length,
  }), [allSessions])

  const displaySessions = useMemo(() => {
    if (viewMode === 'candidate') return groupSessionsByCandidate(filteredSessions)
    return filteredSessions
  }, [filteredSessions, viewMode])

  const sessionBuckets = useMemo(() => bucketSessions(filteredSessions), [filteredSessions])

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true)
      const [vs, rs] = await Promise.all([
        getVoiceSessions({ limit: 50 }).catch(() => []),
        getRecruiterSessions({ limit: 50 }).catch(() => []),
      ])
      setVoiceSessions(Array.isArray(vs) ? vs : vs.sessions || [])
      setRecruiterSessions(Array.isArray(rs) ? rs : rs.sessions || [])
    } catch (err) {
      setError(err.message || 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  async function handleCancelSession(session) {
    const ok = await confirm({
      title: 'Cancel session',
      message: 'Cancel this interview session?',
      confirmLabel: 'Cancel session',
      danger: true,
    })
    if (!ok) return
    try {
      if (session.source === 'voice') {
        await cancelVoiceSession(session.rawId)
      } else {
        await cancelRecruiterSession(session.rawId)
      }
      // Refresh sessions
      const [vs, rs] = await Promise.all([
        getVoiceSessions({ limit: 50 }).catch(() => []),
        getRecruiterSessions({ limit: 50 }).catch(() => []),
      ])
      setVoiceSessions(Array.isArray(vs) ? vs : vs.sessions || [])
      setRecruiterSessions(Array.isArray(rs) ? rs : rs.sessions || [])
    } catch (err) {
      setError(err.message || 'Failed to cancel')
    }
  }

  function handleReschedule(session) {
    const voiceSessionId = session.source === 'voice' ? session.rawId : session.voice_session_id
    if (!voiceSessionId) {
      setError('No voice session associated with this interview')
      return
    }
    setRescheduleSession({
      id: voiceSessionId,
      scheduled_at: session.scheduled_at,
      phone_number: session.phone_number,
    })
  }

  async function handleRescheduleComplete() {
    setRescheduleSession(null)
    try {
      const [vs, rs] = await Promise.all([
        getVoiceSessions({ limit: 50 }).catch(() => []),
        getRecruiterSessions({ limit: 50 }).catch(() => []),
      ])
      setVoiceSessions(Array.isArray(vs) ? vs : vs.sessions || [])
      setRecruiterSessions(Array.isArray(rs) ? rs : rs.sessions || [])
    } catch (err) {
      setError(err.message || 'Failed to refresh sessions')
    }
  }

  function handleSessionClick(session) {
    navigate(`/ai-interviews/${session.rawId}?source=${session.source}&depth=${session.depth}`)
  }

  async function handleExportSessions() {
    try {
      setExporting(true)
      const params = {}
      if (depthFilter && depthFilter !== 'all') params.depth = depthFilter
      if (statusFilter && statusFilter !== 'all') params.status = statusFilter
      const blob = await exportInterviewSessions(params)
      const url = window.URL.createObjectURL(new Blob([blob], { type: 'text/csv' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `interview_sessions_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (err) {
      setError(err.message || 'Failed to export sessions')
    } finally {
      setExporting(false)
    }
  }

  if (loading) {
    return (
      <div className="h-screen bg-surface flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-brand-600 animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-surface">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        <PageHeader
          className="mb-6"
          icon={Mic}
          title={INTERVIEW.hubTitle}
          subtitle={INTERVIEW.hubSubtitle}
          actions={(
            <>
              <Link
                to="/settings?tab=interviews"
                className="hidden sm:inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-slate-600 hover:text-brand-700 rounded-xl hover:bg-brand-50 transition-colors"
              >
                <SettingsIcon className="w-4 h-4" />
                {INTERVIEW.settingsLink}
              </Link>
              <Button onClick={() => setShowInitiateModal(true)}>
                <Plus className="w-4 h-4 mr-1.5" />
                {INTERVIEW.newScreenCall}
              </Button>
            </>
          )}
        />

        <div className="flex flex-wrap gap-2 mb-6">
          <Link to="/voice-screening" className="px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm font-semibold text-brand-700 hover:bg-brand-50">Voice screening</Link>
          <Link to="/recruiter-interviews" className="px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm font-semibold text-brand-700 hover:bg-brand-50">Recruiter interviews</Link>
          <Link to="/video" className="px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm font-semibold text-brand-700 hover:bg-brand-50">Interview review</Link>
          <Link to="/transcript" className="px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm font-semibold text-brand-700 hover:bg-brand-50">Transcript analysis</Link>
        </div>

        <div className="flex flex-wrap items-center gap-3 mb-6">
          <SegmentedControl
            options={[
              { label: 'Sessions', value: 'sessions' },
              { label: 'Analytics', value: 'analytics' },
            ]}
            value={activeTab}
            onChange={(v) => {
              if (v === 'analytics') {
                navigate('/analytics/explore?slice=interviews')
                return
              }
              setActiveTab(v)
            }}
          />
          <button
            type="button"
            onClick={fetchAll}
            className="p-2 rounded-xl text-slate-400 hover:text-brand-600 hover:bg-brand-50 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        {error && (
          <Card className="mb-6 p-4 flex items-center gap-3 bg-red-50 ring-red-200">
            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0" />
            <p className="text-sm text-red-700 flex-1">{error}</p>
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss error" className="text-red-400 hover:text-red-600">
              <X className="w-4 h-4" />
            </button>
          </Card>
        )}

        {activeTab === 'sessions' && (
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
            <Card className="p-3 sm:p-4">
              <div className="flex flex-col lg:flex-row lg:items-center gap-3">
                <div className="relative flex-1 min-w-0">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search candidates or roles…"
                    className="w-full pl-9 pr-3 py-2.5 rounded-xl ring-1 ring-slate-200 dark:ring-white/10 bg-white dark:bg-dark-card text-sm outline-none focus:ring-2 focus:ring-brand-500"
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={depthFilter}
                    onChange={(e) => setDepthFilter(e.target.value)}
                    className="px-3 py-2 rounded-xl ring-1 ring-slate-200 dark:ring-white/10 bg-white dark:bg-dark-card text-sm font-medium outline-none"
                  >
                    <option value="all">All depths ({depthCounts.all})</option>
                    <option value="quick">{INTERVIEW.quick} ({depthCounts.quick})</option>
                    <option value="standard">{INTERVIEW.standard} ({depthCounts.standard})</option>
                    <option value="deep">{INTERVIEW.deep} ({depthCounts.deep})</option>
                  </select>
                  <select
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    className="px-3 py-2 rounded-xl ring-1 ring-slate-200 dark:ring-white/10 bg-white dark:bg-dark-card text-sm font-medium outline-none"
                  >
                    <option value="all">All status</option>
                    <option value="in_progress">In progress</option>
                    <option value="scheduled">Scheduled</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                    <option value="cancelled">Cancelled</option>
                  </select>
                  <SegmentedControl
                    options={[
                      { label: INTERVIEW.viewBySession, value: 'session' },
                      { label: INTERVIEW.viewByCandidate, value: 'candidate' },
                    ]}
                    value={viewMode}
                    onChange={setViewMode}
                  />
                  <Button variant="secondary" size="sm" loading={exporting} onClick={handleExportSessions}>
                    <Download className="w-3.5 h-3.5 mr-1" />
                    Export
                  </Button>
                </div>
              </div>
            </Card>

            {filteredSessions.length === 0 ? (
              <InterviewEmptyState
                onStart={() => setShowInitiateModal(true)}
                filterLabel={depthFilter !== 'all' ? depthLabel(depthFilter) : null}
              />
            ) : viewMode === 'candidate' ? (
              <Card className="overflow-hidden p-0 divide-y divide-brand-50 dark:divide-white/10">
                {displaySessions.map((session) => (
                  <InterviewSessionRow
                    key={session.id}
                    session={session}
                    showSessionCount={session.sessionCount}
                    onClick={() => handleSessionClick(session)}
                    onReschedule={handleReschedule}
                    onCancel={handleCancelSession}
                  />
                ))}
              </Card>
            ) : (
              <div className="space-y-4">
                <InterviewSessionSection
                  title={INTERVIEW.needsAttention}
                  sessions={sessionBuckets.attention}
                  onClick={handleSessionClick}
                  onReschedule={handleReschedule}
                  onCancel={handleCancelSession}
                />
                <InterviewSessionSection
                  title={INTERVIEW.upcoming}
                  sessions={sessionBuckets.upcoming}
                  onClick={handleSessionClick}
                  onReschedule={handleReschedule}
                  onCancel={handleCancelSession}
                />
                <InterviewSessionSection
                  title={INTERVIEW.recent}
                  sessions={sessionBuckets.recent}
                  onClick={handleSessionClick}
                  onReschedule={handleReschedule}
                  onCancel={handleCancelSession}
                />
              </div>
            )}
          </motion.div>
        )}

        {(showInitiateModal || rescheduleSession) && (
          <InterviewInitiateModal
            onClose={() => { setShowInitiateModal(false); setRescheduleSession(null) }}
            onSuccess={() => {
              fetchAll()
              if (rescheduleSession) handleRescheduleComplete()
            }}
            editSession={rescheduleSession || undefined}
          />
        )}
      </div>
      {dialog}
    </div>
  )
}
