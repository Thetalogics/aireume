import axios from 'axios'

export const API_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true,  // Send httpOnly cookies with every request
})

// Attach JWT token to every request (for backward compatibility with API clients)
// Browser clients will use httpOnly cookies automatically
api.interceptors.request.use((config) => {
  // Token storage is now handled via httpOnly cookies only
  // No localStorage token handling for security
  return config
})

// Shared CSRF token reader
export function getCsrfToken() {
  return document.cookie
    .split('; ')
    .find(row => row.startsWith('csrf_token='))
    ?.split('=')[1] || null
}

// Paths that are CSRF-exempt on the backend (no CSRF token needed)
const CSRF_EXEMPT_PATHS = [
  '/auth/login',
  '/auth/register',
  '/auth/refresh',
  '/auth/logout',
  '/auth/forgot-password',
  '/auth/reset-password',
  '/auth/resend-verification',
  '/auth/verify-email',
  '/auth/test/verify-email',
  '/billing/webhook',
  '/sso/callback',
  '/auth/oauth',
]

function isCsrfExempt(url) {
  const path = url?.split('?')[0] || ''
  return CSRF_EXEMPT_PATHS.some(p => path === p || path.startsWith(p + '/'))
}

// Add CSRF token to state-mutating requests for browser clients
api.interceptors.request.use(async (config) => {
  const safeMethod = ['get', 'head', 'options'].includes(config.method?.toLowerCase())
  if (!safeMethod && !isCsrfExempt(config.url)) {
    let csrfToken = getCsrfToken()
    if (!csrfToken) {
      // Try to refresh CSRF cookie by calling /auth/me (sets new CSRF cookie)
      try {
        await axios.get(`${API_URL}/auth/me`, { withCredentials: true })
        csrfToken = getCsrfToken()
      } catch {
        // Ignore refresh failure; backend will reject with 403 if CSRF is required
      }
    }
    if (csrfToken) {
      config.headers['X-CSRF-Token'] = csrfToken
    } else {
      console.warn('[CSRF] Token missing for', config.method?.toUpperCase(), config.url)
    }
  }
  return config
})

// Auto-refresh on 401 - uses httpOnly cookies only
// Only skip refresh for endpoints that cannot benefit from a refresh attempt.
// /auth/me is intentionally NOT excluded — a 401 there means the access token
// expired but the refresh token may still be valid.
const NON_REFRESHABLE_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']
const PUBLIC_PATHS = ['/login', '/register']

let isRefreshing = false
let refreshFailedQueue = []
let lastSuccessfulAuthTime = 0

api.interceptors.response.use(
  (res) => {
    // Track last successful authenticated response
    if (res.status >= 200 && res.status < 300) {
      lastSuccessfulAuthTime = Date.now()
    }
    return res
  },
  async (error) => {
    const original = error.config
    const reqPath = original?.url || ''
    const isNonRefreshable = NON_REFRESHABLE_PATHS.some(p => reqPath.includes(p))

    if (error.response?.status === 401 && !original._retry && !isNonRefreshable) {
      original._retry = true

      // If a refresh is already in progress, queue this request
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          refreshFailedQueue.push({ resolve, reject })
        }).then(() => api(original)).catch((err) => Promise.reject(err))
      }

      try {
        isRefreshing = true
        // Refresh endpoint reads from cookie - browser sends cookie automatically
        await axios.post(`${API_URL}/auth/refresh`, {}, { withCredentials: true })
        // Refresh succeeded - drain queue and retry original request
        refreshFailedQueue.forEach(({ resolve }) => resolve())
        refreshFailedQueue = []
        return api(original)
      } catch (refreshError) {
        // Refresh failed - reject all queued requests
        refreshFailedQueue.forEach(({ reject }) => reject(refreshError))
        refreshFailedQueue = []
        // Only force logout if session is genuinely expired (no recent successful auth)
        const timeSinceLastAuth = Date.now() - lastSuccessfulAuthTime
        if (timeSinceLastAuth > 10000) {
          window.dispatchEvent(new CustomEvent('auth:logout', { detail: { reason: 'refresh_failed' } }))
        }
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false  // ALWAYS reset, even on success or failure
      }
    }
    if (error.response?.status === 403) {
      const detail = error.response?.data?.detail
      const errorCode = typeof detail === 'object' ? detail?.error_code : null
      if (errorCode === 'ROLE_FORBIDDEN') {
        window.dispatchEvent(new CustomEvent('rbac:forbidden', {
          detail: {
            message: typeof detail === 'object' ? detail.message : detail,
          },
        }))
      }
    }
    return Promise.reject(error)
  }
)

// Retry configuration for transient errors
const MAX_RETRIES = 3
const RETRY_DELAYS = [1000, 2000, 4000] // Exponential backoff

// Add retry interceptor (must be after 401 refresh interceptor)
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config

    // Only retry on 5xx errors and network errors (not 4xx)
    const isRetryable = !error.response || (error.response.status >= 500)

    // Don't retry POST requests that might not be idempotent (except specific ones)
    const isIdempotent = config.method === 'get' || config._isRetryable

    if (!isRetryable || !isIdempotent) {
      return Promise.reject(error)
    }

    config._retryCount = config._retryCount || 0
    if (config._retryCount >= MAX_RETRIES) {
      return Promise.reject(error)
    }

    config._retryCount++
    const delay = RETRY_DELAYS[config._retryCount - 1] || 4000

    await new Promise(resolve => setTimeout(resolve, delay))
    return api(config)
  }
)


export default api
