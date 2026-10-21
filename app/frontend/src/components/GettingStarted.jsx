import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Check, Circle, ChevronRight, X, PartyPopper, Lock } from 'lucide-react'
import { useOnboarding } from '../contexts/useOnboarding'
import { useSubscription } from '../hooks/useSubscription'
import usePermissions from '../hooks/usePermissions'
import {
  getVisibleReadinessItems,
  isReadinessComplete,
  readinessProgress,
} from '../lib/workspaceReadiness'
import { trackOnboardingEvent } from '../lib/onboardingAnalytics'

/**
 * Dashboard widget — Workspace Readiness checklist.
 */
export default function GettingStarted({ compact = false }) {
  const { checklist, checklistDismissed, dismissChecklist } = useOnboarding()
  const { isFeatureAvailable } = useSubscription()
  const { isAdmin, role } = usePermissions()

  const visibleItems = getVisibleReadinessItems({ isFeatureAvailable, isAdmin, role })
  const { completed: completedCount, total, percent } = readinessProgress(checklist, visibleItems)
  const allComplete = isReadinessComplete(checklist, visibleItems)

  useEffect(() => {
    if (!allComplete) return undefined
    trackOnboardingEvent('readiness_completed')
    const timer = setTimeout(() => {
      dismissChecklist()
    }, 5000)
    return () => clearTimeout(timer)
  }, [allComplete, dismissChecklist])

  if (checklistDismissed) return null

  if (allComplete) {
    return (
      <div className="bg-white dark:bg-dark-card rounded-xl shadow-lg border border-brand-200 dark:border-white/10 p-6 w-full max-w-sm animate-pulse-once">
        <div className="text-center py-4">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-brand-100 dark:bg-brand-950/40 mb-4">
            <PartyPopper className="w-7 h-7 text-brand-600 dark:text-brand-300" />
          </div>
          <h3 className="text-lg font-bold text-brand-900 dark:text-brand-100 mb-1">Workspace ready!</h3>
          <p className="text-sm text-slate-500 dark:text-dark-text-secondary">
            You&apos;ve completed setup. ARIA is ready for day-to-day hiring.
          </p>
        </div>
      </div>
    )
  }

  return (
    <ReadinessCard
      compact={compact}
      visibleItems={visibleItems}
      checklist={checklist}
      completedCount={completedCount}
      total={total}
      percent={percent}
      onDismiss={dismissChecklist}
      showStarterHint={!isFeatureAvailable('requisitions')}
    />
  )
}

/** Shared readiness UI — used on dashboard and Settings → Setup */
export function ReadinessCard({
  compact = false,
  visibleItems,
  checklist,
  completedCount,
  total,
  percent,
  onDismiss,
  showStarterHint = false,
  className = '',
}) {
  return (
    <div
      className={`bg-white dark:bg-dark-card rounded-xl shadow-lg border border-slate-200 dark:border-white/10 p-6 w-full ${
        compact ? 'max-w-none' : 'max-w-sm'
      } relative ${className}`}
    >
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="absolute top-4 right-4 text-slate-400 dark:text-dark-text-secondary hover:text-slate-600 dark:hover:text-dark-text-primary transition-colors"
          aria-label="Dismiss workspace readiness"
        >
          <X className="w-4 h-4" />
        </button>
      )}

      <h3 className={`font-bold text-brand-900 dark:text-brand-100 pr-6 ${compact ? 'text-lg' : 'text-base'}`}>
        Workspace Readiness
      </h3>
      <p className="text-xs text-slate-500 dark:text-dark-text-secondary mt-1 mb-4">
        Complete these steps to get ARIA production-ready for your team.
      </p>

      <div className="space-y-2 mb-5">
        {visibleItems.map(({ key, label, description, href, Icon }) => {
          const done = checklist[key]
          return (
            <Link
              key={key}
              to={href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors group ${
                done ? 'bg-green-50/60 dark:bg-green-950/30' : 'hover:bg-slate-50 dark:hover:bg-dark-card-elevated'
              }`}
            >
              {done ? (
                <span className="flex items-center justify-center w-5 h-5 rounded-full bg-green-500 text-white shrink-0">
                  <Check className="w-3 h-3" />
                </span>
              ) : (
                <Circle className="w-5 h-5 text-slate-300 shrink-0" />
              )}
              <span className="flex-1 min-w-0">
                <span
                  className={`block text-sm font-medium ${
                    done
                      ? 'text-slate-400 dark:text-dark-text-secondary line-through'
                      : 'text-slate-700 dark:text-dark-text-primary group-hover:text-brand-600 dark:group-hover:text-brand-300'
                  }`}
                >
                  {label}
                </span>
                {compact && description && !done && (
                  <span className="block text-xs text-slate-400 dark:text-dark-text-secondary truncate">
                    {description}
                  </span>
                )}
              </span>
              {!done && (
                <ChevronRight className="w-3.5 h-3.5 text-slate-300 group-hover:text-brand-500 shrink-0 transition-colors" />
              )}
            </Link>
          )
        })}
      </div>

      {showStarterHint && (
        <p className="text-xs text-slate-500 dark:text-dark-text-secondary mb-4 flex items-start gap-1.5">
          <Lock className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          On Starter, use{' '}
          <Link to="/analyze" className="text-brand-600 dark:text-brand-300 font-medium hover:underline">
            Analyze
          </Link>{' '}
          to screen resumes — no requisition needed.
        </p>
      )}

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-semibold text-slate-500 dark:text-dark-text-secondary">Progress</span>
          <span className="text-xs font-bold text-brand-600 dark:text-brand-300">
            {completedCount}/{total} complete ({percent}%)
          </span>
        </div>
        <div className="h-2 rounded-full bg-slate-100 dark:bg-white/10 overflow-hidden">
          <div
            className="h-full rounded-full bg-brand-500 transition-all duration-500 ease-out"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
    </div>
  )
}
