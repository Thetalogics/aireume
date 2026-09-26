import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Sparkles, Eye, EyeOff, AlertCircle, Building2, ArrowRight } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { TRUST } from '../lib/uxLabels'
import OAuthButtons from '../components/OAuthButtons'

export default function RegisterPage() {
  const navigate = useNavigate()
  const { register } = useAuth()
  const [companyName, setCompanyName] = useState('')
  const [email, setEmail]             = useState('')
  const [password, setPassword]       = useState('')
  const [showPw, setShowPw]           = useState(false)
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (password.length < 10) {
      setError('Password must be at least 10 characters with upper, lower, and a number')
      return
    }
    setLoading(true)
    try {
      const data = await register(companyName, email, password)
      sessionStorage.setItem('aria_pending_verify_email', email)
      if (data?.tenant?.slug) {
        sessionStorage.setItem('aria_workspace_slug', data.tenant.slug)
        localStorage.setItem('aria_last_workspace_slug', data.tenant.slug)
      }
      navigate('/check-email')
    } catch (err) {
      setError(err.response?.data?.detail || 'Registration failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

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
          <h2 className="text-2xl font-bold text-brand-900 mb-1 tracking-tight">Create your workspace</h2>
          <p className="text-slate-500 text-sm mb-6">Set up ARIA for your company — free to start</p>

          {error && (
            <div role="alert" className="mb-5 p-3.5 bg-red-50 ring-1 ring-red-200 rounded-2xl flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="register-company" className="block text-sm font-semibold text-slate-700 mb-1.5">
                <span className="inline-flex items-center gap-1.5">
                  <Building2 className="w-3.5 h-3.5 text-brand-500" />
                  Company Name
                </span>
              </label>
              <input
                id="register-company"
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                required
                autoComplete="organization"
                placeholder="Acme Corp"
                className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 bg-white text-sm text-slate-800 placeholder-slate-400 transition-shadow"
              />
            </div>
            <div>
              <label htmlFor="register-email" className="block text-sm font-semibold text-slate-700 mb-1.5">Work Email</label>
              <input
                id="register-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                placeholder="you@company.com"
                className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 bg-white text-sm text-slate-800 placeholder-slate-400 transition-shadow"
              />
            </div>
            <div>
              <label htmlFor="register-password" className="block text-sm font-semibold text-slate-700 mb-1.5">Password</label>
              <div className="relative">
                <input
                  id="register-password"
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={10}
                  autoComplete="new-password"
                  placeholder="Min. 10 characters"
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
                  Creating workspace...
                </>
              ) : (
                <>Create Workspace <ArrowRight className="w-4 h-4" /></>
              )}
            </button>
            <p className="text-[11px] leading-relaxed text-slate-500 text-center">
              By creating a workspace, you agree to ARIA&apos;s{' '}
              <a href="/legal/terms" className="font-semibold text-brand-600 hover:text-brand-700">Terms</a>
              {' '}and{' '}
              <a href="/legal/privacy" className="font-semibold text-brand-600 hover:text-brand-700">Privacy Notice</a>.
              Candidate data is processed for your tenant workspace and is not used to train public models.
            </p>
          </form>

          <div className="mt-6">
            <OAuthButtons mode="signup" companyName={companyName} />
          </div>

          <p className="text-center text-sm text-slate-500 mt-6">
            Already have an account?{' '}
            <Link to="/login" className="text-brand-600 font-semibold hover:text-brand-700 transition-colors">
              Sign in
            </Link>
          </p>
        </div>

        <p className="text-center text-xs text-slate-400 mt-6">
          {TRUST.authFooter}
        </p>
        <div className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-xs text-slate-400">
          {TRUST.legalLinks.map((link) => (
            <a key={link.label} href={link.href} className="hover:text-brand-600">
              {link.label}
            </a>
          ))}
        </div>
      </div>
    </div>
  )
}
