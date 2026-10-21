import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Brain, BarChart3, Settings, Plus, Filter, Search, RefreshCw,
  Clock, CheckCircle2, XCircle, AlertTriangle, Loader2, Calendar,
  FileText, ChevronRight, Download, Save, X, TrendingUp, Users,
  Target, Zap,
} from 'lucide-react'
import {
  getRecruiterSessions, getRecruiterAnalytics, getRecruiterConfig,
  updateRecruiterConfig, cancelRecruiterSession, exportRecruiterSessions,
} from '../lib/api'
import InterviewInitiateModal from '../components/InterviewInitiateModal'
import { StaggerContainer, StaggerItem } from '../components/motion'
import useConfirm from '../hooks/useConfirm'

/* ── Status / Recommendation mappings ────────────────── */

const STATUS_CONFIG = {
  pending_strategy: { label: 'Preparing',   color: 'bg-slate-100 text-slate-700' },
  strategy_ready:   { label: 'Ready',       color: 'bg-blue-100 text-blue-700' },
  scheduled:        { label: 'Scheduled',   color: 'bg-amber-100 text-amber-700' },
  in_progress:      { label: 'In Progress', color: 'bg-indigo-100 text-indigo-700' },
  completed:        { label: 'Completed',   color: 'bg-green-100 text-green-700' },
  failed:           { label: 'Failed',      color: 'bg-red-100 text-red-700' },
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

const TRIGGER_LABELS = {
  manual: 'Manual',
  auto_shortlist: 'Auto (Shortlist)',
  auto_score_threshold: 'Auto (Score)',
  scheduled: 'Scheduled',
}

/* ── Reusable sub-components ─────────────────────────── */

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending_strategy
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

function StatCard({ label, value, icon: Icon, color }) {
  return (
    <div className="bg-white/90 backdrop-blur-md rounded-2xl ring-1 ring-brand-100 shadow-brand px-4 py-3 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${color}`}>
        <Icon className="w-4 h-4" />
      </div>
      <div>
        <p className="text-xs text-slate-400 font-medium">{label}</p>
        <p className="text-lg font-bold text-slate-800">{value}</p>
      </div>
    </div>
  )
}

function Section({ title, icon: Icon, children, description, action }) {
  return (
    <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6">
      <div className="flex items-start justify-between mb-5">
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-2xl bg-brand-50 ring-1 ring-brand-100 flex items-center justify-center shrink-0">
            <Icon className="w-5 h-5 text-brand-600" />
          </div>
          <div>
            <h3 className="font-extrabold text-brand-900 text-lg tracking-tight">{title}</h3>
            {description && <p className="text-sm text-slate-500 mt-0.5">{description}</p>}
          </div>
        </div>
        {action}
      </div>
      {children}
    </div>
  )
}

/* ── Main Page ───────────────────────────────────────── */

export default function RecruiterInterviewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { confirm, dialog } = useConfirm()
  const initialTab = ['sessions', 'analytics', 'config'].includes(searchParams.get('tab'))
    ? searchParams.get('tab')
    : 'sessions'
  const [sessions, setSessions] = useState([])
  const [analytics, setAnalytics] = useState(null)
  const [config, setConfig] = useState(null)
  const [configDraft, setConfigDraft] = useState(null)
  const [loading, setLoading] = useState(true)
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState(initialTab)

  useEffect(() => {
    const tab = searchParams.get('tab')
    if (tab === 'sessions' || tab === 'analytics' || tab === 'config') setActiveTab(tab)
  }, [searchParams])
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState(null)
  const [showInitiateModal, setShowInitiateModal] = useState(false)

  const configDirty = config && configDraft && JSON.stringify(config) !== JSON.stringify(configDraft)

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true)
      const [analyticsData, cfg] = await Promise.all([
        getRecruiterAnalytics().catch(() => null),
        getRecruiterConfig().catch(() => null),
      ])
      setAnalytics(analyticsData)
      if (cfg) { setConfig(cfg); setConfigDraft(cfg) }
    } catch (err) {
      setError(err.message || 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchSessions = useCallback(async (params = {}) => {
    try {
      setSessionsLoading(true)
      const sess = await getRecruiterSessions({ limit: 50, ...params })
      setSessions(Array.isArray(sess) ? sess : sess.sessions || [])
    } catch { /* ignore */ }
    finally { setSessionsLoading(false) }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  // Debounced search + filter
  useEffect(() => {
    const timeout = setTimeout(() => {
      const params = { limit: 50 }
      if (searchQuery.length >= 2) params.search = searchQuery
      if (statusFilter) params.status = statusFilter
      fetchSessions(params)
    }, 300)
    return () => clearTimeout(timeout)
  }, [searchQuery, statusFilter, fetchSessions])

  async function handleSaveConfig() {
    try {
      setSaving(true)
      const updated = await updateRecruiterConfig(configDraft)
      setConfig(updated)
      setConfigDraft(updated)
    } catch (err) {
      setError(err.message || 'Failed to save config')
    } finally {
      setSaving(false)
    }
  }

  async function handleCancelSession(sessionId) {
    const ok = await confirm({
      title: 'Cancel session',
      message: 'Cancel this interview session?',
      confirmLabel: 'Cancel session',
      danger: true,
    })
    if (!ok) return
    try {
      await cancelRecruiterSession(sessionId)
      fetchSessions()
    } catch (err) {
      setError(err.message || 'Failed to cancel')
    }
  }

  async function handleExport() {
    try {
      await exportRecruiterSessions({ status: statusFilter || undefined })
    } catch (err) {
      setError(err.message || 'Failed to export')
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
        {/* Breadcrumb back to the interview hub */}
        <button
          onClick={() => navigate('/ai-interviews')}
          className="inline-flex items-center gap-1 text-sm font-semibold text-slate-500 hover:text-brand-700 transition-colors mb-4"
        >
          <ChevronRight className="w-4 h-4 rotate-180" />
          AI Interviews
        </button>
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center shadow-lg shadow-brand-200">
              <Brain className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-3xl font-extrabold text-brand-900 tracking-tight">AI Recruiter</h1>
              <p className="text-sm text-slate-500">AI-powered structured interviews</p>
            </div>
          </div>
          <button
            onClick={() => setShowInitiateModal(true)}
            className="flex items-center gap-2 px-5 py-2.5 bg-brand-600 text-white rounded-xl font-semibold text-sm hover:bg-brand-700 transition-all shadow-sm shadow-brand-200"
          >
            <Plus className="w-4 h-4" />
            Initiate Interview
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-white/60 backdrop-blur rounded-2xl p-1 ring-1 ring-brand-100 w-fit">
          {[
            { key: 'sessions', label: 'Sessions', icon: Users },
            { key: 'analytics', label: 'Analytics', icon: BarChart3 },
            { key: 'config', label: 'Configuration', icon: Settings },
          ].map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
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

        {error && (
          <div className="mb-6 p-4 bg-red-50 ring-1 ring-red-200 rounded-2xl flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
            <button onClick={() => setError(null)} aria-label="Dismiss error" className="ml-auto text-red-400 hover:text-red-600">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* ── Sessions Tab ─────────────────────────────── */}
        {activeTab === 'sessions' && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
            {/* Quick stats */}
            {analytics && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard label="Total Sessions" value={analytics.total_sessions ?? analytics.total ?? 0} icon={Users} color="text-brand-600 bg-brand-50" />
                <StatCard label="Completed" value={analytics.completed ?? 0} icon={CheckCircle2} color="text-emerald-600 bg-emerald-50" />
                <StatCard label="Avg Score" value={analytics.avg_score != null ? Math.round(analytics.avg_score) : '—'} icon={Target} color="text-blue-600 bg-blue-50" />
                <StatCard label="Hire Rate" value={analytics.hire_rate != null ? `${analytics.hire_rate}%` : '—'} icon={TrendingUp} color="text-amber-600 bg-amber-50" />
              </div>
            )}

            {sessionsLoading && (
              <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-brand-600 animate-spin" /></div>
            )}

            {!sessionsLoading && sessions.length === 0 && (
              <div className="text-center py-16">
                <Brain className="w-12 h-12 text-slate-300 mx-auto mb-4" />
                <h3 className="text-lg font-bold text-slate-500 mb-2">No interview sessions yet</h3>
                <p className="text-sm text-slate-400 mb-6">Initiate an AI interview to get started</p>
                <button
                  onClick={() => setShowInitiateModal(true)}
                  className="px-5 py-2.5 bg-brand-600 text-white rounded-xl font-semibold text-sm hover:bg-brand-700 transition-all"
                >
                  Start First Interview
                </button>
              </div>
            )}

            {!sessionsLoading && sessions.length > 0 && (
              <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand overflow-hidden">
                {/* Session list header */}
                <div className="px-6 py-4 border-b border-brand-50 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="font-bold text-brand-900">Recent Sessions</h3>
                    <div className="flex items-center gap-2">
                      <button onClick={handleExport} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-slate-500 hover:text-brand-600 hover:bg-brand-50 rounded-lg transition-colors" title="Export">
                        <Download className="w-3.5 h-3.5" /> Export
                      </button>
                      <button onClick={() => fetchSessions()} className="p-2 rounded-lg hover:bg-brand-50 text-slate-400 hover:text-brand-600 transition-colors">
                        <RefreshCw className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="relative flex-1">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        placeholder="Search by candidate or JD..."
                        className="w-full pl-9 pr-3 py-2 bg-slate-50 rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 text-sm outline-none transition-all"
                      />
                    </div>
                    <div className="flex gap-1 flex-wrap">
                      {[null, 'in_progress', 'completed', 'failed', 'cancelled'].map(s => (
                        <button
                          key={s || 'all'}
                          onClick={() => setStatusFilter(s)}
                          className={`px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                            statusFilter === s
                              ? 'bg-brand-600 text-white shadow-sm'
                              : 'text-slate-500 hover:bg-slate-100'
                          }`}
                        >
                          {s ? STATUS_CONFIG[s]?.label || s : 'All'}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Session rows */}
                <StaggerContainer className="divide-y divide-brand-50">
                  {sessions.map(session => (
                    <StaggerItem key={session.id}>
                      <div className="flex items-center">
                      <button
                        type="button"
                        className="flex-1 flex items-center gap-3 px-6 py-4 hover:bg-brand-50/50 transition-colors text-left bg-transparent border-0 min-w-0"
                        onClick={() => navigate(`/recruiter-interviews/${session.id}`)}
                      >
                        <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
                          session.status === 'completed' ? 'bg-emerald-100' :
                          session.status === 'in_progress' ? 'bg-indigo-100' :
                          session.status === 'failed' ? 'bg-red-100' :
                          'bg-slate-100'
                        }`}>
                          <Brain className={`w-5 h-5 ${
                            session.status === 'completed' ? 'text-emerald-600' :
                            session.status === 'in_progress' ? 'text-indigo-600' :
                            session.status === 'failed' ? 'text-red-600' :
                            'text-slate-500'
                          }`} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="text-sm font-semibold text-brand-600">
                              {session.candidate_name || `Candidate #${session.candidate_id}`}
                            </span>
                            <StatusBadge status={session.status} />
                            {session.recommendation && (
                              <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${RECOMMENDATION_COLORS[session.recommendation] || 'bg-slate-100 text-slate-600'}`}>
                                {session.recommendation.replace(/_/g, ' ')}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 text-xs text-slate-400">
                            {session.jd_title && (
                              <span className="flex items-center gap-1"><FileText className="w-3 h-3" />{session.jd_title}</span>
                            )}
                            <span className="flex items-center gap-1">
                              <Zap className="w-3 h-3" />
                              {TRIGGER_LABELS[session.trigger_type] || session.trigger_type || 'Manual'}
                            </span>
                            {session.duration_seconds != null && (
                              <span className="flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {Math.floor(session.duration_seconds / 60)}m {session.duration_seconds % 60}s
                              </span>
                            )}
                            {session.overall_score != null && (
                              <span className={`font-bold ${
                                session.overall_score >= 70 ? 'text-emerald-600' :
                                session.overall_score >= 40 ? 'text-amber-600' :
                                'text-red-600'
                              }`}>
                                Score: {session.overall_score}
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="text-xs text-slate-400 shrink-0">
                          {session.created_at ? new Date(session.created_at).toLocaleDateString('en-US') : ''}
                        </span>
                        <ChevronRight className="w-4 h-4 text-slate-300 shrink-0" />
                      </button>
                        {['pending_strategy', 'strategy_ready', 'scheduled', 'failed'].includes(session.status) && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); handleCancelSession(session.id) }}
                            className="p-1.5 rounded-lg hover:bg-red-100 text-red-400 transition-colors shrink-0 mr-4"
                            title="Cancel"
                            aria-label="Cancel session"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </StaggerItem>
                  ))}
                </StaggerContainer>
              </div>
            )}
          </motion.div>
        )}

        {/* ── Analytics Tab ────────────────────────────── */}
        {activeTab === 'analytics' && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
            {!analytics ? (
              <div className="text-center py-16 text-slate-400">
                <BarChart3 className="w-12 h-12 mx-auto mb-4 opacity-40" />
                <p className="text-sm">No analytics data available yet</p>
              </div>
            ) : (
              <>
                {/* Stat cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <StatCard label="Total Sessions" value={analytics.total_sessions ?? analytics.total ?? 0} icon={Users} color="text-brand-600 bg-brand-50" />
                  <StatCard label="Completed" value={analytics.completed ?? 0} icon={CheckCircle2} color="text-emerald-600 bg-emerald-50" />
                  <StatCard label="Avg Score" value={analytics.avg_score != null ? Math.round(analytics.avg_score) : '—'} icon={Target} color="text-blue-600 bg-blue-50" />
                  <StatCard label="Hire Rate" value={analytics.hire_rate != null ? `${analytics.hire_rate}%` : '—'} icon={TrendingUp} color="text-amber-600 bg-amber-50" />
                </div>

                {/* Recommendation distribution */}
                {analytics.recommendation_distribution && (
                  <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6">
                    <h3 className="font-bold text-brand-900 mb-4">Recommendation Distribution</h3>
                    <div className="space-y-3">
                      {Object.entries(analytics.recommendation_distribution).map(([key, count]) => {
                        const total = Object.values(analytics.recommendation_distribution).reduce((a, b) => a + b, 0)
                        const pct = total > 0 ? Math.round((count / total) * 100) : 0
                        return (
                          <div key={key} className="flex items-center gap-3">
                            <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold w-28 text-center ${RECOMMENDATION_COLORS[key] || 'bg-slate-100 text-slate-600'}`}>
                              {key.replace(/_/g, ' ')}
                            </span>
                            <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${RECOMMENDATION_COLORS[key]?.split(' ')[0]?.replace('bg-', 'bg-') || 'bg-slate-400'}`}
                                style={{ width: `${pct}%`, opacity: 0.7 }}
                              />
                            </div>
                            <span className="text-sm font-bold text-slate-700 w-12 text-right">{count}</span>
                            <span className="text-xs text-slate-400 w-10 text-right">{pct}%</span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Status breakdown */}
                {analytics.status_breakdown && (
                  <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6">
                    <h3 className="font-bold text-brand-900 mb-4">Status Breakdown</h3>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                      {Object.entries(analytics.status_breakdown).map(([status, count]) => (
                        <div key={status} className="p-3 bg-slate-50 rounded-xl text-center">
                          <p className="text-xs text-slate-400 mb-1">{STATUS_CONFIG[status]?.label || status}</p>
                          <p className="text-xl font-bold text-slate-800">{count}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </motion.div>
        )}

        {/* ── Configuration Tab ────────────────────────── */}
        {activeTab === 'config' && configDraft && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
            <Section
              title="Auto-Trigger Settings"
              icon={Zap}
              description="Configure when AI interviews are automatically triggered"
              action={
                <button
                  onClick={handleSaveConfig}
                  disabled={saving || !configDirty}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 rounded-lg transition-colors disabled:opacity-50"
                >
                  {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                  Save
                </button>
              }
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id="auto_trigger_enabled"
                    checked={configDraft.auto_trigger_enabled ?? false}
                    onChange={e => setConfigDraft({ ...configDraft, auto_trigger_enabled: e.target.checked })}
                    className="w-4 h-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                  />
                  <label htmlFor="auto_trigger_enabled" className="text-sm font-medium text-slate-700">
                    Enable auto-trigger on shortlist
                  </label>
                </div>
                <div>
                  <label htmlFor="recruiterinterviewpage-min-fit-score-for-auto-trigger-1" className="block text-sm font-semibold text-slate-700 mb-1.5">Min fit score for auto-trigger</label>
                  <input id="recruiterinterviewpage-min-fit-score-for-auto-trigger-1"
                    type="number"
                    value={configDraft.min_score_threshold ?? 60}
                    onChange={e => setConfigDraft({ ...configDraft, min_score_threshold: parseInt(e.target.value) || 60 })}
                    min={0} max={100}
                    className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 text-sm outline-none transition-all"
                  />
                </div>
                <div>
                  <label htmlFor="recruiterinterviewpage-default-duration-minutes-2" className="block text-sm font-semibold text-slate-700 mb-1.5">Default duration (minutes)</label>
                  <input id="recruiterinterviewpage-default-duration-minutes-2"
                    type="number"
                    value={configDraft.default_duration_minutes ?? 30}
                    onChange={e => setConfigDraft({ ...configDraft, default_duration_minutes: parseInt(e.target.value) || 30 })}
                    min={10} max={60}
                    className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 text-sm outline-none transition-all"
                  />
                </div>
                <div>
                  <label htmlFor="recruiterinterviewpage-max-concurrent-sessions-3" className="block text-sm font-semibold text-slate-700 mb-1.5">Max concurrent sessions</label>
                  <input id="recruiterinterviewpage-max-concurrent-sessions-3"
                    type="number"
                    value={configDraft.max_concurrent ?? 3}
                    onChange={e => setConfigDraft({ ...configDraft, max_concurrent: parseInt(e.target.value) || 3 })}
                    min={1} max={10}
                    className="w-full px-3.5 py-2.5 bg-white rounded-xl ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500 text-sm outline-none transition-all"
                  />
                </div>
              </div>
            </Section>

            {/* Sticky save bar */}
            {configDirty && (
              <div className="sticky bottom-0 z-10 bg-white/95 backdrop-blur-md rounded-2xl ring-1 ring-brand-200 shadow-lg px-6 py-4 flex items-center justify-between mt-2">
                <p className="text-sm font-medium text-slate-600">You have unsaved changes</p>
                <div className="flex gap-3">
                  <button
                    onClick={() => setConfigDraft(config)}
                    className="px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100 rounded-xl transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSaveConfig}
                    disabled={saving}
                    className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 rounded-xl transition-colors disabled:opacity-50 shadow-sm shadow-brand-200"
                  >
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    Save Changes
                  </button>
                </div>
              </div>
            )}
          </motion.div>
        )}

        {/* Initiate Modal */}
        {showInitiateModal && (
          <InterviewInitiateModal
            onClose={() => setShowInitiateModal(false)}
            onSuccess={() => { fetchSessions(); fetchAll() }}
          />
        )}
      </div>
      {dialog}
    </div>
  )
}
