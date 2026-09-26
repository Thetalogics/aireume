import { Link } from 'react-router-dom'
import { Lock, Sparkles } from 'lucide-react'
import { useSubscription } from '../hooks/useSubscription'
import { getUpgradeRoute } from '../lib/routes'

const FEATURE_HINTS = {
  requisitions: { label: 'Requisitions', tier: 'Growth' },
  pipeline: { label: 'Pipeline', tier: 'Growth' },
  compare: { label: 'Compare', tier: 'Growth' },
  export_excel: { label: 'Export', tier: 'Growth' },
  email_generation: { label: 'Email templates', tier: 'Growth' },
  analytics: { label: 'Analytics', tier: 'Agency' },
  api_access: { label: 'API access', tier: 'Agency' },
  ai_interviews: { label: 'AI Interviews', tier: 'Business' },
  video_analysis: { label: 'Video analysis', tier: 'Business' },
  custom_weights: { label: 'Custom weights', tier: 'Business' },
  white_label: { label: 'White-label', tier: 'Business' },
  hm_workflow: { label: 'HM workflows', tier: 'Growth' },
}

/** Disabled button with lock — links to billing on click */
export function PlanLockedButton({ feature, children, className = '' }) {
  const hint = FEATURE_HINTS[feature] || { label: feature, tier: 'a higher plan' }
  return (
    <Link
      to={getUpgradeRoute(feature)}
      title={`${hint.label} is on ${hint.tier}+`}
      className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 border border-slate-200 bg-slate-50 text-slate-500 rounded-lg text-sm font-medium cursor-pointer hover:bg-slate-100 transition-colors ${className}`}
    >
      <Lock className="w-3.5 h-3.5 shrink-0" />
      {children}
    </Link>
  )
}

/** Compact upsell card for empty dashboard sections */
export function PlanUpgradeCard({ feature, title, description, icon: Icon = Sparkles }) {
  const hint = FEATURE_HINTS[feature] || { label: feature, tier: 'a higher plan' }
  return (
    <div className="bg-white dark:bg-dark-card rounded-xl shadow-sm border border-slate-100 dark:border-white/10 p-8 text-center">
      <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-brand-50 dark:bg-brand-950/40 text-brand-600 dark:text-brand-300 mb-4">
        <Icon className="w-6 h-6" />
      </div>
      <h3 className="text-base font-bold text-slate-900 dark:text-dark-text-primary mb-1">{title || `${hint.label} on ${hint.tier}`}</h3>
      <p className="text-sm text-slate-500 dark:text-dark-text-secondary max-w-md mx-auto mb-4">
        {description || `Upgrade to ${hint.tier} or higher to unlock this feature for your workspace.`}
      </p>
      <Link
        to={getUpgradeRoute(feature)}
        className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700 transition-colors"
      >
        View plans
      </Link>
    </div>
  )
}

/** Usage meter for dashboard header */
export function PlanUsageMeter() {
  const { subscription, getUsageStats, getCurrentPlan } = useSubscription()
  const usage = getUsageStats()
  const plan = getCurrentPlan()
  if (!subscription || !usage) return null

  const limit = usage.analysesLimit
  const used = usage.analysesUsed
  const unlimited = limit < 0
  const pct = unlimited ? 0 : Math.min(100, usage.percentUsed || 0)
  const planName = plan?.plan?.display_name || plan?.plan?.name || 'Starter'

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-white dark:bg-dark-card rounded-xl ring-1 ring-slate-200 dark:ring-white/10 text-sm">
      <span className="font-semibold text-slate-700 dark:text-dark-text-primary">{planName}</span>
      <span className="text-slate-300 dark:text-dark-text-secondary">|</span>
      {unlimited ? (
        <span className="text-slate-500 dark:text-dark-text-secondary">Unlimited analyses</span>
      ) : (
        <>
          <span className={`font-medium ${pct >= 90 ? 'text-red-600' : pct >= 75 ? 'text-amber-600' : 'text-slate-600 dark:text-dark-text-secondary'}`}>
            {used}/{limit} analyses
          </span>
          <div className="hidden sm:block w-24 h-1.5 rounded-full bg-slate-100 dark:bg-white/10 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${pct >= 90 ? 'bg-red-500' : pct >= 75 ? 'bg-amber-500' : 'bg-brand-500'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </>
      )}
    </div>
  )
}

export function usePlanLimits() {
  const { subscription } = useSubscription()
  const limits = subscription?.current_plan?.plan?.limits || {}
  return {
    batchSize: Number(limits.batch_size) > 0 ? Number(limits.batch_size) : 10,
    analysesLimit: limits.analyses_per_month ?? 30,
    teamLimit: limits.team_members ?? 2,
    planName: subscription?.current_plan?.plan?.name || 'starter',
    displayName: subscription?.current_plan?.plan?.display_name || 'Starter',
  }
}
