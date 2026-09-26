import { Link } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { useSubscription } from '../hooks/useSubscription'
import { getUpgradeRoute } from '../lib/routes'

const FEATURE_LABELS = {
  requisitions: 'Requisitions',
  pipeline: 'Pipeline',
  compare: 'Compare',
  analytics: 'Analytics',
  ai_interviews: 'AI Interviews',
  video_analysis: 'Video Analysis',
  export_excel: 'Export',
  white_label: 'White-label Branding',
  hm_workflow: 'Hiring Manager Workflows',
  api_access: 'API Access',
  custom_weights: 'Custom Scoring Weights',
  batch_analysis: 'Batch Screening',
  transcript_analysis: 'Transcript Analysis',
  email_generation: 'Email Generation',
}

export function PlanUpgradePrompt({ feature }) {
  const { getCurrentPlan } = useSubscription()
  const plan = getCurrentPlan()
  const label = FEATURE_LABELS[feature] || feature
  const planName = plan?.plan?.display_name || plan?.plan?.name || 'your current plan'

  return (
    <div className="max-w-lg mx-auto mt-16 px-6 text-center">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-brand-50 text-brand-600 mb-4">
        <Lock className="w-7 h-7" />
      </div>
      <h1 className="text-2xl font-bold text-slate-900 dark:text-dark-text-primary mb-2">
        {label} is not on {planName}
      </h1>
      <p className="text-slate-600 dark:text-dark-text-secondary mb-6">
        Upgrade your subscription to unlock this feature for your workspace.
      </p>
      <Link
        to={getUpgradeRoute(feature)}
        className="inline-flex items-center justify-center px-5 py-2.5 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700 transition-colors"
      >
        View plans & upgrade
      </Link>
    </div>
  )
}

export default function RequirePlanFeature({ feature, children }) {
  const { isFeatureAvailable, loading } = useSubscription()

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!isFeatureAvailable(feature)) {
    return <PlanUpgradePrompt feature={feature} />
  }

  return children
}
