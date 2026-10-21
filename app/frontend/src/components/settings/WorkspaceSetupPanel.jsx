import { Link } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { useOnboarding } from '../../contexts/useOnboarding'
import { useSubscription } from '../../hooks/useSubscription'
import usePermissions from '../../hooks/usePermissions'
import { ReadinessCard } from '../GettingStarted'
import {
  getVisibleReadinessItems,
  readinessProgress,
} from '../../lib/workspaceReadiness'

export default function WorkspaceSetupPanel() {
  const { checklist, checklistDismissed, dismissChecklist } = useOnboarding()
  const { isFeatureAvailable } = useSubscription()
  const { isAdmin, role } = usePermissions()

  const visibleItems = getVisibleReadinessItems({ isFeatureAvailable, isAdmin, role })
  const { completed: completedCount, total, percent } = readinessProgress(checklist, visibleItems)

  if (!isAdmin) {
    return (
      <div className="bg-white dark:bg-dark-card rounded-2xl ring-1 ring-brand-100 dark:ring-white/10 p-8 text-center">
        <p className="text-sm text-slate-500 dark:text-dark-text-secondary">
          Workspace setup is managed by your admin. Ask them to review Settings → Setup.
        </p>
        <Link
          to="/analyze"
          className="inline-flex items-center gap-2 mt-4 px-4 py-2 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700"
        >
          <Sparkles className="w-4 h-4" />
          Screen resumes
        </Link>
      </div>
    )
  }

  if (checklistDismissed && percent === 100) {
    return (
      <div className="bg-white dark:bg-dark-card rounded-2xl ring-1 ring-brand-100 dark:ring-white/10 p-8">
        <h2 className="text-lg font-bold text-brand-900 dark:text-brand-100 mb-2">Setup complete</h2>
        <p className="text-sm text-slate-500 dark:text-dark-text-secondary">
          Your workspace readiness checklist is complete. You can revisit tasks anytime from the dashboard widget.
        </p>
      </div>
    )
  }

  return (
    <ReadinessCard
      compact
      visibleItems={visibleItems}
      checklist={checklist}
      completedCount={completedCount}
      total={total}
      percent={percent}
      onDismiss={checklistDismissed ? null : dismissChecklist}
      showStarterHint={!isFeatureAvailable('requisitions')}
      className="max-w-none shadow-sm ring-1 ring-brand-100 dark:ring-white/10"
    />
  )
}
