import { Link } from 'react-router-dom'
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
  if (!isPlatformAdmin(user)) return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-bold text-slate-900 dark:text-white">Admin is unavailable</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">This account cannot open platform admin.</p>
        <Link to="/" className="btn-brand mt-6 inline-flex">Back home</Link>
      </div>
    </div>
  )
  return children
}
