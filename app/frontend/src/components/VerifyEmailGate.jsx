import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Mail, RefreshCw, LogOut, Sparkles } from 'lucide-react'
import { useAuth } from '../contexts/useAuth'
import { resendVerificationEmail } from '../lib/api'

/**
 * Blocks the app until the user's email is verified.
 * Platform admins are exempt.
 */
export default function VerifyEmailGate({ children }) {
  const { user, logout, loading } = useAuth()
  const [resending, setResending] = useState(false)
  const [resent, setResent] = useState(false)
  const [error, setError] = useState('')

  if (loading || !user) return children

  const isPlatformAdmin = user.is_platform_admin === true || !!user.platform_role
  if (user.email_verified || isPlatformAdmin) return children

  const handleResend = async () => {
    setResending(true)
    setError('')
    try {
      await resendVerificationEmail(user.email)
      setResent(true)
      setTimeout(() => setResent(false), 5000)
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not resend email.')
    } finally {
      setResending(false)
    }
  }

  const workspaceSlug = sessionStorage.getItem('aria_workspace_slug') || ''

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-4">
      <div className="w-full max-w-md text-center">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-brand-600 to-brand-500 shadow-brand-lg mb-4">
          <Sparkles className="w-7 h-7 text-white" />
        </div>
        <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand-xl p-8">
          <Mail className="w-12 h-12 text-amber-500 mx-auto mb-4" />
          <h1 className="text-xl font-bold text-brand-900 mb-2">Verify your email to continue</h1>
          <p className="text-sm text-slate-600 mb-4">
            We sent a verification link to <strong>{user.email}</strong>.
            Check your inbox and click the link before using ARIA.
          </p>
          {workspaceSlug && (
            <p className="text-xs text-slate-500 mb-4">
              Workspace slug: <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">{workspaceSlug}</code>
            </p>
          )}
          {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
          <button
            type="button"
            onClick={handleResend}
            disabled={resending}
            className="inline-flex items-center gap-2 text-sm font-semibold text-brand-600 hover:text-brand-700 disabled:opacity-50 mb-4"
          >
            <RefreshCw className={`w-4 h-4 ${resending ? 'animate-spin' : ''}`} />
            {resent ? 'Email sent!' : 'Resend verification email'}
          </button>
          <div className="flex flex-col gap-2 pt-2 border-t border-slate-100">
            <Link to="/check-email" className="text-sm text-slate-500 hover:text-brand-600">
              Open check-email instructions
            </Link>
            <button
              type="button"
              onClick={logout}
              className="inline-flex items-center justify-center gap-2 text-sm text-slate-500 hover:text-slate-700"
            >
              <LogOut className="w-4 h-4" /> Sign out
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
