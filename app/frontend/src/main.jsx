import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import * as Sentry from '@sentry/react'
import { ThemeProvider } from './contexts/ThemeContext'
import './index.css'
import App from './App.jsx'

const buildId = typeof __APP_BUILD_ID__ !== 'undefined' ? __APP_BUILD_ID__ : 'dev'
document.documentElement.dataset.buildId = buildId

const sentryDsn = import.meta.env.VITE_SENTRY_DSN
if (sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,
    environment: import.meta.env.MODE,
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || 0.1),
    sendDefaultPii: false,
  })
}

function reportClientError(payload, error) {
  try {
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload),
    })
  } catch { /* reporting must never break the UI */ }
  if (error) {
    Sentry.captureException(error)
  } else {
    Sentry.captureMessage(String(payload.message || 'client-error'))
  }
}

window.onerror = (message, source, lineno, colno, error) => {
  console.error('Global error:', { message, source, lineno, colno, error })
  reportClientError(
    { message: String(message), source: String(source || ''), stack: error?.stack || '' },
    error instanceof Error ? error : undefined,
  )
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason)
  const reason = event.reason
  reportClientError(
    {
      message: String(reason?.message || reason || 'unhandledrejection'),
      source: 'unhandledrejection',
      stack: reason?.stack || '',
    },
    reason instanceof Error ? reason : undefined,
  )
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
)
