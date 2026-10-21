import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/useAuth'

export default function MFAEnrollGate({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return children
  if (!user) return children
  if (!user.mfa_required || user.mfa_enabled) return children

  const params = new URLSearchParams(location.search)
  const onSecurityTab =
    location.pathname.startsWith('/settings') && params.get('tab') === 'security'
  if (onSecurityTab) return children

  return <Navigate to="/settings?tab=security" replace />
}
