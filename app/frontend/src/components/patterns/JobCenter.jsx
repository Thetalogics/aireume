import { useState } from 'react'
import { Activity, CheckCircle2, Loader2, XCircle, Sparkles, ChevronRight, FileSearch, Mic } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Sheet } from '../ui'
import { useNotification } from '../../contexts/useNotification'
import { useQueuePolling } from '../../hooks/useQueuePolling'

function StatusIcon({ status }) {
  if (status === 'completed' || status === 'ready') {
    return <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
  }
  if (status === 'error' || status === 'failed') {
    return <XCircle className="w-4 h-4 text-red-500 shrink-0" />
  }
  return <Loader2 className="w-4 h-4 text-brand-600 dark:text-brand-400 animate-spin shrink-0" />
}

function EmptyJobs({ message, hint, icon: Icon = Activity, action }) {
  return (
    <div className="text-center py-10 px-4 rounded-2xl ring-1 ring-brand-100 dark:ring-white/10 bg-brand-50/30 dark:bg-dark-card-elevated/50">
      <div className="w-12 h-12 mx-auto mb-3 rounded-2xl bg-white dark:bg-dark-card flex items-center justify-center ring-1 ring-brand-100 dark:ring-white/10">
        <Icon className="w-6 h-6 text-brand-400 dark:text-brand-300 opacity-80" />
      </div>
      <p className="text-sm font-semibold text-slate-700 dark:text-dark-text-primary">{message}</p>
      {hint && <p className="text-xs text-slate-500 dark:text-dark-text-secondary mt-2 max-w-xs mx-auto leading-relaxed">{hint}</p>}
      {action}
    </div>
  )
}

function JobCenterPanel({ analysisProgress, enrichmentJobs, queueJobs, notifications, unreadCount, onClose }) {
  const [tab, setTab] = useState('jobs')

  const tabs = [
    { id: 'jobs', label: 'Screening' },
    { id: 'enrichment', label: 'Enrichment' },
    { id: 'alerts', label: `Alerts${unreadCount ? ` (${unreadCount})` : ''}` },
  ]

  const activeQueue = queueJobs.filter((j) => !['completed', 'failed', 'cancelled'].includes(j.status))
  const activeEnrichment = enrichmentJobs.filter((j) => j.status === 'pending' || j.status === 'processing')

  return (
    <div className="flex flex-col h-full min-h-[100dvh] md:min-h-0 md:h-full bg-white dark:bg-dark-card">
      <div className="px-5 py-4 border-b border-brand-100 dark:border-white/10 flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-lg font-bold text-brand-900 dark:text-dark-text-primary">Activity Center</h2>
          <p className="text-xs text-slate-500 dark:text-dark-text-secondary">Background analyses and AI enrichment</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-2 rounded-lg hover:bg-brand-50 dark:hover:bg-dark-card-elevated text-slate-400 dark:text-dark-text-secondary text-xl leading-none"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      <div className="flex gap-1 px-4 pt-3 pb-2 shrink-0 border-b border-brand-50 dark:border-white/5">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              tab === t.id
                ? 'bg-brand-600 text-white'
                : 'text-slate-600 dark:text-dark-text-secondary hover:bg-brand-50 dark:hover:bg-dark-card-elevated'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        {tab === 'jobs' && (
          <div className="space-y-3">
            {queueJobs.length > 0 && (
              <div className="rounded-xl ring-1 ring-indigo-100 dark:ring-indigo-900/50 p-4 bg-indigo-50/40 dark:bg-indigo-950/30">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-bold text-indigo-900 dark:text-indigo-200">Background queue</span>
                  <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-300">
                    {queueJobs.filter((j) => j.status === 'completed').length}/{queueJobs.length}
                  </span>
                </div>
                <ul className="space-y-1.5 max-h-40 overflow-y-auto">
                  {queueJobs.slice(0, 20).map((job) => (
                    <li key={job.id} className="flex items-center gap-2 text-sm">
                      <StatusIcon status={job.status} />
                      <span className="truncate text-slate-700 dark:text-dark-text-primary flex-1">{job.filename}</span>
                      {job.progress > 0 && job.status !== 'completed' && (
                        <span className="text-xs text-slate-400 dark:text-dark-text-secondary">{job.progress}%</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {analysisProgress.isActive ? (
              <div className="rounded-xl ring-1 ring-brand-100 dark:ring-white/10 p-4 bg-brand-50/50 dark:bg-dark-card-elevated/50">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-bold text-brand-900 dark:text-dark-text-primary">Batch analysis</span>
                  <span className="text-xs font-semibold text-brand-600 dark:text-brand-300">
                    {analysisProgress.completed}/{analysisProgress.total}
                  </span>
                </div>
                <div className="h-1.5 bg-brand-100 dark:bg-white/10 rounded-full overflow-hidden mb-3">
                  <div
                    className="h-full bg-gradient-to-r from-brand-500 to-brand-600 transition-all duration-500"
                    style={{
                      width: `${
                        analysisProgress.total
                          ? (analysisProgress.completed / analysisProgress.total) * 100
                          : 0
                      }%`,
                    }}
                  />
                </div>
                <ul className="space-y-1.5 max-h-48 overflow-y-auto">
                  {analysisProgress.items.map((item) => (
                    <li key={item.filename} className="flex items-center gap-2 text-sm">
                      <StatusIcon status={item.status} />
                      <span className="truncate text-slate-700 dark:text-dark-text-primary">{item.filename}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : queueJobs.length === 0 ? (
              <EmptyJobs
                message="No active batch analyses"
                hint="Upload resumes on Analyze to start screening. Progress appears here while files are processed."
                icon={FileSearch}
                action={(
                  <Link
                    to="/analyze"
                    onClick={onClose}
                    className="inline-flex items-center gap-1 mt-4 text-xs font-semibold text-brand-600 dark:text-brand-300 hover:underline"
                  >
                    Go to Analyze
                    <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                )}
              />
            ) : null}
          </div>
        )}

        {tab === 'enrichment' && (
          <div className="space-y-2">
            {enrichmentJobs.length === 0 ? (
              <EmptyJobs
                message="No reports enriching"
                hint="Interview kits and AI narratives run in the background after screening completes."
                icon={Sparkles}
                action={(
                  <Link
                    to="/candidates"
                    onClick={onClose}
                    className="inline-flex items-center gap-1 mt-4 text-xs font-semibold text-brand-600 dark:text-brand-300 hover:underline"
                  >
                    View candidates
                    <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                )}
              />
            ) : (
              enrichmentJobs.map((job) => (
                <Link
                  key={job.id}
                  to={job.href || '#'}
                  onClick={onClose}
                  className="flex items-center gap-3 p-3 rounded-xl ring-1 ring-brand-100 dark:ring-white/10 hover:bg-brand-50/60 dark:hover:bg-dark-card-elevated transition-colors"
                >
                  <StatusIcon status={job.status} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-slate-800 dark:text-dark-text-primary truncate">{job.label}</p>
                    <p className="text-xs text-slate-500 dark:text-dark-text-secondary">{job.phase || job.status}</p>
                  </div>
                  <ChevronRight className="w-4 h-4 text-slate-300 dark:text-dark-text-secondary shrink-0" />
                </Link>
              ))
            )}
          </div>
        )}

        {tab === 'alerts' && (
          <div className="space-y-2">
            {notifications.length === 0 ? (
              <EmptyJobs
                message="You're all caught up"
                hint="System alerts and screening notifications will show here."
                icon={Activity}
              />
            ) : (
              notifications.slice(0, 15).map((n) => (
                <div
                  key={n.id}
                  className={`p-3 rounded-xl ring-1 ring-brand-100 dark:ring-white/10 bg-white dark:bg-dark-card-elevated ${n.read ? 'opacity-60' : ''}`}
                >
                  <p className="text-sm font-semibold text-slate-800 dark:text-dark-text-primary">{n.title}</p>
                  {n.message && <p className="text-xs text-slate-500 dark:text-dark-text-secondary mt-0.5">{n.message}</p>}
                  {n.href && (
                    <Link
                      to={n.href}
                      onClick={onClose}
                      className="text-xs text-brand-600 dark:text-brand-300 font-semibold mt-1 inline-block"
                    >
                      View →
                    </Link>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {(activeQueue.length > 0 || activeEnrichment.length > 0 || analysisProgress.isActive) && (
        <div className="shrink-0 px-4 py-3 border-t border-brand-100 dark:border-white/10 bg-brand-50/40 dark:bg-dark-card-elevated/40 text-xs text-slate-600 dark:text-dark-text-secondary">
          {analysisProgress.isActive && `${analysisProgress.completed}/${analysisProgress.total} analyses · `}
          {activeQueue.length > 0 && `${activeQueue.length} queued · `}
          {activeEnrichment.length > 0 && `${activeEnrichment.length} enriching`}
        </div>
      )}
    </div>
  )
}

/** Nav activity button + sheet — replaces standalone ProgressBadge */
export default function JobCenter() {
  const [open, setOpen] = useState(false)
  const {
    analysisProgress,
    enrichmentJobs,
    queueJobs,
    notifications,
    unreadCount,
  } = useNotification()

  useQueuePolling()

  const activeAnalysis = analysisProgress.isActive
  const activeQueue = queueJobs.filter(
    (j) => !['completed', 'failed', 'cancelled'].includes(j.status)
  ).length
  const activeEnrichment = enrichmentJobs.filter(
    (j) => j.status === 'pending' || j.status === 'processing'
  ).length
  const totalActive = (activeAnalysis ? 1 : 0) + activeQueue + activeEnrichment

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`relative flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
          totalActive > 0
            ? 'bg-brand-50 dark:bg-brand-950/40 text-brand-700 dark:text-brand-300 border border-brand-200 dark:border-brand-800 hover:bg-brand-100 dark:hover:bg-brand-950/60'
            : 'text-slate-600 dark:text-dark-text-secondary hover:bg-brand-50 dark:hover:bg-dark-card-elevated border border-transparent'
        }`}
        aria-label={totalActive > 0 ? `Activity, ${totalActive} in progress` : 'Activity center'}
      >
        <Activity className="w-4 h-4" />
        <span className="hidden sm:inline">
          {activeAnalysis
            ? `Analyzing ${analysisProgress.completed}/${analysisProgress.total}`
            : activeQueue > 0
            ? `Queue ${queueJobs.filter((j) => j.status === 'completed').length}/${queueJobs.length}`
            : 'Activity'}
        </span>
        {totalActive > 0 && (
          <span className="min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full bg-brand-600 text-white text-[10px] font-bold">
            {totalActive > 9 ? '9+' : totalActive}
          </span>
        )}
      </button>

      <Sheet isOpen={open} onClose={() => setOpen(false)} maxWidth="max-w-md">
        <JobCenterPanel
          analysisProgress={analysisProgress}
          enrichmentJobs={enrichmentJobs}
          queueJobs={queueJobs}
          notifications={notifications}
          unreadCount={unreadCount}
          onClose={() => setOpen(false)}
        />
      </Sheet>
    </>
  )
}
