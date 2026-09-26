export const UPGRADE_ROUTE = '/settings?tab=subscription'

export function getUpgradeRoute(feature) {
  if (!feature) return UPGRADE_ROUTE
  return `${UPGRADE_ROUTE}&feature=${encodeURIComponent(feature)}`
}
