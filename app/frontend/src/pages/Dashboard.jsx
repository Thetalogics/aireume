import { useState, useEffect, useCallback, memo } from 'react'
import { useNavigate, Link, Navigate, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Clock, FileText, ArrowRight, RefreshCw,
  LayoutTemplate, AlertCircle, Loader2,
  ChevronRight, UserCheck, HourglassIcon, XCircle, Award, Columns,
  Plus, AlertTriangle, Sparkles, GitCompare, Download,
  Mic, Calendar, TrendingUp, Star
} from 'lucide-react'
import { useAuth } from '../contexts/useAuth'
import usePermissions from '../hooks/usePermissions'
import { useSubscription } from '../hooks/useSubscription'
import { ViewerReadOnlyBanner } from '../components/RequireWriteAccess'
import { PlanLockedButton, PlanUpgradeCard, PlanUsageMeter } from '../components/PlanLockedInline'

import { getDashboardSummary, getDashboardActivity, getInterviewAnalytics } from '../lib/api'
import { safeStr } from '../lib/utils'
import { getScoreColor, getScoreHexColor } from '../lib/constants'
import Skeleton from '../components/Skeleton'
import GettingStarted from '../components/GettingStarted'
import { StaggerContainer, StaggerItem } from '../components/motion'

/** Convert ISO timestamp to relative time string */
function timeAgo(timestamp) {
  if (!timestamp) return ''
  const now = Date.now()
  const then = new Date(timestamp).getTime()
  if (isNaN(then)) return ''
  const diffMs = now - then
  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours > 1 ? 's' : ''} ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days > 1 ? 's' : ''} ago`
  return new Date(timestamp).toLocaleDateString()
}

/** Score badge color classes — delegates to shared getScoreColor from constants */
function scoreBadgeClasses(score) {
  const c = getScoreColor(score)
  if (!c) return 'bg-slate-100 text-slate-500 ring-slate-200'
  return `${c.bg} ${c.text} ${c.ring}`
}

/** Status bar segment Tailwind bg classes (Improvement #1) */
const STATUS_BG = {
  pending: 'bg-slate-300',
  'in-review': 'bg-blue-400',
  shortlisted: 'bg-green-400',
  rejected: 'bg-red-400',
  hired: 'bg-brand-400',
}

/** Human-readable status labels */
const STATUS_LABELS = {
  pending: 'Pending',
  'in-review': 'In Review',
  shortlisted: 'Shortlisted',
  rejected: 'Rejected',
  hired: 'Hired',
}

// ─── Stacked Status Bar (Improvement #1 — Tailwind classes) ──────────────────

function StackedStatusBar({ breakdown = {}, total = 0 }) {
  if (!total) return null
  const segments = Object.entries(breakdown).filter(([, count]) => count > 0)
  if (!segments.length) return null

  return (
    <div className="flex w-full h-2.5 rounded-full overflow-hidden bg-slate-100 dark:bg-white/10">
      {segments.map(([status, count]) => (
        <div
          key={status}
          className={`h-full transition-all duration-500 ${STATUS_BG[status] || 'bg-slate-300'}`}
          style={{ width: `${(count / total) * 100}%` }}
          title={`${STATUS_LABELS[status] || status}: ${count}`}
        />
      ))}
    </div>
  )
}

// ─── Score Ring Gauge (Improvement #3 — SVG circle) ──────────────────────────

const ScoreRingGauge = memo(function ScoreRingGauge({ score, size = 80, strokeWidth = 6 }) {
  if (score == null) return <span className="text-3xl font-extrabold text-slate-400">—</span>

  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - Math.min(score, 100) / 100)

  // Use shared thresholds (80/60/40) so gauge color matches badges everywhere
  const strokeColor = getScoreHexColor(score)

  return (
    <div
      className="relative inline-flex items-center justify-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Average fit score ${Math.round(score)} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          className="stroke-slate-200 dark:stroke-white/15"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke={strokeColor} strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all duration-700"
        />
      </svg>
      <span className="absolute text-lg font-extrabold text-slate-900 dark:text-dark-text-primary">
        {Math.round(score)}
      </span>
    </div>
  )
})

function RecommendationTag({ recommendation }) {
  const rec = safeStr(recommendation)?.toLowerCase() || ''
  let bg = 'bg-slate-100 text-slate-600 ring-slate-200'
  if (rec.includes('shortlist') || rec.includes('strong') || rec.includes('recommend')) {
    bg = 'bg-green-50 text-green-700 ring-green-200'
  } else if (rec.includes('consider') || rec.includes('moderate')) {
    bg = 'bg-amber-50 text-amber-700 ring-amber-200'
  } else if (rec.includes('reject') || rec.includes('not recommend') || rec.includes('weak')) {
    bg = 'bg-red-50 text-red-700 ring-red-200'
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ring-1 ${bg}`}>
      {safeStr(recommendation) || '—'}
    </span>
  )
}

// ─── Activity Helpers (Improvement #5) ────────────────────────────────────────

/** Derive action label and icon from an activity item */
function getActionInfo(item) {
  const rec = (item.recommendation || '').toLowerCase()
  if (rec.includes('shortlist') || rec.includes('strong') || rec.includes('recommend')) {
    return { label: 'Shortlisted', Icon: UserCheck, colorCls: 'text-green-600 bg-green-50' }
  }
  if (rec.includes('reject') || rec.includes('not recommend') || rec.includes('weak')) {
    return { label: 'Rejected', Icon: XCircle, colorCls: 'text-red-600 bg-red-50' }
  }
  return { label: 'Analyzed', Icon: FileText, colorCls: 'text-brand-600 bg-brand-50' }
}

/** Group activities by time period: Today / Yesterday / This Week / Earlier */
function groupActivitiesByTime(activities) {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday)
  startOfYesterday.setDate(startOfYesterday.getDate() - 1)
  const startOfWeek = new Date(startOfToday)
  startOfWeek.setDate(startOfWeek.getDate() - 7)

  const today = [], yesterday = [], thisWeek = [], earlier = []

  for (const item of activities) {
    const ts = new Date(item.timestamp)
    if (isNaN(ts.getTime())) { earlier.push(item); continue }
    if (ts >= startOfToday) today.push(item)
    else if (ts >= startOfYesterday) yesterday.push(item)
    else if (ts >= startOfWeek) thisWeek.push(item)
    else earlier.push(item)
  }

  const groups = []
  if (today.length) groups.push({ label: 'Today', items: today })
  if (yesterday.length) groups.push({ label: 'Yesterday', items: yesterday })
  if (thisWeek.length) groups.push({ label: 'This Week', items: thisWeek })
  if (earlier.length) groups.push({ label: 'Earlier', items: earlier })

  return groups
}

// ─── Main Component ───────────────────────────────────────────────────────────

function DashboardContent() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user } = useAuth()
  const { canWrite } = usePermissions()
  const { isFeatureAvailable } = useSubscription()
  const hasRequisitions = isFeatureAvailable('requisitions')
  const hasCompare = isFeatureAvailable('compare')
  const hasAiInterviews = isFeatureAvailable('ai_interviews')

  const [summary, setSummary] = useState(null)
  const [activity, setActivity] = useState(null)
  const [interviewStats, setInterviewStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [widgetErrors, setWidgetErrors] = useState({})

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [summaryData, activityData, interviewData] = await Promise.allSettled([
        getDashboardSummary(),
        getDashboardActivity(),
        getInterviewAnalytics(),
      ])
      setSummary(summaryData.status === 'fulfilled' ? summaryData.value : null)
      setActivity(activityData.status === 'fulfilled' ? activityData.value : null)
      setInterviewStats(interviewData.status === 'fulfilled' ? interviewData.value : null)
      const nextErrors = {
        summary: summaryData.status === 'rejected' ? (summaryData.reason?.message || 'Failed to load summary') : null,
        activity: activityData.status === 'rejected' ? (activityData.reason?.message || 'Failed to load activity') : null,
        interviews: interviewData.status === 'rejected' ? (interviewData.reason?.message || 'Failed to load interviews') : null,
      }
      setWidgetErrors(nextErrors)
      const failed = Object.entries(nextErrors).filter(([, msg]) => msg).map(([k]) => k)
      setError(failed.length ? `Could not load: ${failed.join(', ')}` : null)
    } catch (err) {
      setError(err.message || 'Failed to load dashboard data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user?.is_platform_admin === true || !!user?.platform_role) return
    fetchData()
  }, [fetchData, user])

  // Platform admins default to /admin — redirect if they land on recruiter dashboard
  if (user?.is_platform_admin === true || !!user?.platform_role) {
    return <Navigate to="/admin" replace />
  }

  // ─── Early returns ────────────────────────────────────────────────────────
  if (loading && !summary) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header skeleton */}
        <div className="mb-6 space-y-2">
          <Skeleton variant="text" width="16rem" className="h-8" />
          <Skeleton variant="text" width="24rem" />
        </div>
        {/* KPI cards skeleton */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <Skeleton variant="card" count={3} />
        </div>
        {/* Activity + Metrics skeleton */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
          <Skeleton variant="card" height="20rem" />
          <Skeleton variant="card" height="20rem" />
        </div>
      </div>
    )
  }

  if (error && !summary) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-red-100 dark:border-red-900/40 p-12 text-center">
          <AlertCircle className="w-12 h-12 text-red-400 mx-auto mb-4" />
          <h2 className="text-lg font-bold text-slate-900 dark:text-dark-text-primary mb-2">Failed to load dashboard</h2>
          <p className="text-sm text-slate-500 dark:text-dark-text-secondary mb-6">{safeStr(error)}</p>
          <button
            onClick={fetchData}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            Retry
          </button>
        </div>
      </div>
    )
  }

  // ─── Derived data ─────────────────────────────────────────────────────────
  const actionItems = summary?.action_items || {}
  const pipelineByReq = (summary?.pipeline_by_requisition || []).map((r) => ({
    requisition_id: r.requisition_id,
    jd_id: r.requisition_id,
    jd_name: r.title,
    title: r.title,
    status: r.status,
    is_calibrated: r.is_calibrated,
    total_candidates: r.total_candidates,
    status_breakdown: r.by_status || {},
    avg_fit_score: r.avg_fit_score,
  }))
  const pipelineByJd = pipelineByReq.length > 0 ? pipelineByReq : (summary?.pipeline_by_jd || []).map((j) => ({
    ...j,
    requisition_id: j.jd_id,
    status_breakdown: j.status_breakdown || j.by_status || {},
  }))
  const weeklyMetrics = summary?.weekly_metrics || {}
  const activities = activity?.activities || []

  const pendingCount = actionItems.pending_review ?? 0
  const shortlistedCount = actionItems.shortlisted_count ?? 0
  const inProgressCount = actionItems.in_progress_analyses ?? 0

  // Sum rejected & hired across all JDs in pipeline
  const rejectedCount = pipelineByJd.reduce(
    (sum, jd) => sum + ((jd.status_breakdown || {}).rejected ?? 0), 0
  )
  const hiredCount = pipelineByJd.reduce(
    (sum, jd) => sum + ((jd.status_breakdown || {}).hired ?? 0), 0
  )

  // Improvement #2: Sort JDs by urgency — most pending first, then most candidates
  const sortedPipelineByJd = [...pipelineByJd].sort((a, b) => {
    const aPending = (a.status_breakdown || {}).pending ?? 0
    const bPending = (b.status_breakdown || {}).pending ?? 0
    if (bPending !== aPending) return bPending - aPending
    return (b.total_candidates ?? 0) - (a.total_candidates ?? 0)
  })

  // Improvement #5: Group activities by time period
  const activityGroups = groupActivitiesByTime(activities)

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* ── Workspace Readiness ───────────────────────────────────────────── */}
      <div className="mb-8">
        <GettingStarted />
      </div>

      {(location.state?.viewerBlocked || !canWrite) && <ViewerReadOnlyBanner />}

      {/* ── Improvement #4 & #6: Compact Header + Quick Actions ──────────── */}
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight text-brand-900 dark:text-brand-100 whitespace-nowrap">
          Welcome back{user?.email ? `, ${user.email.split('@')[0]}` : ''}
        </h1>
        <div className="grid grid-cols-2 sm:flex items-center gap-2 sm:gap-3 w-full sm:w-auto">
          <PlanUsageMeter />
          {canWrite && (
          <button
            onClick={() => navigate('/analyze')}
            className="flex items-center justify-center gap-2 px-4 py-2.5 bg-brand-600 text-white rounded-lg hover:bg-brand-700 transition-colors shadow-sm font-medium text-sm"
          >
            <Sparkles className="w-4 h-4" />
            Screen Resumes
          </button>
          )}

          {canWrite && hasRequisitions ? (
          <button
            onClick={() => navigate('/requisitions')}
            className="flex items-center justify-center gap-2 px-4 py-2.5 border border-slate-200 dark:border-white/15 bg-white dark:bg-dark-card text-slate-700 dark:text-dark-text-primary rounded-lg hover:bg-slate-50 dark:hover:bg-dark-card-elevated hover:border-brand-300 dark:hover:border-brand-500/40 transition-colors font-medium text-sm"
          >
            <Plus className="w-4 h-4" />
            Create Requisition
          </button>
          ) : canWrite ? (
            <PlanLockedButton feature="requisitions">Create Requisition</PlanLockedButton>
          ) : null}

          {hasCompare ? (
          <button
            onClick={() => navigate('/compare')}
            className="flex items-center justify-center gap-2 px-4 py-2.5 border border-slate-200 dark:border-white/15 bg-white dark:bg-dark-card text-slate-700 dark:text-dark-text-primary rounded-lg hover:bg-slate-50 dark:hover:bg-dark-card-elevated hover:border-brand-300 dark:hover:border-brand-500/40 transition-colors font-medium text-sm"
          >
            <GitCompare className="w-4 h-4" />
            Compare Candidates
          </button>
          ) : (
            <PlanLockedButton feature="compare">Compare Candidates</PlanLockedButton>
          )}
        </div>
        {loading && <Loader2 className="w-5 h-5 text-brand-500 animate-spin shrink-0" />}
      </div>

      {Object.values(widgetErrors).some(Boolean) && (
        <div className="flex flex-wrap gap-2 mb-4" role="status">
          {widgetErrors.summary && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 px-2.5 py-1 rounded-full ring-1 ring-red-200 dark:ring-red-800">
              Summary: {widgetErrors.summary}
            </span>
          )}
          {widgetErrors.activity && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 px-2.5 py-1 rounded-full ring-1 ring-red-200 dark:ring-red-800">
              Activity: {widgetErrors.activity}
            </span>
          )}
          {widgetErrors.interviews && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 px-2.5 py-1 rounded-full ring-1 ring-red-200 dark:ring-red-800">
              Interviews: {widgetErrors.interviews}
            </span>
          )}
        </div>
      )}

      {/* ── Action Items Bar (Improvement #2 — ring on Pending > 50) ─────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
        {/* Pending Review */}
        <button
          onClick={() => navigate('/candidates?status=pending')}
          className={`bg-amber-50 dark:bg-amber-950/30 hover:bg-amber-100 dark:hover:bg-amber-950/50 border rounded-xl shadow-sm p-5 text-left transition-colors group ${
            pendingCount > 50
              ? 'border-amber-300 dark:border-amber-700 ring-2 ring-orange-400 dark:ring-orange-600'
              : 'border-amber-200 dark:border-amber-800'
          }`}
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-3xl font-extrabold text-amber-700">{pendingCount}</p>
              <p className="text-sm font-semibold text-amber-600 mt-1">Pending Review</p>
            </div>
            <div className={`w-11 h-11 rounded-xl flex items-center justify-center ${
              pendingCount > 50 ? 'bg-orange-200/60 dark:bg-orange-900/35' : 'bg-amber-200/60 dark:bg-amber-900/35'
            }`}>
              <Clock className={`w-5 h-5 ${pendingCount > 50 ? 'text-orange-700' : 'text-amber-700'}`} />
            </div>
          </div>
          {pendingCount > 50 && (
            <p className="text-xs font-semibold text-orange-600 mt-2 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />
              Needs attention
            </p>
          )}
          <div className="flex items-center gap-1 mt-3 text-xs text-amber-600 font-medium">
            View candidates
            <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
          </div>
        </button>

        {/* In Progress */}
        <button
          onClick={() => navigate('/candidates?narrative_status=processing')}
          className="bg-blue-50 dark:bg-blue-950/30 hover:bg-blue-100 dark:hover:bg-blue-950/50 border border-blue-200 dark:border-blue-800 rounded-xl shadow-sm p-5 text-left transition-colors group"
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-3xl font-extrabold text-blue-700">{inProgressCount}</p>
              <p className="text-sm font-semibold text-blue-600 mt-1">In Progress</p>
            </div>
            <div className="w-11 h-11 rounded-xl bg-blue-200/60 dark:bg-blue-900/35 flex items-center justify-center">
              <HourglassIcon className="w-5 h-5 text-blue-700" />
            </div>
          </div>
          <div className="flex items-center gap-1 mt-3 text-xs text-blue-600 font-medium">
            View candidates
            <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
          </div>
        </button>

        {/* Shortlisted */}
        <button
          onClick={() => navigate('/candidates?status=shortlisted')}
          className="bg-green-50 dark:bg-green-950/30 hover:bg-green-100 dark:hover:bg-green-950/50 border border-green-200 dark:border-green-800 rounded-xl shadow-sm p-5 text-left transition-colors group"
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-3xl font-extrabold text-green-700">{shortlistedCount}</p>
              <p className="text-sm font-semibold text-green-600 mt-1">Shortlisted</p>
            </div>
            <div className="w-11 h-11 rounded-xl bg-green-200/60 dark:bg-green-900/35 flex items-center justify-center">
              <UserCheck className="w-5 h-5 text-green-700" />
            </div>
          </div>
          <div className="flex items-center gap-1 mt-3 text-xs text-green-600 font-medium">
            View candidates
            <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
          </div>
        </button>

        {/* Hired */}
        <button
          onClick={() => navigate('/candidates?status=hired')}
          className="bg-indigo-50 dark:bg-indigo-950/30 hover:bg-indigo-100 dark:hover:bg-indigo-950/50 border border-indigo-200 dark:border-indigo-800 rounded-xl shadow-sm p-5 text-left transition-colors group"
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-3xl font-extrabold text-indigo-700">{hiredCount}</p>
              <p className="text-sm font-semibold text-indigo-600 mt-1">Hired</p>
            </div>
            <div className="w-11 h-11 rounded-xl bg-indigo-200/60 dark:bg-indigo-900/35 flex items-center justify-center">
              <Award className="w-5 h-5 text-indigo-700" />
            </div>
          </div>
          <div className="flex items-center gap-1 mt-3 text-xs text-indigo-600 font-medium">
            View candidates
            <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
          </div>
        </button>

        {/* Rejected */}
        <button
          onClick={() => navigate('/candidates?status=rejected')}
          className="bg-red-50 dark:bg-red-950/30 hover:bg-red-100 dark:hover:bg-red-950/50 border border-red-200 dark:border-red-800 rounded-xl shadow-sm p-5 text-left transition-colors group"
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-3xl font-extrabold text-red-700">{rejectedCount}</p>
              <p className="text-sm font-semibold text-red-600 mt-1">Rejected</p>
            </div>
            <div className="w-11 h-11 rounded-xl bg-red-200/60 dark:bg-red-900/35 flex items-center justify-center">
              <XCircle className="w-5 h-5 text-red-700" />
            </div>
          </div>
          <div className="flex items-center gap-1 mt-3 text-xs text-red-600 font-medium">
            View candidates
            <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
          </div>
        </button>
      </div>

      {/* ── Pipeline Summary (Improvements #1 & #2 — progress bars, urgency, sorting) */}
      <div className="mb-8">
        <h2 className="text-xl font-bold tracking-tight text-brand-900 dark:text-dark-text-primary mb-4">Requisitions</h2>
        {!hasRequisitions ? (
          <PlanUpgradeCard
            feature="requisitions"
            title="Organize roles with Requisitions"
            description="Starter includes core resume screening on Analyze. Upgrade to Growth to create requisitions, track pipeline per role, and collaborate with hiring managers."
            icon={LayoutTemplate}
          />
        ) : sortedPipelineByJd.length > 0 ? (
          <div className="max-h-[480px] overflow-y-auto scroll-smooth pr-2">
            <StaggerContainer className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {sortedPipelineByJd.map((jd) => {
              const total = jd.total_candidates ?? 0
              const breakdown = jd.status_breakdown || {}
              const avgScore = jd.avg_fit_score
              const pendingInJd = breakdown.pending ?? 0
              const segments = Object.entries(breakdown).filter(([, c]) => c > 0)

              return (
                <StaggerItem key={jd.requisition_id || jd.jd_id}>
                <div
                  className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-slate-100 dark:border-white/10 p-5 hover:shadow-md dark:hover:ring-1 dark:hover:ring-white/10 transition-shadow"
                >
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="text-sm font-bold text-slate-900 dark:text-dark-text-primary leading-snug line-clamp-2 flex-1 mr-2">
                      {safeStr(jd.title || jd.jd_name) || 'Untitled requisition'}
                    </h3>
                    {avgScore != null && (
                      <span className={`shrink-0 px-2.5 py-1 rounded-full text-xs font-bold ring-1 ${scoreBadgeClasses(avgScore)}`}>
                        {avgScore}
                      </span>
                    )}
                  </div>

                  <p className="text-xs text-slate-500 dark:text-dark-text-secondary mb-3">
                    {total} candidate{total !== 1 ? 's' : ''}
                  </p>

                  {/* Stacked status bar */}
                  <StackedStatusBar breakdown={breakdown} total={total} />

                  {/* Improvement #1: Compact legend with · separator */}
                  <div className="mt-2 text-xs text-slate-500 dark:text-dark-text-secondary flex flex-wrap items-center">
                    {segments.flatMap(([status, count], i) => {
                      const el = (
                        <span key={status} className="inline-flex items-center gap-1">
                          <span className={`w-1.5 h-1.5 rounded-full ${STATUS_BG[status] || 'bg-slate-300'}`} />
                          {count} {STATUS_LABELS[status] || status}
                        </span>
                      )
                      return i < segments.length - 1
                        ? [el, <span key={`sep-${i}`} className="mx-1 text-slate-300">·</span>]
                        : [el]
                    })}
                  </div>

                  {/* Improvement #2: Urgency indicator */}
                  {pendingInJd > 0 && (
                    <p className="text-xs font-medium text-orange-600 dark:text-orange-300 mt-2 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" />
                      {pendingInJd} pending review
                    </p>
                  )}

                  <div className="flex items-center gap-4 mt-4">
                    <Link
                      to={`/requisitions/${jd.requisition_id || jd.jd_id}`}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-brand-600 dark:text-brand-300 hover:text-brand-700 dark:hover:text-brand-200"
                    >
                      Open requisition
                      <ChevronRight className="w-3 h-3" />
                    </Link>
                    <Link
                      to={`/requisitions/${jd.requisition_id || jd.jd_id}?tab=pipeline`}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 dark:text-indigo-300 hover:text-indigo-700 dark:hover:text-indigo-200"
                    >
                      <Columns className="w-3 h-3" />
                      View Pipeline
                    </Link>
                  </div>
                </div>
                </StaggerItem>
              )
            })}
            </StaggerContainer>
          </div>
        ) : (
          <div className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-slate-100 dark:border-white/10 p-10 text-center">
            <LayoutTemplate className="w-10 h-10 text-slate-300 dark:text-dark-text-secondary mx-auto mb-3" />
            <p className="text-sm font-medium text-slate-500 dark:text-dark-text-secondary">No active job descriptions.</p>
            <p className="text-xs text-slate-400 dark:text-dark-text-secondary mt-1 mb-4">Create one in JD Library to start screening candidates.</p>
            <Link
              to="/requisitions"
              className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-xl text-sm font-medium hover:bg-brand-700 transition-colors"
            >
              <LayoutTemplate className="w-4 h-4" />
              JD Library
            </Link>
          </div>
        )}
      </div>

      {/* ── Two-Column Layout: Activity + Metrics ──────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Left — Activity Feed (Improvement #5 — time groupings, clickable, action type) */}
        <div className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-slate-100 dark:border-white/10 p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold tracking-tight text-brand-900 dark:text-brand-100">Recent Activity</h2>
            <Link
              to="/candidates"
              className="text-xs font-semibold text-brand-600 dark:text-brand-300 hover:text-brand-700 dark:hover:text-brand-200 flex items-center gap-1"
            >
              View all
              <ArrowRight className="w-3 h-3" />
            </Link>
          </div>

          {activities.length > 0 ? (
            <div className="space-y-0 max-h-96 overflow-y-auto pr-1">
              {activityGroups.map(group => (
                <div key={group.label}>
                  <p className="text-[10px] font-bold text-slate-400 dark:text-dark-text-secondary uppercase tracking-widest px-3 pt-3 pb-1 sticky top-0 bg-white dark:bg-dark-card z-10">
                    {group.label}
                  </p>
                  {group.items.map((item, idx) => {
                    const { label: actionLabel, Icon: ActionIcon, colorCls } = getActionInfo(item)
                    const hasLink = !!(item.candidate_id || item.result_id)
                    return (
                      <button
                        key={`${group.label}-${idx}`}
                        onClick={() => {
                          if (item.candidate_id) {
                            navigate(`/candidates/${item.candidate_id}`)
                          } else if (item.result_id) {
                            navigate(`/report?id=${item.result_id}`)
                          }
                        }}
                        className={`w-full text-left flex items-center gap-3 p-3 rounded-xl transition-colors ${
                          hasLink ? 'hover:bg-slate-50 dark:hover:bg-dark-card-elevated cursor-pointer' : 'cursor-default'
                        }`}
                      >
                        {/* Icon with action-based color */}
                        <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${colorCls}`}>
                          <ActionIcon className="w-4 h-4" />
                        </div>

                        {/* Content */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400 dark:text-dark-text-secondary">
                              {actionLabel}
                            </span>
                            <span className="text-sm font-semibold text-slate-900 dark:text-dark-text-primary truncate">
                              {safeStr(item.candidate_name) || 'Candidate'}
                            </span>
                            {item.fit_score != null && (
                              <span className={`shrink-0 px-2 py-0.5 rounded-full text-xs font-bold ring-1 ${scoreBadgeClasses(item.fit_score)}`}>
                                {item.fit_score}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-xs text-slate-400 dark:text-dark-text-secondary truncate">{safeStr(item.jd_name) || 'JD'}</span>
                            <span className="text-xs text-slate-300 dark:text-dark-text-secondary">·</span>
                            <span className="text-xs text-slate-400 dark:text-dark-text-secondary shrink-0">{timeAgo(item.timestamp)}</span>
                          </div>
                        </div>

                        {/* Recommendation tag */}
                        {item.recommendation && (
                          <RecommendationTag recommendation={item.recommendation} />
                        )}
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-12">
              <Clock className="w-10 h-10 text-slate-300 dark:text-dark-text-secondary mx-auto mb-3" />
              <p className="text-sm font-medium text-slate-500 dark:text-dark-text-secondary">No recent activity</p>
              <p className="text-xs text-slate-400 dark:text-dark-text-secondary mt-1">Analyses will appear here as they complete</p>
            </div>
          )}
        </div>

        {/* Right — Weekly Metrics (Improvements #3 & #7 — ring gauge, trend indicators) */}
        <div className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-slate-100 dark:border-white/10 p-6">
          <h2 className="text-xl font-bold tracking-tight text-brand-900 dark:text-brand-100 mb-5">Weekly Metrics</h2>

          <div className="grid grid-cols-2 gap-4 mb-6">
            {/* Analyses This Week — Improvement #7: color-coded */}
            <div className="bg-slate-50 dark:bg-dark-card-elevated rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">Analyses This Week</p>
              <p className={`text-3xl font-extrabold ${
                (weeklyMetrics.analyses_this_week ?? 0) > 0 ? 'text-green-700' : 'text-slate-400'
              }`}>
                {weeklyMetrics.analyses_this_week ?? 0}
              </p>
            </div>

            {/* Improvement #3: Avg Fit Score — Ring Gauge */}
            <div className="bg-slate-50 dark:bg-dark-card-elevated rounded-xl p-4 flex flex-col items-center justify-center">
              <p className="text-xs font-semibold text-slate-500 dark:text-dark-text-secondary uppercase tracking-wide mb-2">Avg Fit Score</p>
              <ScoreRingGauge score={weeklyMetrics.avg_fit_score} />
            </div>

            {/* Improvement #7: Shortlist Rate — color-coded */}
            <div className="bg-slate-50 dark:bg-dark-card-elevated rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">Shortlist Rate</p>
              <p className={`text-3xl font-extrabold ${
                weeklyMetrics.shortlist_rate == null ? 'text-slate-400' :
                weeklyMetrics.shortlist_rate >= 40 ? 'text-green-700' :
                weeklyMetrics.shortlist_rate >= 20 ? 'text-amber-700' :
                'text-red-700'
              }`}>
                {weeklyMetrics.shortlist_rate != null
                  ? `${Math.round(weeklyMetrics.shortlist_rate)}%`
                  : '—'}
              </p>
            </div>

            {/* Active Pipeline mini-summary */}
            <div className="bg-slate-50 dark:bg-dark-card-elevated rounded-xl p-4">
              <p className="text-xs font-semibold text-slate-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">
                Active Pipeline
              </p>
              {(() => {
                const totals = pipelineByJd.reduce(
                  (acc, jd) => {
                    const b = jd.status_breakdown || {}
                    acc.pending += b.pending ?? 0
                    acc['in-review'] += b['in-review'] ?? 0
                    acc.shortlisted += b.shortlisted ?? 0
                    acc.rejected += b.rejected ?? 0
                    acc.hired += b.hired ?? 0
                    return acc
                  },
                  { pending: 0, 'in-review': 0, shortlisted: 0, rejected: 0, hired: 0 }
                )
                const grandTotal = Object.values(totals).reduce((s, c) => s + c, 0)
                const pipelineSegments = Object.entries(totals).filter(([, c]) => c > 0)
                return (
                  <>
                    <p className="text-2xl font-extrabold text-brand-900 dark:text-brand-100 mb-2">
                      {grandTotal} <span className="text-sm font-semibold text-slate-400 dark:text-dark-text-secondary">candidates</span>
                    </p>
                    {grandTotal > 0 && (
                      <div className="flex w-full h-3 rounded-full overflow-hidden bg-slate-200 dark:bg-white/10 mb-2">
                        {pipelineSegments.map(([status, count]) => (
                          <div
                            key={status}
                            className={`h-full transition-all duration-500 ${STATUS_BG[status] || 'bg-slate-300'}`}
                            style={{ width: `${(count / grandTotal) * 100}%` }}
                            title={`${STATUS_LABELS[status] || status}: ${count}`}
                          />
                        ))}
                      </div>
                    )}
                    <div className="flex flex-wrap gap-x-2.5 gap-y-0.5">
                      {Object.entries(totals).map(([status, count]) => (
                        <span key={status} className="flex items-center gap-1 text-[10px] text-slate-500 dark:text-dark-text-secondary">
                          <span className={`w-1.5 h-1.5 rounded-full inline-block ${STATUS_BG[status] || 'bg-slate-300'}`} />
                          {STATUS_LABELS[status] || status} {count}
                        </span>
                      ))}
                    </div>
                    <Link
                      to="/pipeline"
                      className="inline-flex items-center gap-1 mt-2 text-[10px] font-semibold text-indigo-600 dark:text-indigo-300 hover:text-indigo-700 dark:hover:text-indigo-200"
                    >
                      View Full Pipeline <ArrowRight className="w-2.5 h-2.5" />
                    </Link>
                  </>
                )
              })()}
            </div>
          </div>

          {/* Top Skill Gaps */}
          {weeklyMetrics.top_skill_gaps && weeklyMetrics.top_skill_gaps.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500 dark:text-dark-text-secondary uppercase tracking-wide mb-2">Top Skill Gaps</p>
              <div className="flex flex-wrap gap-2">
                {weeklyMetrics.top_skill_gaps.map((skill, i) => (
                  <span
                    key={i}
                    className={`px-3 py-1 rounded-full text-xs font-semibold ring-1 ${
                      i < 2
                        ? 'bg-red-50 text-red-700 ring-red-200'
                        : 'bg-orange-50 text-orange-700 ring-orange-200'
                    }`}
                  >
                    {safeStr(skill)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── AI Interviews Widget ─────────────────────────────────────────── */}
      {hasAiInterviews && (
      <div className="mb-8">
        <div className="bg-white dark:bg-dark-card rounded-2xl shadow-sm border border-neutral-200 dark:border-white/10 p-6">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 rounded-xl bg-brand-50 dark:bg-brand-950/40 flex items-center justify-center">
                <Mic className="w-5 h-5 text-brand-600 dark:text-brand-300" />
              </div>
              <h2 className="text-xl font-bold tracking-tight text-neutral-900 dark:text-dark-text-primary">AI Interviews</h2>
            </div>
            <Link
              to="/ai-interviews"
              className="text-xs font-semibold text-brand-600 dark:text-brand-300 hover:text-brand-700 dark:hover:text-brand-200 flex items-center gap-1"
            >
              View all
              <ArrowRight className="w-3 h-3" />
            </Link>
          </div>

          {interviewStats ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-neutral-50 dark:bg-dark-card-elevated rounded-xl p-4">
                <p className="text-xs font-semibold text-neutral-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">Total Interviews</p>
                <p className="text-2xl font-extrabold text-neutral-900 dark:text-dark-text-primary">
                  {(interviewStats.voice?.total ?? 0) + (interviewStats.recruiter?.total ?? 0)}
                </p>
              </div>
              <div className="bg-neutral-50 dark:bg-dark-card-elevated rounded-xl p-4">
                <p className="text-xs font-semibold text-neutral-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">Completed</p>
                <p className="text-2xl font-extrabold text-green-700">
                  {interviewStats.voice?.completed ?? 0}
                </p>
              </div>
              <div className="bg-neutral-50 dark:bg-dark-card-elevated rounded-xl p-4">
                <p className="text-xs font-semibold text-neutral-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">Quick</p>
                <p className="text-2xl font-extrabold text-blue-700">
                  {interviewStats.voice?.quick_count ?? 0}
                </p>
              </div>
              <div className="bg-neutral-50 dark:bg-dark-card-elevated rounded-xl p-4">
                <p className="text-xs font-semibold text-neutral-500 dark:text-dark-text-secondary uppercase tracking-wide mb-1">Standard / Deep</p>
                <p className="text-2xl font-extrabold text-purple-700">
                  {interviewStats.voice?.deep_count ?? 0}
                </p>
              </div>
            </div>
          ) : (
            <div className="text-center py-8">
              <Mic className="w-10 h-10 text-neutral-300 dark:text-dark-text-secondary mx-auto mb-3" />
              <p className="text-sm font-medium text-neutral-500 dark:text-dark-text-secondary">No AI interviews yet</p>
              <Link
                to="/ai-interviews"
                className="inline-flex items-center gap-2 mt-3 px-4 py-2 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700 transition-all duration-200 shadow-sm"
              >
                <Mic className="w-4 h-4" />
                Start First Interview
              </Link>
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  )
}

export default DashboardContent
