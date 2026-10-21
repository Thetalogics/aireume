import { useAuth } from '../contexts/useAuth'
import { getPermissions } from '../lib/rbac'

/** Tenant RBAC permissions derived from the logged-in user. */
export function usePermissions() {
  const { user } = useAuth()
  return getPermissions(user)
}

export default usePermissions
