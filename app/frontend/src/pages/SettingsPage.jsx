import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Settings,
  Building2,
  CreditCard,
  Bell,
  Shield,
  Key,
  ChevronRight,
  Check,
  Loader2,
  AlertTriangle,
  Users,
  FileText,
  Receipt,
  Plug,
  Mic,
  Palette,
  ListChecks,
} from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useOnboarding } from '../contexts/OnboardingContext'
import { useUserPreferences } from '../contexts/UserPreferencesContext'
import { trackOnboardingEvent } from '../lib/onboardingAnalytics'
import { useSubscription } from '../hooks/useSubscription'
import { adminResetUsage, adminChangePlan, getUserFriendlyError, getInvoices, getInvoice, getTenantBranding, updateTenantBranding, changePassword } from '../lib/api'
import { showSuccess, showError } from '../lib/toast'
import { sanitizePlanFeatures, TRUST, INTERVIEW } from '../lib/uxLabels'
import { isSalesLedPlan, SALES_CONTACT_EMAIL } from '../lib/planDisplay'
import ATSIntegrationsPanel from '../components/settings/ATSIntegrationsPanel'
import InterviewSettingsPanel from '../components/settings/InterviewSettingsPanel'
import RequisitionSettingsPanel from '../components/settings/RequisitionSettingsPanel'
import MfaSettingsPanel from '../components/settings/MfaSettingsPanel'
import useConfirm from '../hooks/useConfirm'

import WorkspaceSetupPanel from '../components/settings/WorkspaceSetupPanel'
import { Section } from '../components/settings/SettingsPrimitives'
import SubscriptionSettingsPanel from '../components/settings/SubscriptionSettingsPanel'
import BillingHistoryPanel from '../components/settings/BillingHistoryPanel'

export default function SettingsPage() {
  const navigate = useNavigate()
  const { user, tenant, logout } = useAuth()
  const { confirm, dialog } = useConfirm()
  const {
    subscription,
    availablePlans,
    loading,
    error,
    fetchSubscription,
    getUsageStats,
    getCurrentPlan,
    isFeatureAvailable,
    getRemainingAnalyses,
  } = useSubscription()
  const { completeChecklistItem } = useOnboarding()
  const { preferences, updatePreferences } = useUserPreferences()
  const [searchParams, setSearchParams] = useSearchParams()
  const isAdmin = user?.role === 'admin'
  const requestedTab = searchParams.get('tab') || (isAdmin ? 'subscription' : 'team')
  const moneyTabs = new Set(['subscription', 'billing'])
  const initialTab = (!isAdmin && moneyTabs.has(requestedTab)) ? 'team' : requestedTab
  const [activeTab, setActiveTab] = useState(initialTab)
  const [saving, setSaving] = useState(false)
  const [actionLoading, setActionLoading] = useState(null)

  // White-label branding
  const [brandingForm, setBrandingForm] = useState({
    brand_name: '',
    brand_logo_url: '',
    brand_primary_color: '#7C3AED',
    brand_favicon_url: '',
    custom_domain: '',
  })
  const [brandingLoading, setBrandingLoading] = useState(false)
  const [brandingSaving, setBrandingSaving] = useState(false)
  const [brandingMessage, setBrandingMessage] = useState('')

  // Billing history state
  const [invoices, setInvoices] = useState([])
  const [invoicesTotal, setInvoicesTotal] = useState(0)
  const [invoicesLoading, setInvoicesLoading] = useState(false)
  const [invoicesError, setInvoicesError] = useState(null)
  const [invoicesPage, setInvoicesPage] = useState(0)
  const invoicesPerPage = 10
  const [selectedInvoice, setSelectedInvoice] = useState(null)
  const [invoiceDetail, setInvoiceDetail] = useState(null)
  const [invoiceDetailLoading, setInvoiceDetailLoading] = useState(false)
  const [expandedInvoice, setExpandedInvoice] = useState(null)

  const fetchInvoices = useCallback(async (page = 0) => {
    setInvoicesLoading(true)
    setInvoicesError(null)
    try {
      const offset = page * invoicesPerPage
      const data = await getInvoices(invoicesPerPage, offset)
      setInvoices(data.invoices || [])
      setInvoicesTotal(data.total || 0)
      setInvoicesPage(page)
    } catch (err) {
      setInvoicesError(getUserFriendlyError(err))
    } finally {
      setInvoicesLoading(false)
    }
  }, [])

  const fetchInvoiceDetail = useCallback(async (invoiceId) => {
    setInvoiceDetailLoading(true)
    try {
      const data = await getInvoice(invoiceId)
      setInvoiceDetail(data)
    } catch (err) {
      console.error('Failed to fetch invoice detail:', err)
    } finally {
      setInvoiceDetailLoading(false)
    }
  }, [])

  // Fetch invoices when billing tab is active
  useEffect(() => {
    if (activeTab === 'billing' && isAdmin) {
      fetchInvoices(0)
    }
  }, [activeTab, fetchInvoices])

  useEffect(() => {
    if (activeTab === 'subscription' && isAdmin) {
      completeChecklistItem('reviewedSubscription')
    }
  }, [activeTab, isAdmin, completeChecklistItem])

  useEffect(() => {
    if (activeTab !== 'branding' || !isAdmin) return
    setBrandingLoading(true)
    getTenantBranding()
      .then((data) => {
        const b = data.branding || {}
        setBrandingForm({
          brand_name: b.brand_name || tenant?.name || '',
          brand_logo_url: b.brand_logo_url || '',
          brand_primary_color: b.brand_primary_color || '#7C3AED',
          brand_favicon_url: b.brand_favicon_url || '',
          custom_domain: b.custom_domain || '',
        })
      })
      .catch(() => setBrandingMessage('Could not load branding settings'))
      .finally(() => setBrandingLoading(false))
  }, [activeTab, isAdmin, tenant?.name])

  const handleSaveBranding = async () => {
    setBrandingSaving(true)
    setBrandingMessage('')
    try {
      await updateTenantBranding(brandingForm)
      setBrandingMessage('Branding saved. Changes appear after refresh.')
    } catch (err) {
      setBrandingMessage(getUserFriendlyError(err))
    } finally {
      setBrandingSaving(false)
    }
  }

  // Profile / notification form state (synced from persisted preferences)
  const [profile, setProfile] = useState({
    name: user?.email?.split('@')[0] || '',
    email: user?.email || '',
    notifications: {
      emailOnComplete: true,
      emailOnBatchComplete: true,
      marketing: false,
    },
  })
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [passwordSaving, setPasswordSaving] = useState(false)

  useEffect(() => {
    if (preferences?.notifications) {
      setProfile((prev) => ({
        ...prev,
        notifications: { ...prev.notifications, ...preferences.notifications },
      }))
    }
  }, [preferences?.notifications])

  const handleSaveProfile = async () => {
    setSaving(true)
    try {
      await updatePreferences({ notifications: profile.notifications })
      trackOnboardingEvent('preferences_updated')
    } catch (err) {
      showError(getUserFriendlyError(err) || 'Failed to save notification preferences')
    } finally {
      setSaving(false)
    }
  }

  const handleResetUsage = async () => {
    const ok = await confirm({
      title: 'Reset usage',
      message: 'Reset usage counters? This is for testing only.',
      confirmLabel: 'Reset',
      danger: true,
    })
    if (!ok) return
    setActionLoading('resetUsage')
    try {
      await adminResetUsage()
      await fetchSubscription(true)
      showSuccess('Usage counters reset successfully')
    } catch (err) {
      showError('Failed to reset: ' + getUserFriendlyError(err))
    } finally {
      setActionLoading(null)
    }
  }

  const handleChangePlan = async (plan) => {
    if (isSalesLedPlan(plan)) {
      window.location.href = `mailto:${SALES_CONTACT_EMAIL}?subject=ARIA Enterprise Plan Inquiry&body=Hi, I'd like to learn more about the Enterprise plan for our team.`
      return
    }
    const ok = await confirm({
      title: 'Change plan',
      message: `Switch to ${plan.display_name || plan.name} plan?`,
      confirmLabel: 'Switch plan',
    })
    if (!ok) return
    setActionLoading(`changePlan-${plan.id}`)
    try {
      await adminChangePlan(plan.id)
      await fetchSubscription(true)
      showSuccess(`Switched to ${plan.display_name || plan.name}`)
    } catch (err) {
      showError('Failed to change plan: ' + getUserFriendlyError(err))
    } finally {
      setActionLoading(null)
    }
  }

  const tabs = [
    ...(isAdmin ? [{ id: 'setup', label: 'Setup', icon: ListChecks }] : []),
    ...(isAdmin ? [{ id: 'subscription', label: 'Subscription', icon: CreditCard }] : []),
    ...(isAdmin ? [{ id: 'billing', label: 'Billing History', icon: Receipt }] : []),
    { id: 'team', label: 'Team & Access', icon: Users },
    ...(isAdmin && isFeatureAvailable('white_label') ? [{ id: 'branding', label: 'White-label', icon: Palette }] : []),
    ...(isFeatureAvailable('ai_interviews') ? [{ id: 'interviews', label: 'Interviews', icon: Mic }] : []),
    ...(isFeatureAvailable('requisitions') ? [{ id: 'requisitions', label: 'Requisitions', icon: FileText }] : []),
    ...(isFeatureAvailable('api_access') || isFeatureAvailable('custom_integrations') ? [{ id: 'integrations', label: 'Integrations', icon: Plug }] : []),
    { id: 'notifications', label: 'Notifications', icon: Bell },
    { id: 'security', label: 'Security', icon: Shield },
  ]

  const mfaLocked = Boolean(user?.mfa_required && !user?.mfa_enabled)

  const selectTab = (id) => {
    if (mfaLocked && id !== 'security') return
    setActiveTab(id)
    setSearchParams({ tab: id }, { replace: true })
  }
  const usageStats = getUsageStats()
  const currentPlan = getCurrentPlan()
  const remainingAnalyses = getRemainingAnalyses()

  // Get plan features list
  const planFeatures = sanitizePlanFeatures(currentPlan?.plan?.features || [])

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8 card-animate">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-2xl bg-brand-50 ring-1 ring-brand-100 flex items-center justify-center">
            <Settings className="w-5 h-5 text-brand-600" />
          </div>
          <div>
            <h1 className="text-3xl font-extrabold text-brand-900 tracking-tight">Settings</h1>
            <p className="text-slate-500 text-sm font-medium">
              Manage your account, subscription, and preferences
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-8">
        {/* Sidebar */}
        <div className="lg:w-64 shrink-0">
          <div className="bg-white/90 backdrop-blur-md rounded-3xl ring-1 ring-brand-100 shadow-brand p-2 sticky top-24">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                disabled={mfaLocked && id !== 'security'}
                onClick={() => selectTab(id)}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-2xl text-sm font-medium transition-all ${
                  activeTab === id
                    ? 'bg-brand-50 text-brand-700 ring-1 ring-brand-200'
                    : 'text-slate-600 hover:bg-brand-50/50 hover:text-brand-700'
                } ${mfaLocked && id !== 'security' ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                <Icon className={`w-4 h-4 ${activeTab === id ? 'text-brand-600' : 'text-slate-400'}`} />
                {label}
                {activeTab === id && <ChevronRight className="w-4 h-4 ml-auto" />}
              </button>
            ))}
          </div>
        </div>

        {/* Main Content */}
        <div className="flex-1 space-y-6">
          {activeTab === 'setup' && isAdmin && (
            <WorkspaceSetupPanel />
          )}

          {/* Subscription Tab — admin only */}
          {activeTab === 'subscription' && isAdmin && (
            <SubscriptionSettingsPanel
              loading={loading}
              error={error}
              onRetry={() => fetchSubscription(true)}
              currentPlan={currentPlan}
              usageStats={usageStats}
              remainingAnalyses={remainingAnalyses}
              planFeatures={planFeatures}
              availablePlans={availablePlans}
              user={user}
              actionLoading={actionLoading}
              onResetUsage={handleResetUsage}
              onChangePlan={handleChangePlan}
            />
          )}

          {/* Billing History Tab */}
          {activeTab === 'billing' && isAdmin && (
            <BillingHistoryPanel
              currentPlan={currentPlan}
              invoicesLoading={invoicesLoading}
              invoicesError={invoicesError}
              invoices={invoices}
              invoicesTotal={invoicesTotal}
              invoicesPage={invoicesPage}
              invoicesPerPage={invoicesPerPage}
              fetchInvoices={fetchInvoices}
              expandedInvoice={expandedInvoice}
              setExpandedInvoice={setExpandedInvoice}
              invoiceDetail={invoiceDetail}
              fetchInvoiceDetail={fetchInvoiceDetail}
              selectedInvoice={selectedInvoice}
              setSelectedInvoice={setSelectedInvoice}
              setInvoiceDetail={setInvoiceDetail}
              invoiceDetailLoading={invoiceDetailLoading}
            />
          )}

          {/* Team & Access Tab */}
          {activeTab === 'team' && (
            <>
              <Section
                title="Organization"
                icon={Building2}
                description="Manage your team and tenant settings"
              >
                <div className="space-y-4">
                  <div className="p-4 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                    <p className="block text-xs font-bold text-slate-700 uppercase tracking-wide mb-1">Organization Name</p>
                    <p className="font-semibold text-brand-900">{tenant?.name || 'Your Organization'}</p>
                  </div>
                  <div className="p-4 bg-brand-50/50 rounded-2xl ring-1 ring-brand-100">
                    <p className="block text-xs font-bold text-slate-700 uppercase tracking-wide mb-1">Tenant ID</p>
                    <p className="font-mono text-sm text-slate-600">{tenant?.slug || 'your-org'}</p>
                  </div>
                  <div className="flex gap-2 mt-4">
                    <button
                      onClick={() => navigate('/team')}
                      className="flex items-center gap-2 px-4 py-2.5 btn-brand text-white text-sm font-bold rounded-xl shadow-brand-sm"
                    >
                      <Users className="w-4 h-4" />
                      Manage Team Members
                    </button>
                  </div>
                </div>
              </Section>

              <Section
                title="API Access"
                icon={Key}
                description="API keys for integrations"
              >
                <div className="p-4 bg-slate-50 rounded-2xl ring-1 ring-slate-200">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-medium text-slate-700">API Key</span>
                    <span className={`px-2 py-0.5 text-xs font-bold rounded-full ring-1 ${
                      isFeatureAvailable('api_access')
                        ? 'bg-brand-100 text-brand-700 ring-brand-200'
                        : 'bg-amber-100 text-amber-700 ring-amber-200'
                    }`}>
                      {isFeatureAvailable('api_access') ? 'Available' : 'Agency plan required'}
                    </span>
                  </div>
                  {isFeatureAvailable('api_access') ? (
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value="••••••••••••••••••••••••••"
                        readOnly
                        className="flex-1 px-4 py-2 bg-white rounded-xl text-sm font-mono text-slate-500 ring-1 ring-slate-200"
                      />
                      <button className="px-4 py-2 bg-brand-50 text-brand-700 text-sm font-semibold rounded-xl hover:bg-brand-100 transition-colors">
                        Reveal
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-sm text-slate-500">
                      <AlertTriangle className="w-4 h-4 text-amber-500" />
                      Upgrade to Agency to access API keys
                    </div>
                  )}
                </div>
              </Section>
            </>
          )}

          {activeTab === 'interviews' && (
            <Section
              title={INTERVIEW.settingsLink}
              icon={Mic}
              description={INTERVIEW.hubSubtitle}
            >
              <InterviewSettingsPanel />
            </Section>
          )}

          {activeTab === 'requisitions' && (
            <Section
              title="Requisition workflow"
              icon={FileText}
              description="Intake gates and hiring manager permissions"
            >
              <RequisitionSettingsPanel />
            </Section>
          )}

          {activeTab === 'integrations' && (
            <Section
              title="API connections"
              icon={Plug}
              description="Connect your ATS via webhook or HTTP API. ARIA is not an ATS."
            >
              <ATSIntegrationsPanel />
            </Section>
          )}

          {/* Notifications Tab */}
          {activeTab === 'notifications' && (
            <Section
              title="Notification Preferences"
              icon={Bell}
              description="Control when and how you receive updates"
            >
              <div className="space-y-4">
                {[{
                  id: 'emailOnComplete',
                  label: 'Email when analysis completes',
                  description: 'Receive an email when a single resume analysis is finished'
                }, {
                  id: 'emailOnBatchComplete',
                  label: 'Email when batch completes',
                  description: 'Receive an email summary when a batch screening is finished'
                }, {
                  id: 'marketing',
                  label: 'Product updates & tips',
                  description: 'Occasional emails about new features and best practices'
                }].map(({ id, label, description }) => (
                  <label
                    key={id}
                    htmlFor={`notif-${id}`}
                    className="flex items-start gap-4 p-4 bg-brand-50/30 rounded-2xl ring-1 ring-brand-100 cursor-pointer hover:bg-brand-50/50 transition-colors"
                  >
                    <input
                      id={`notif-${id}`}
                      type="checkbox"
                      checked={profile.notifications[id]}
                      onChange={(e) => setProfile(prev => ({
                        ...prev,
                        notifications: { ...prev.notifications, [id]: e.target.checked }
                      }))}
                      className="mt-0.5 w-5 h-5 rounded-lg border-brand-300 text-brand-600 focus:ring-brand-500"
                    />
                    <span className="flex-1 font-semibold text-slate-800 text-sm">
                      {label}
                      <span className="block text-xs font-normal text-slate-500 mt-0.5">{description}</span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="flex justify-end mt-6">
                <button
                  onClick={handleSaveProfile}
                  disabled={saving}
                  className="flex items-center gap-2 px-5 py-2.5 btn-brand text-white text-sm font-bold rounded-xl shadow-brand-sm disabled:opacity-60"
                >
                  {saving ? <><Loader2 className="w-4 h-4 animate-spin" /> Saving...</> : <><Check className="w-4 h-4" /> Save Preferences</>}
                </button>
              </div>
            </Section>
          )}

          {/* Security Tab */}
          {activeTab === 'branding' && isAdmin && (
            <Section
              title="White-label & Custom Domain"
              icon={Palette}
              description="Customize how ARIA appears to your team. Point a custom domain at this workspace for a fully branded experience."
            >
              {brandingLoading ? (
                <div className="flex justify-center py-12">
                  <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
                </div>
              ) : (
                <div className="space-y-4">
                  {brandingMessage && (
                    <p className="text-sm text-slate-600 bg-brand-50 rounded-xl px-4 py-3 ring-1 ring-brand-100">{brandingMessage}</p>
                  )}
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <label htmlFor="settingspage-brand-name-1" className="block text-sm font-semibold text-slate-700 mb-1">Brand name</label>
                      <input id="settingspage-brand-name-1"
                        type="text"
                        value={brandingForm.brand_name}
                        onChange={(e) => setBrandingForm((f) => ({ ...f, brand_name: e.target.value }))}
                        className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 text-sm"
                        placeholder={tenant?.name || 'Your company'}
                      />
                    </div>
                    <div>
                      <label htmlFor="settingspage-primary-color-2" className="block text-sm font-semibold text-slate-700 mb-1">Primary color</label>
                      <input id="settingspage-primary-color-2"
                        type="color"
                        value={brandingForm.brand_primary_color}
                        onChange={(e) => setBrandingForm((f) => ({ ...f, brand_primary_color: e.target.value }))}
                        className="w-full h-11 rounded-xl ring-1 ring-brand-200 cursor-pointer"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label htmlFor="settingspage-logo-url-3" className="block text-sm font-semibold text-slate-700 mb-1">Logo URL</label>
                      <input id="settingspage-logo-url-3"
                        type="url"
                        value={brandingForm.brand_logo_url}
                        onChange={(e) => setBrandingForm((f) => ({ ...f, brand_logo_url: e.target.value }))}
                        className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 text-sm"
                        placeholder="https://cdn.example.com/logo.png"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label htmlFor="settingspage-favicon-url-4" className="block text-sm font-semibold text-slate-700 mb-1">Favicon URL</label>
                      <input id="settingspage-favicon-url-4"
                        type="url"
                        value={brandingForm.brand_favicon_url}
                        onChange={(e) => setBrandingForm((f) => ({ ...f, brand_favicon_url: e.target.value }))}
                        className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 text-sm"
                        placeholder="https://cdn.example.com/favicon.ico"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label htmlFor="settingspage-custom-domain-5" className="block text-sm font-semibold text-slate-700 mb-1">Custom domain</label>
                      <input id="settingspage-custom-domain-5"
                        type="text"
                        value={brandingForm.custom_domain}
                        onChange={(e) => setBrandingForm((f) => ({ ...f, custom_domain: e.target.value }))}
                        className="w-full px-4 py-2.5 rounded-xl ring-1 ring-brand-200 text-sm font-mono"
                        placeholder="hiring.yourcompany.com"
                      />
                      <p className="text-xs text-slate-400 mt-1">
                        CNAME this hostname to your ARIA deployment, then enter it here. Branding resolves automatically via the Host header.
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={handleSaveBranding}
                    disabled={brandingSaving}
                    className="px-5 py-2.5 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700 disabled:opacity-50"
                  >
                    {brandingSaving ? 'Saving…' : 'Save branding'}
                  </button>
                </div>
              )}
            </Section>
          )}

          {activeTab === 'security' && (
            <>
              <Section
                title="Account Security"
                icon={Shield}
                description="Manage your password and security settings"
              >
                <div className="space-y-4">
                  <div>
                    <label htmlFor="settingspage-email-address-6" className="block text-sm font-medium text-slate-700 mb-1.5">Email Address</label>
                    <input id="settingspage-email-address-6"
                      type="email"
                      value={profile.email}
                      disabled
                      autoComplete="email"
                      className="w-full px-4 py-2.5 bg-slate-50 rounded-xl text-sm text-slate-500 ring-1 ring-slate-200 cursor-not-allowed"
                    />
                    <p className="text-xs text-slate-400 mt-1">Contact support to change your email</p>
                  </div>

                  <div>
                    <label htmlFor="current-password" className="block text-sm font-medium text-slate-700 mb-1.5">Current Password</label>
                    <input
                      id="current-password"
                      type="password"
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      placeholder="••••••••"
                      autoComplete="current-password"
                      className="w-full px-4 py-2.5 rounded-xl text-sm ring-1 ring-slate-200"
                    />
                  </div>
                  <div>
                    <label htmlFor="new-password" className="block text-sm font-medium text-slate-700 mb-1.5">New Password</label>
                    <input
                      id="new-password"
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="Min. 10 characters"
                      autoComplete="new-password"
                      className="w-full px-4 py-2.5 rounded-xl text-sm ring-1 ring-slate-200"
                    />
                  </div>

                  <div className="flex gap-2 pt-2">
                    <button
                      type="button"
                      disabled={passwordSaving || !currentPassword || !newPassword}
                      onClick={async () => {
                        setPasswordSaving(true)
                        try {
                          await changePassword(currentPassword, newPassword)
                          showSuccess('Password updated')
                          setCurrentPassword('')
                          setNewPassword('')
                        } catch (err) {
                          showError(err.response?.data?.detail || 'Failed to change password')
                        } finally {
                          setPasswordSaving(false)
                        }
                      }}
                      className="px-4 py-2.5 bg-brand-50 text-brand-700 text-sm font-semibold rounded-xl hover:bg-brand-100 transition-colors disabled:opacity-50"
                    >
                      {passwordSaving ? 'Saving…' : 'Change Password'}
                    </button>
                  </div>
                </div>
              </Section>

              <Section
                title="Multi-factor authentication"
                icon={Key}
                description="Required for workspace admins and platform roles"
              >
                <MfaSettingsPanel />
              </Section>

              <Section
                title={TRUST.aiProcessingTitle}
                icon={Shield}
                description="How ARIA processes candidate and job data"
              >
                <p className="text-sm text-slate-600 leading-relaxed mb-4">{TRUST.aiProcessingBody}</p>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">AI subprocessors</p>
                <ul className="space-y-1.5">
                  {TRUST.aiSubprocessors.map((name) => (
                    <li key={name} className="text-sm text-slate-700 flex items-center gap-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-brand-500 shrink-0" />
                      {name}
                    </li>
                  ))}
                </ul>
                <div className="mt-5 flex flex-wrap gap-2">
                  {TRUST.legalLinks.map((link) => (
                    <a
                      key={link.label}
                      href={link.href}
                      className="px-3 py-1.5 rounded-lg bg-slate-50 text-xs font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100"
                    >
                      {link.label}
                    </a>
                  ))}
                </div>
              </Section>

              <Section
                title="Danger Zone"
                icon={AlertTriangle}
                description="Actions that can't be undone"
              >
                <div className="p-4 bg-red-50/50 rounded-2xl ring-1 ring-red-200">
                  <div className="flex items-center justify-between">
                    <div>
                      <h4 className="font-bold text-red-800 text-sm">Delete Account</h4>
                      <p className="text-xs text-red-600 mt-0.5">This will permanently delete your account and all data</p>
                    </div>
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: 'Delete account',
                          message: 'Are you sure? This cannot be undone.',
                          confirmLabel: 'Delete',
                          danger: true,
                        })
                        if (ok) logout()
                      }}
                      className="px-4 py-2 bg-red-100 text-red-700 text-sm font-semibold rounded-xl hover:bg-red-200 transition-colors"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </Section>
            </>
          )}
        </div>
      </div>
      {dialog}
    </div>
  )
}
