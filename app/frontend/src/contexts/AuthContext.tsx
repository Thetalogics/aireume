import { AuthContext } from './useAuth'
import { useState, useEffect, useCallback, useRef } from 'react'
import api from '../lib/api'
import useIdleTimeout from '../hooks/useIdleTimeout'
import SessionTimeoutModal from '../components/SessionTimeoutModal'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [tenant, setTenant] = useState(null)
  const [loading, setLoading] = useState(true)
  const authGenRef = useRef(0)

  // Check auth status on app load by calling /auth/me
  // Browser automatically sends httpOnly cookies with the request
  const loadUser = useCallback(async () => {
    const gen = ++authGenRef.current
    try {
      const res = await api.get('/auth/me')
      if (gen !== authGenRef.current) return   // stale — login/register won
      setUser(res.data.user)
      setTenant(res.data.tenant)
    } catch (err) {
      if (gen !== authGenRef.current) return   // stale — login/register won
      // Retry once on network error (not 401)
      if (!err.response) {
        await new Promise(r => setTimeout(r, 1000))
        if (gen !== authGenRef.current) return
        try {
          const res = await api.get('/auth/me')
          if (gen !== authGenRef.current) return
          setUser(res.data.user)
          setTenant(res.data.tenant)
          return
        } catch (retryErr) {
          if (gen !== authGenRef.current) return
        }
      }
      // Cookie is invalid or expired - user is not authenticated
      setUser(null)
      setTenant(null)
    } finally {
      if (gen === authGenRef.current) {
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    loadUser()
  }, [loadUser])

  // Listen for auth:logout event dispatched from api.js interceptor
  useEffect(() => {
    const handleAuthLogout = () => {
      setUser(null)
      setTenant(null)
      setLoading(false)
    }
    window.addEventListener('auth:logout', handleAuthLogout)
    return () => window.removeEventListener('auth:logout', handleAuthLogout)
  }, [])

  const login = async (email, password, tenant_slug, mfa_code) => {
    authGenRef.current++   // invalidate any in-flight loadUser
    try {
      const res = await api.post('/auth/login', { email, password, tenant_slug, mfa_code })
      // Tokens are set as httpOnly cookies; JSON bodies omit them unless X-ARIA-Token-Grant: api
      setUser(res.data.user)
      setTenant(res.data.tenant)
      return res.data
    } finally {
      setLoading(false)
    }
  }

  const register = async (companyName, email, password) => {
    authGenRef.current++   // invalidate any in-flight loadUser
    try {
      const res = await api.post('/auth/register', { company_name: companyName, email, password })
      // Registration does not issue session cookies until email is verified
      return res.data
    } finally {
      setLoading(false)
    }
  }

  const logout = async () => {
    try {
      // Call logout endpoint to clear httpOnly cookies
      await api.post('/auth/logout')
    } catch {
      // Ignore errors - proceed to clear local state
    }
    try {
      sessionStorage.clear()
    } catch {
      // Ignore storage errors (private mode / mocked stores)
    }
    setUser(null)
    setTenant(null)
  }

  // ── Idle session timeout ──────────────────────────────────────────
  const handleIdleTimeout = useCallback(() => {
    logout()
    // Redirect to login — ProtectedRoute will also catch the null user,
    // but this ensures the redirect even on the current cycle
    window.location.href = '/login'
  }, [])

  const { isWarning, countdown, resetTimer } = useIdleTimeout(!!user, handleIdleTimeout)

  const handleStayLoggedIn = useCallback(() => {
    resetTimer()
    // Attempt a silent token refresh to extend the backend session
    api.post('/auth/refresh', {}, { withCredentials: true }).catch(() => {})
  }, [resetTimer])

  const handleLogoutNow = useCallback(() => {
    logout()
    window.location.href = '/login'
  }, [])

  return (
    <AuthContext.Provider value={{ user, tenant, loading, login, register, logout, loadUser }}>
      {children}
      {isWarning && (
        <SessionTimeoutModal
          countdown={countdown}
          onStayLoggedIn={handleStayLoggedIn}
          onLogoutNow={handleLogoutNow}
        />
      )}
    </AuthContext.Provider>
  )
}
