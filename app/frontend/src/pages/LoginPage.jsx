import { useState, useEffect } from 'react'
import { Link, useNavigate, useSearchParams, useLocation } from 'react-router-dom'
import { Sparkles, Eye, EyeOff, AlertCircle, ArrowRight, Building2 } from 'lucide-react'
import { trackOnboardingEvent } from '../lib/onboardingAnalytics'
import { TRUST } from '../lib/uxLabels'
import { useAuth } from '../contexts/AuthContext'
import OAuthButtons from '../components/OAuthButtons'
import { FloatingInput } from '../components/ui'
import { getSSOConfig } from '../lib/api'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const oauthError = searchParams.get('oauth_error')
  const { login } = useAuth()
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [tenantSlug, setTenantSlug] = useState(searchParams.get('workspace') || '')
  const [showPw, setShowPw]     = useState(false)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const [mfaCode, setMfaCode] = useState('')
  const [needsMfa, setNeedsMfa] = useState(false)
  const [ssoState, setSsoState] = useState(null) // { enabled, enforced, login_url }
  const [checkingSSO, setCheckingSSO] = useState(false)

  // Check SSO config when tenant slug changes (with debounce)
  useEffect(() => {
    if (!tenantSlug.trim()) {
      setSsoState(null)
      return
    }
    const timer = setTimeout(async () => {
      setCheckingSSO(true)
      try {
        const cfg = await getSSOConfig(tenantSlug.trim())
        setSsoState(cfg)
      } catch {
        setSsoState(null)
      } finally {
        setCheckingSSO(false)
      }
    }, 400)
    return () => clearTimeout(timer)
  }, [tenantSlug])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await login(email, password, tenantSlug.trim() || undefined, mfaCode.trim() || undefined)
      const isAdmin = data.user?.is_platform_admin === true || !!data.user?.platform_role
      if (isAdmin) {
        navigate('/admin')
      } else if (data.tenant?.onboarding_completed === false) {
        navigate('/onboarding')
      } else {
        trackOnboardingEvent('login_success')
        const from = location.state?.from
        navigate(from && from !== '/login' ? from : '/')
      }
    } catch (err) {
      const detail = err.response?.data?.detail
      if (detail && typeof detail === 'object' && detail.error_code === 'SSO_ENFORCED') {
        setSsoState({
          enabled: true,
          enforced: true,
          login_url: detail.sso_login_url,
        })
        setError('Password login is disabled for your workspace. Please use SSO below.')
      } else if (detail && typeof detail === 'object' && detail.error_code === 'MFA_SETUP_REQUIRED') {
        navigate('/settings?tab=security')
      } else if (typeof detail === 'string' && detail.toLowerCase().includes('mfa')) {
        setNeedsMfa(true)
        setError(detail)
      } else {
        setError(detail || 'Invalid email or password')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleSSOLogin = () => {
    const slug = tenantSlug.trim()
    if (!slug) {
      setError('Please enter your workspace slug to sign in with SSO')
      return
    }
    window.location.href = ssoState?.login_url || `/api/sso/login/${slug}`
  }

  const ssoEnforced = ssoState?.enforced === true
  const ssoEnabled = ssoState?.enabled === true

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-4">
      <div className="w-full max-w-md card-animate">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-brand-600 to-brand-500 shadow-brand-lg mb-4">
            <Sparkles className="w-7 h-7 text-white" />
          </div>
          <h1 className="text-3xl font-extrabold text-brand-900 tracking-tight">
            <span className="text-gradient">ARIA</span>
          </h1>
          <p className="text-slate-500 text-sm mt-1">AI Resume Intelligence by ThetaLogics</p>
        </div>

        {/* Card */}
        <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand-xl p-8">
          <h2 className="text-2xl font-bold text-brand-900 mb-1 tracking-tight">Welcome back</h2>
          <p className="text-slate-500 text-sm mb-6">Sign in to your workspace</p>

          {(error || oauthError) && (
            <div role="alert" className="mb-5 p-3.5 bg-red-50 ring-1 ring-red-200 rounded-2xl flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <p className="text-sm text-red-700">{error || oauthError}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="login-workspace" className="block text-sm font-semibold text-slate-700 mb-1.5">Workspace</label>
              <div className="relative">
                <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  id="login-workspace"
                  type="text"
                  value={tenantSlug}
                  onChange={(e) => setTenantSlug(e.target.value)}
                  required
                  autoComplete="organization"
                  placeholder="your-company"
                  className="w-full pl-9 pr-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 bg-white text-sm text-slate-800 placeholder-slate-400 transition-shadow"
                />
              </div>
              {checkingSSO && (
                <p className="text-xs text-slate-400 mt-1">Checking workspace settings...</p>
              )}
              {ssoState?.found === false && (
                <p className="text-xs text-red-600 mt-1">No workspace matches that name. Use the slug from signup.</p>
              )}
            </div>

            {!ssoEnforced && (
              <>
                <FloatingInput
                  label="Email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  disabled={false}
                  autoComplete="email"
                />
                <div>
                  <label htmlFor="login-password" className="block text-sm font-semibold text-slate-700 mb-1.5">Password</label>
                  <div className="relative">
                    <input
                      id="login-password"
                      type={showPw ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required={!ssoEnforced}
                      autoComplete="current-password"
                      placeholder="••••••••"
                      className="w-full px-4 py-2.5 pr-11 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 bg-white text-sm text-slate-800 placeholder-slate-400 transition-shadow"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPw((v) => !v)}
                      aria-label={showPw ? 'Hide password' : 'Show password'}
                      aria-pressed={showPw}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-brand-600 transition-colors p-1"
                    >
                      {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>
                {(needsMfa || mfaCode) && (
                  <div>
                    <label htmlFor="login-mfa" className="block text-sm font-semibold text-slate-700 mb-1.5">Authenticator code</label>
                    <input
                      id="login-mfa"
                      value={mfaCode}
                      onChange={(e) => setMfaCode(e.target.value)}
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      placeholder="123456"
                      className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 bg-white text-sm text-slate-800"
                    />
                  </div>
                )}
                <div className="flex justify-end">
                  <Link to="/forgot-password" className="text-sm text-brand-600 hover:text-brand-700 transition-colors">
                    Forgot password?
                  </Link>
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-3 rounded-2xl font-bold text-white text-sm flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed btn-brand shadow-brand mt-2"
                >
                  {loading ? (
                    <>
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Signing in...
                    </>
                  ) : (
                    <>Sign In <ArrowRight className="w-4 h-4" /></>
                  )}
                </button>
              </>
            )}

            {ssoEnabled && (
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-slate-200" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-white px-2 text-slate-400">{ssoEnforced ? '' : 'or'}</span>
                </div>
              </div>
            )}

            {ssoEnabled && (
              <button
                type="button"
                onClick={handleSSOLogin}
                className="w-full py-3 rounded-2xl font-bold text-brand-700 text-sm flex items-center justify-center gap-2 ring-1 ring-brand-200 hover:bg-brand-50 transition-colors"
              >
                <Building2 className="w-4 h-4" />
                {ssoEnforced ? 'Sign in with SSO' : 'Sign in with SSO'}
              </button>
            )}
          </form>

          <div className="mt-6">
            <OAuthButtons mode="login" />
          </div>

          <p className="text-center text-sm text-slate-500 mt-6">
            Don't have an account?{' '}
            <Link to="/register" className="text-brand-600 font-semibold hover:text-brand-700 transition-colors">
              Create workspace
            </Link>
          </p>
        </div>

        <p className="text-center text-xs text-slate-400 mt-6">
          {TRUST.authFooter}
        </p>
      </div>
    </div>
  )
}
