import { useState, useEffect, useCallback } from 'react'
import {
  Shield,
  Loader2,
  AlertTriangle,
  Check,
  X,
  ChevronDown,
  Trash2,
  Plug,
  Plus,
} from 'lucide-react'
import {
  getAdminTenants,
  getTenantSSO,
  updateTenantSSO,
  testTenantSSO,
  deleteTenantSSO,
} from '../../lib/api'

function Toast({ message, type = 'success', onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3500)
    return () => clearTimeout(t)
  }, [onDone])

  return (
    <div className={`p-4 rounded-2xl ring-1 text-sm shadow-sm ${
      type === 'success'
        ? 'bg-green-50 text-green-700 ring-green-200'
        : type === 'error'
        ? 'bg-red-50 text-red-700 ring-red-200'
        : 'bg-amber-50 text-amber-700 ring-amber-200'
    }`}>
      <div className="flex items-center gap-2">
        {type === 'success' ? <Check className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
        {message}
      </div>
    </div>
  )
}

function StatusDot({ status }) {
  const colors = {
    configured: 'bg-green-500',
    untested: 'bg-amber-500',
    failed: 'bg-red-500',
    none: 'bg-slate-300',
  }
  const labels = {
    configured: 'Configured & tested',
    untested: 'Configured — untested',
    failed: 'Test failed',
    none: 'Not configured',
  }
  return (
    <div className="flex items-center gap-2">
      <span className={`w-2.5 h-2.5 rounded-full ${colors[status] || colors.none}`} />
      <span className="text-xs text-slate-500">{labels[status] || 'Unknown'}</span>
    </div>
  )
}

const EMPTY_FORM = {
  provider_type: 'saml2',
  idp_entity_id: '',
  idp_sso_url: '',
  idp_certificate: '',
  default_role: 'viewer',
  groups_attribute: 'groups',
  group_mappings: [],
  auto_provision: true,
  enforce_sso: false,
}

export default function SSOPage() {
  const [tenants, setTenants] = useState([])
  const [selectedTenantId, setSelectedTenantId] = useState(null)
  const [ssoConfig, setSsoConfig] = useState(null)
  const [loadingTenants, setLoadingTenants] = useState(true)
  const [loadingSSO, setLoadingSSO] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const [ssoError, setSSOError] = useState('')
  const [formErrors, setFormErrors] = useState([])
  const [toast, setToast] = useState(null)
  const [ssoStatus, setSsoStatus] = useState('none')

  const [form, setForm] = useState(EMPTY_FORM)

  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [enforceWarning, setEnforceWarning] = useState(false)

  const fetchTenants = useCallback(async () => {
    setLoadingTenants(true)
    setError('')
    try {
      const data = await getAdminTenants()
      const items = data.items || data.tenants || data || []
      setTenants(items)
      if (items.length > 0) setSelectedTenantId(current => current ?? items[0].id)
    } catch (err) {
      console.error('Failed to load tenants:', err)
      setError(err.response?.data?.detail || 'Failed to load tenants.')
    } finally {
      setLoadingTenants(false)
    }
  }, [])

  useEffect(() => {
    fetchTenants()
  }, [fetchTenants])

  const fetchSSO = useCallback(async (tenantId) => {
    if (!tenantId) return
    setLoadingSSO(true)
    setSSOError('')
    try {
      const data = await getTenantSSO(tenantId)
      setSsoConfig(data)
      if (data.enabled) {
        setForm({
          provider_type: data.provider_type || 'saml2',
          idp_entity_id: data.idp_entity_id || '',
          idp_sso_url: data.idp_sso_url || '',
          idp_certificate: '',
          default_role: data.default_role || 'viewer',
          groups_attribute: data.groups_attribute || 'groups',
          group_mappings: data.group_mappings || [],
          auto_provision: data.auto_provision ?? true,
          enforce_sso: data.enforce_sso ?? false,
        })
        // Certificate is not returned from API for security, leave blank with hint
        setSsoStatus('untested')
      } else {
        setForm(EMPTY_FORM)
        setSsoStatus('none')
      }
    } catch (err) {
      console.error('Failed to load SSO config:', err)
      setSSOError(err.response?.data?.detail || 'Failed to load SSO configuration.')
      setSsoConfig(null)
      setForm(EMPTY_FORM)
      setSsoStatus('none')
    } finally {
      setLoadingSSO(false)
    }
  }, [])

  useEffect(() => {
    if (selectedTenantId) {
      fetchSSO(selectedTenantId)
    } else {
      setSsoConfig(null)
      setForm(EMPTY_FORM)
      setSsoStatus('none')
    }
  }, [selectedTenantId, fetchSSO])

  const selectedTenant = tenants.find((t) => t.id === selectedTenantId)

  const validateForm = () => {
    const errs = []
    if (!form.idp_entity_id.trim()) errs.push('Entity ID is required')
    if (!form.idp_sso_url.trim()) errs.push('SSO Login URL is required')
    else {
      try {
        new URL(form.idp_sso_url)
      } catch {
        errs.push('SSO Login URL must be a valid URL')
      }
    }
    // Certificate is required only when creating new config (no existing config)
    if (!ssoConfig?.enabled && !form.idp_certificate.trim()) {
      errs.push('X.509 Certificate is required for new configuration')
    }
    // When updating, certificate is optional (keep existing)
    if (form.idp_certificate.trim() && form.idp_certificate.trim().length < 50) {
      errs.push('X.509 Certificate appears too short — please paste the full certificate')
    }
    return errs
  }

  const handleSave = async () => {
    setFormErrors([])
    const errs = validateForm()
    if (errs.length > 0) {
      setFormErrors(errs)
      return
    }

    const payload = {
      idp_entity_id: form.idp_entity_id.trim(),
      idp_sso_url: form.idp_sso_url.trim(),
      idp_certificate: form.idp_certificate.trim(),
      enforce_sso: form.enforce_sso,
      auto_provision: form.auto_provision,
      default_role: form.default_role,
      groups_attribute: form.groups_attribute || 'groups',
      group_mappings: form.group_mappings || [],
      is_active: true,
    }

    // If updating and certificate not changed, don't send empty cert
    if (ssoConfig?.enabled && !form.idp_certificate.trim()) {
      delete payload.idp_certificate
    }

    setSaving(true)
    try {
      await updateTenantSSO(selectedTenantId, payload)
      setToast({ message: 'SSO configuration saved successfully.', type: 'success' })
      fetchSSO(selectedTenantId)
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || 'Failed to save SSO configuration.'
      setFormErrors([detail])
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      const result = await testTenantSSO(selectedTenantId)
      if (result.success || result.status === 'ok') {
        setSsoStatus('configured')
        setToast({ message: 'SSO connection test successful!', type: 'success' })
      } else {
        setSsoStatus('failed')
        setToast({
          message: result.message || result.error || 'SSO connection test failed.',
          type: 'error',
        })
      }
    } catch (err) {
      setSsoStatus('failed')
      setToast({
        message: err.response?.data?.detail || 'SSO connection test failed.',
        type: 'error',
      })
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteTenantSSO(selectedTenantId)
      setToast({ message: 'SSO configuration deleted.', type: 'success' })
      setShowDeleteModal(false)
      setSsoStatus('none')
      fetchSSO(selectedTenantId)
    } catch (err) {
      setToast({
        message: err.response?.data?.detail || 'Failed to delete SSO configuration.',
        type: 'error',
      })
    } finally {
      setDeleting(false)
    }
  }

  // ── Skeletons ──
  const TenantSkeleton = () => (
    <div className="space-y-4">
      <div className="h-8 w-48 bg-slate-200 rounded-lg animate-pulse" />
      <div className="h-64 bg-slate-100 rounded-3xl animate-pulse" />
    </div>
  )

  const SSOSkeleton = () => (
    <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6 space-y-4 animate-pulse">
      <div className="h-6 w-32 bg-slate-200 rounded-lg" />
      <div className="h-10 w-full bg-slate-100 rounded-xl" />
      <div className="h-10 w-full bg-slate-100 rounded-xl" />
      <div className="h-24 w-full bg-slate-100 rounded-xl" />
      <div className="h-10 w-1/3 bg-slate-100 rounded-xl" />
      <div className="h-6 w-1/2 bg-slate-100 rounded-lg" />
    </div>
  )

  // ── Render ──
  if (loadingTenants) return <TenantSkeleton />

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-brand-500/10 flex items-center justify-center ring-1 ring-brand-200">
          <Shield className="w-5 h-5 text-brand-600" />
        </div>
        <div>
          <h2 className="text-2xl font-extrabold text-brand-900 tracking-tight">SSO Configuration</h2>
          <p className="text-sm text-slate-500">Internal only — not a shipped customer feature in this release.</p>
        </div>
      </div>

      <div className="p-4 rounded-2xl bg-amber-50 ring-1 ring-amber-200 text-sm text-amber-900">
        Do not enable SSO for paying tenants. Identity for this release is password login with admin MFA.
        The SAML integration is not certified.
      </div>

      {toast && <Toast message={toast.message} type={toast.type} onDone={() => setToast(null)} />}

      {/* Error state for tenants */}
      {error && (
        <div className="p-4 bg-red-50 rounded-2xl ring-1 ring-red-200 text-sm text-red-700 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
          <button
            onClick={fetchTenants}
            className="ml-auto px-3 py-1 text-xs font-bold bg-red-100 hover:bg-red-200 rounded-lg transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Tenant selector */}
      {tenants.length > 0 && (
        <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-4">
          <label htmlFor="ssopage-select-tenant-1" className="block text-sm font-bold text-slate-700 mb-2">Select Tenant</label>
          <div className="relative max-w-md">
            <select id="ssopage-select-tenant-1"
              value={selectedTenantId || ''}
              onChange={(e) => setSelectedTenantId(Number(e.target.value))}
              className="w-full pl-4 pr-10 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white appearance-none cursor-pointer"
            >
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.slug})
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          </div>
          {selectedTenant && (
            <div className="mt-2 text-xs text-slate-400">
              Plan: {selectedTenant.plan_display_name || selectedTenant.plan_name || '—'} · Status: {selectedTenant.subscription_status}
            </div>
          )}
        </div>
      )}

      {tenants.length === 0 && !error && (
        <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-8 text-center text-slate-400 text-sm">
          No tenants found. Create a tenant first to configure SSO.
        </div>
      )}

      {/* SSO Configuration Panel */}
      {selectedTenantId && (
        <>
          {loadingSSO ? (
            <SSOSkeleton />
          ) : ssoError ? (
            <div className="p-4 bg-red-50 rounded-2xl ring-1 ring-red-200 text-sm text-red-700 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              {ssoError}
              <button
                onClick={() => fetchSSO(selectedTenantId)}
                className="ml-auto px-3 py-1 text-xs font-bold bg-red-100 hover:bg-red-200 rounded-lg transition-colors"
              >
                Retry
              </button>
            </div>
          ) : (
            <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-6">
              {/* Status indicator */}
              <div className="flex items-center justify-between mb-6">
                <h3 className="font-extrabold text-brand-900 text-lg">
                  SSO Settings
                </h3>
                <StatusDot status={ssoStatus} />
              </div>

              {/* No SSO configured message */}
              {ssoStatus === 'none' && !ssoConfig?.enabled && (
                <div className="mb-6 p-4 bg-slate-50 rounded-2xl ring-1 ring-slate-200 text-sm text-slate-600 flex items-center gap-3">
                  <Shield className="w-5 h-5 text-slate-400" />
                  <div>
                    <p className="font-semibold">No SSO configured</p>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Fill in the form below to set up SAML2 single sign-on for this tenant.
                    </p>
                  </div>
                </div>
              )}

              {/* SP metadata (read-only, shown only when configured) */}
              {ssoConfig?.enabled && ssoConfig.sp_entity_id && (
                <div className="mb-6 p-4 bg-blue-50/50 rounded-2xl ring-1 ring-blue-200 text-sm">
                  <p className="font-semibold text-blue-800 mb-2">Service Provider (SP) Metadata</p>
                  <div className="space-y-1 text-xs text-blue-700">
                    <p><span className="font-medium">SP Entity ID:</span> {ssoConfig.sp_entity_id}</p>
                    <p><span className="font-medium">ACS URL:</span> {ssoConfig.sp_acs_url}</p>
                    {ssoConfig.idp_slo_url && (
                      <p><span className="font-medium">SLO URL:</span> {ssoConfig.idp_slo_url}</p>
                    )}
                  </div>
                  <p className="text-xs text-blue-500 mt-2">
                    Share these URLs with your Identity Provider to complete the SAML integration.
                  </p>
                </div>
              )}

              {/* Form errors */}
              {formErrors.length > 0 && (
                <div className="mb-4 p-4 bg-red-50 rounded-2xl ring-1 ring-red-200 text-sm text-red-700 space-y-1">
                  {formErrors.map((err, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                      {err}
                    </div>
                  ))}
                </div>
              )}

              {/* SSO Form */}
              <div className="space-y-4">
                {/* Provider Type */}
                <div>
                  <label htmlFor="ssopage-provider-type-2" className="block text-sm font-bold text-slate-700 mb-1.5">
                    Provider Type <span className="text-red-500">*</span>
                  </label>
                  <select id="ssopage-provider-type-2"
                    value={form.provider_type}
                    onChange={(e) => setForm({ ...form, provider_type: e.target.value })}
                    className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
                  >
                    <option value="saml2">SAML 2.0</option>
                  </select>
                  <p className="text-xs text-slate-400 mt-1">Only SAML 2.0 is supported currently.</p>
                </div>

                {/* Entity ID */}
                <div>
                  <label htmlFor="ssopage-entity-id-3" className="block text-sm font-bold text-slate-700 mb-1.5">
                    Entity ID <span className="text-red-500">*</span>
                  </label>
                  <input id="ssopage-entity-id-3"
                    type="text"
                    value={form.idp_entity_id}
                    onChange={(e) => setForm({ ...form, idp_entity_id: e.target.value })}
                    placeholder="e.g. https://idp.example.com/saml/metadata"
                    className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
                  />
                </div>

                {/* SSO Login URL */}
                <div>
                  <label htmlFor="ssopage-sso-login-url-4" className="block text-sm font-bold text-slate-700 mb-1.5">
                    SSO Login URL <span className="text-red-500">*</span>
                  </label>
                  <input id="ssopage-sso-login-url-4"
                    type="url"
                    value={form.idp_sso_url}
                    onChange={(e) => setForm({ ...form, idp_sso_url: e.target.value })}
                    placeholder="e.g. https://idp.example.com/saml/login"
                    className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
                  />
                  <p className="text-xs text-slate-400 mt-1">Must be a valid HTTPS URL.</p>
                </div>

                {/* X.509 Certificate */}
                <div>
                  <label htmlFor="ssopage-x-509-certificate-5" className="block text-sm font-bold text-slate-700 mb-1.5">
                    X.509 Certificate {!ssoConfig?.enabled && <span className="text-red-500">*</span>}
                  </label>
                  <textarea id="ssopage-x-509-certificate-5"
                    value={form.idp_certificate}
                    onChange={(e) => setForm({ ...form, idp_certificate: e.target.value })}
                    placeholder="Paste the full X.509 certificate (PEM format)"
                    rows={6}
                    className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white resize-none font-mono text-xs leading-relaxed"
                  />
                  {ssoConfig?.enabled && (
                    <p className="text-xs text-slate-400 mt-1">
                      Leave blank to keep the existing certificate. Paste a new one only if you need to update it.
                    </p>
                  )}
                  {!ssoConfig?.enabled && (
                    <p className="text-xs text-slate-400 mt-1">
                      Required for new configurations. Paste the full PEM certificate from your IdP.
                    </p>
                  )}
                </div>

                {/* Default Role */}
                <div>
                  <label htmlFor="ssopage-default-role-6" className="block text-sm font-bold text-slate-700 mb-1.5">
                    Default Role
                  </label>
                  <select id="ssopage-default-role-6"
                    value={form.default_role}
                    onChange={(e) => setForm({ ...form, default_role: e.target.value })}
                    className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
                  >
                    <option value="viewer">Viewer</option>
                    <option value="recruiter">Recruiter</option>
                    <option value="admin">Admin</option>
                  </select>
                  <p className="text-xs text-slate-400 mt-1">Role assigned when no IdP group mapping matches.</p>
                </div>

                <div>
                  <label htmlFor="ssopage-groups-saml-attribute-7" className="block text-sm font-bold text-slate-700 mb-1.5">Groups SAML Attribute</label>
                  <input id="ssopage-groups-saml-attribute-7"
                    value={form.groups_attribute}
                    onChange={(e) => setForm({ ...form, groups_attribute: e.target.value })}
                    placeholder="groups"
                    className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 focus:ring-2 focus:ring-brand-500 text-sm bg-white"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="block text-sm font-bold text-slate-700">IdP Group → Role Mappings</p>
                    <button
                      type="button"
                      onClick={() => setForm({
                        ...form,
                        group_mappings: [...(form.group_mappings || []), { idp_group: '', role: 'viewer' }],
                      })}
                      className="text-xs font-semibold text-brand-600 hover:text-brand-800"
                    >
                      + Add mapping
                    </button>
                  </div>
                  {(form.group_mappings || []).length === 0 ? (
                    <p className="text-xs text-slate-400">No group mappings — all SSO users get the default role.</p>
                  ) : (
                    <div className="space-y-2">
                      {form.group_mappings.map((m, idx) => (
                        <div key={idx} className="flex gap-2">
                          <input
                            value={m.idp_group}
                            onChange={(e) => {
                              const next = [...form.group_mappings]
                              next[idx] = { ...next[idx], idp_group: e.target.value }
                              setForm({ ...form, group_mappings: next })
                            }}
                            placeholder="IdP group name"
                            className="flex-1 px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm"
                          />
                          <select
                            value={m.role}
                            onChange={(e) => {
                              const next = [...form.group_mappings]
                              next[idx] = { ...next[idx], role: e.target.value }
                              setForm({ ...form, group_mappings: next })
                            }}
                            className="px-3 py-2 rounded-xl ring-1 ring-brand-200 text-sm"
                          >
                            <option value="viewer">viewer</option>
                            <option value="recruiter">recruiter</option>
                            <option value="admin">admin</option>
                          </select>
                          <button
                            type="button"
                            onClick={() => setForm({
                              ...form,
                              group_mappings: form.group_mappings.filter((_, i) => i !== idx),
                            })}
                            className="p-2 text-slate-400 hover:text-red-600"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Toggle switches */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {/* Auto-provisioning */}
                  <div className="flex items-center justify-between p-4 rounded-xl ring-1 ring-brand-100 bg-slate-50/50">
                    <div>
                      <p className="text-sm font-semibold text-slate-700">Auto-provisioning</p>
                      <p className="text-xs text-slate-400 mt-0.5">Create accounts for new SSO users automatically.</p>
                    </div>
                    <button
                      role="switch"
                      aria-checked={form.auto_provision}
                      onClick={() => setForm({ ...form, auto_provision: !form.auto_provision })}
                      className={`relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 ${
                        form.auto_provision ? 'bg-brand-600' : 'bg-slate-200'
                      }`}
                    >
                      <span
                        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow ring-1 ring-slate-200 transition duration-200 ease-in-out ${
                          form.auto_provision ? 'translate-x-5' : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>

                  {/* Enforce SSO */}
                  <div className="flex items-center justify-between p-4 rounded-xl ring-1 ring-brand-100 bg-slate-50/50">
                    <div>
                      <p className="text-sm font-semibold text-slate-700">Enforce SSO</p>
                      <p className="text-xs text-slate-400 mt-0.5">Require SSO for all logins.</p>
                    </div>
                    <button
                      role="switch"
                      aria-checked={form.enforce_sso}
                      onClick={() => {
                        if (!form.enforce_sso) {
                          setEnforceWarning(true)
                        } else {
                          setForm({ ...form, enforce_sso: false })
                        }
                      }}
                      className={`relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 ${
                        form.enforce_sso ? 'bg-amber-600' : 'bg-slate-200'
                      }`}
                    >
                      <span
                        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow ring-1 ring-slate-200 transition duration-200 ease-in-out ${
                          form.enforce_sso ? 'translate-x-5' : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>
                </div>

                {/* Enforce SSO warning modal */}
                {enforceWarning && (
                  <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                    <div className="bg-white/95 backdrop-blur-xl rounded-3xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-md p-6">
                      <div className="flex items-center gap-3 mb-4">
                        <div className="w-10 h-10 rounded-xl bg-amber-50 flex items-center justify-center ring-1 ring-amber-200">
                          <AlertTriangle className="w-5 h-5 text-amber-600" />
                        </div>
                        <div>
                          <h3 className="font-extrabold text-brand-900 text-lg">Enforce SSO</h3>
                          <p className="text-sm text-slate-500">Important warning</p>
                        </div>
                      </div>
                      <div className="p-4 bg-amber-50 rounded-2xl ring-1 ring-amber-200 text-sm text-amber-800 mb-4">
                        <p>
                          Enforcing SSO means <strong>users will not be able to log in with a password</strong>.
                          All authentication will go through the Identity Provider.
                        </p>
                        <p className="mt-2">
                          Make sure your SSO connection is tested and working before enabling this option.
                        </p>
                      </div>
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => setEnforceWarning(false)}
                          className="px-4 py-2 ring-1 ring-brand-200 text-sm font-semibold text-brand-700 rounded-xl hover:bg-brand-50 transition-colors"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={() => {
                            setForm({ ...form, enforce_sso: true })
                            setEnforceWarning(false)
                          }}
                          className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-bold rounded-xl transition-colors"
                        >
                          Enable Enforce SSO
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Action buttons */}
              <div className="flex items-center gap-3 mt-6 pt-4 border-t border-brand-100">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-5 py-2.5 btn-brand text-white text-sm font-bold rounded-xl shadow-brand-sm disabled:opacity-60 flex items-center gap-2"
                >
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                  {saving ? 'Saving...' : 'Save Configuration'}
                </button>

                {ssoConfig?.enabled && (
                  <>
                    <button
                      onClick={handleTest}
                      disabled={testing || saving}
                      className="px-4 py-2.5 ring-1 ring-brand-200 text-sm font-semibold text-brand-700 rounded-xl hover:bg-brand-50 transition-colors disabled:opacity-60 flex items-center gap-2"
                    >
                      {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plug className="w-4 h-4" />}
                      {testing ? 'Testing...' : 'Test Connection'}
                    </button>

                    <button
                      onClick={() => setShowDeleteModal(true)}
                      className="px-4 py-2.5 ring-1 ring-red-200 text-sm font-semibold text-red-600 rounded-xl hover:bg-red-50 transition-colors flex items-center gap-2"
                    >
                      <Trash2 className="w-4 h-4" />
                      Delete
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* Delete confirmation modal */}
      {showDeleteModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white/95 backdrop-blur-xl rounded-3xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-md p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-xl bg-red-50 flex items-center justify-center ring-1 ring-red-200">
                <Trash2 className="w-5 h-5 text-red-600" />
              </div>
              <div>
                <h3 className="font-extrabold text-brand-900 text-lg">Delete SSO Configuration</h3>
                <p className="text-sm text-slate-500">
                  For tenant: {selectedTenant?.name || `#${selectedTenantId}`}
                </p>
              </div>
            </div>
            <div className="p-4 bg-amber-50 rounded-2xl ring-1 ring-amber-200 text-sm text-amber-800 mb-4">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold">Warning</p>
                  <p className="mt-0.5">
                    Deleting SSO configuration will disable SSO login for all users in this tenant.
                    Users with enforce SSO enabled will be unable to log in.
                  </p>
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowDeleteModal(false)}
                className="px-4 py-2 ring-1 ring-brand-200 text-sm font-semibold text-brand-700 rounded-xl hover:bg-brand-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-bold rounded-xl disabled:opacity-60 flex items-center gap-2 transition-colors"
              >
                {deleting && <Loader2 className="w-4 h-4 animate-spin" />}
                {deleting ? 'Deleting...' : 'Delete Configuration'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
