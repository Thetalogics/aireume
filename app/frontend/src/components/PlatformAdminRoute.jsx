import { Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/useAuth'

function isPlatformAdmin(user) {
  if (!user) return false
  return user.is_platform_admin === true || !!user.platform_role
}

export default function PlatformAdminRoute({ children }) {
  const { user, loading } = useAuth()

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-500"></div>
    </div>
  )
  if (!isPlatformAdmin(user)) return <Navigate to="/" replace />
  return children
}
