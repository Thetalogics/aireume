import { useSubscription } from '../hooks/useSubscription'

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
