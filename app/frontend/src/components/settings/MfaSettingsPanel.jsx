import { useState } from 'react'
import { Copy, Shield } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { setupMfa, enableMfa, disableMfa } from '../../lib/api'
import { showError, showSuccess } from '../../lib/toast'
import { useAuth } from '../../contexts/useAuth'
import { Toggle } from '../ui'

export default function MfaSettingsPanel() {
  const { user, loadUser } = useAuth()
  const [secret, setSecret] = useState('')
  const [otpauth, setOtpauth] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)

  const enrolled = Boolean(user?.mfa_enabled)

  const clearSetup = () => {
    setCode('')
    setSecret('')
    setOtpauth('')
  }

  const startSetup = async () => {
    setBusy(true)
    try {
      const data = await setupMfa()
      setSecret(data.secret)
      setOtpauth(data.otpauth_url)
    } catch (err) {
      showError(err.response?.data?.detail || 'Could not start MFA setup')
    } finally {
      setBusy(false)
    }
  }

  const confirmEnable = async () => {
    setBusy(true)
    try {
      await enableMfa(code)
      showSuccess('Authenticator app enabled')
      clearSetup()
      await loadUser?.()
    } catch (err) {
      showError(err.response?.data?.detail || 'Invalid code')
    } finally {
      setBusy(false)
    }
  }

  const copySecret = async () => {
    try {
      await navigator.clipboard.writeText(secret)
      showSuccess('Setup key copied')
    } catch {
      showError('Could not copy the setup key')
    }
  }

  const confirmDisable = async () => {
    setBusy(true)
    try {
      await disableMfa(code)
      showSuccess('MFA disabled')
      setCode('')
      await loadUser?.()
    } catch (err) {
      showError(err.response?.data?.detail || 'Invalid code')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-brand-600" />
          <p className="text-sm font-semibold text-slate-800 dark:text-dark-text-primary">Authenticator app (TOTP)</p>
        </div>
        <Toggle
          checked={enrolled}
          onChange={() => {
            if (enrolled) return
            startSetup()
          }}
          label={enrolled ? 'Enabled' : 'Off'}
          disabled={busy || enrolled}
        />
      </div>
      {user?.mfa_required && !enrolled && (
        <p role="alert" className="text-sm text-amber-700">
          MFA is required for admin and platform roles. Enroll an authenticator app to continue.
        </p>
      )}
      {secret && (
        <div className="p-4 rounded-xl bg-brand-50 ring-1 ring-brand-100 text-sm space-y-4">
          <div>
            <p className="font-semibold text-brand-900">Scan with your authenticator app</p>
            <p className="mt-1 text-xs text-slate-600">
              Google Authenticator, Microsoft Authenticator, and other TOTP apps are supported.
            </p>
          </div>
          {otpauth && (
            <div className="flex justify-center">
              <div
                className="bg-white p-3 rounded-lg ring-1 ring-slate-200"
                aria-label="Authenticator setup QR code"
                data-testid="mfa-qr-code"
              >
                <QRCodeSVG value={otpauth} size={184} level="M" marginSize={0} />
              </div>
            </div>
          )}
          <div>
            <p className="text-xs font-medium text-slate-700">Cannot scan? Enter this setup key manually.</p>
            <div className="mt-1.5 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded-md bg-white px-3 py-2 text-xs text-slate-800 ring-1 ring-slate-200">
                {secret}
              </code>
              <button
                type="button"
                onClick={copySecret}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-600 ring-1 ring-slate-200 hover:bg-white hover:text-brand-700"
                aria-label="Copy MFA setup key"
                title="Copy setup key"
              >
                <Copy className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      )}
      <div>
        <label htmlFor="mfa-code" className="block text-sm font-medium text-slate-700 mb-1.5">
          Authenticator code
        </label>
        <input
          id="mfa-code"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          className="w-full px-4 py-2.5 rounded-xl text-sm ring-1 ring-slate-200 dark:bg-dark-card dark:text-dark-text-primary"
          placeholder="123456"
        />
      </div>
      <div className="flex gap-2">
        {!enrolled ? (
          <button
            type="button"
            disabled={busy || code.length !== 6}
            onClick={confirmEnable}
            className="px-4 py-2.5 bg-brand-600 text-white text-sm font-semibold rounded-xl disabled:opacity-50"
          >
            Verify and enable
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || code.length !== 6 || user?.mfa_required}
            onClick={confirmDisable}
            className="px-4 py-2.5 bg-red-100 text-red-700 text-sm font-semibold rounded-xl disabled:opacity-50"
          >
            Disable MFA
          </button>
        )}
      </div>
    </div>
  )
}
