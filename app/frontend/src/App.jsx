import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate, useLocation, useParams, Link } from 'react-router-dom'
import { Spinner } from './components/ui'
import { MotionConfig } from 'framer-motion'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { BrandingProvider } from './contexts/BrandingContext'
import { NotificationProvider } from './contexts/NotificationContext'
import { OnboardingProvider, useOnboarding } from './contexts/OnboardingContext'
import { UserPreferencesProvider } from './contexts/UserPreferencesContext'
import InvitedUserWelcome from './components/onboarding/InvitedUserWelcome'
import { SubscriptionProvider } from './hooks/useSubscription'
import ProtectedRoute from './components/ProtectedRoute'
import VerifyEmailGate from './components/VerifyEmailGate'
import MFAEnrollGate from './components/MFAEnrollGate'
import RequireWriteAccess from './components/RequireWriteAccess'
import RequirePlanFeature from './components/RequirePlanFeature'
import PlatformAdminRoute from './components/PlatformAdminRoute'
import AppShell from './components/AppShell'
import ErrorBoundary from './components/ErrorBoundary'
import OnboardingWizard from './components/OnboardingWizard'
import usePermissions from './hooks/usePermissions'

// New pages
const Dashboard = lazy(() => import('./pages/Dashboard'))
const AnalyzePage  = lazy(() => import('./pages/AnalyzePage'))
const HandoffPackage = lazy(() => import('./components/HandoffPackage'))
const PublicHandoffPage = lazy(() => import('./pages/PublicHandoffPage'))

// Existing pages
const ReportPage   = lazy(() => import('./pages/ReportPage'))
const LoginPage    = lazy(() => import('./pages/LoginPage'))
const RegisterPage = lazy(() => import('./pages/RegisterPage'))
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'))
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage'))
const VerifyEmailPage = lazy(() => import('./pages/VerifyEmailPage'))
const CheckEmailPage = lazy(() => import('./pages/CheckEmailPage'))
const CandidatesPage = lazy(() => import('./pages/CandidatesPage'))
const CandidateProfilePage = lazy(() => import('./pages/CandidateProfilePage'))
const RequisitionsPage = lazy(() => import('./pages/RequisitionsPage'))
const RequisitionDetailPage = lazy(() => import('./pages/RequisitionDetailPage'))
const OpenRequestsPage = lazy(() => import('./pages/OpenRequestsPage'))
const KanbanBoard    = lazy(() => import('./pages/KanbanBoard'))
const ComparePage  = lazy(() => import('./pages/ComparePage'))
const TeamPage       = lazy(() => import('./pages/TeamPage'))
const TeamSkillsPage = lazy(() => import('./pages/TeamSkillsPage'))
const TranscriptPage = lazy(() => import('./pages/TranscriptPage'))
const VideoPage    = lazy(() => import('./pages/VideoPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const AnalyticsLayout = lazy(() => import('./pages/AnalyticsLayout'))
const AnalyticsOverviewPage = lazy(() => import('./pages/AnalyticsOverviewPage'))
const AnalyticsExplorePage = lazy(() => import('./pages/AnalyticsExplorePage'))
const ReportBuilderPage = lazy(() => import('./pages/ReportBuilderPage'))
const AnalyticsDocsPage = lazy(() => import('./pages/AnalyticsDocsPage'))
const InterviewPage = lazy(() => import('./pages/InterviewPage'))
const InterviewDetailPage = lazy(() => import('./pages/InterviewDetailPage'))
const VoiceScreeningPage = lazy(() => import('./pages/VoiceScreeningPage'))
const RecruiterInterviewPage = lazy(() => import('./pages/RecruiterInterviewPage'))
const RecruiterSessionDetailPage = lazy(() => import('./pages/RecruiterSessionDetailPage'))

// Admin layout + pages
const AdminLayout        = lazy(() => import('./layouts/AdminLayout'))
const AdminOverviewPage  = lazy(() => import('./pages/admin/AdminOverviewPage'))
const TenantsPage        = lazy(() => import('./pages/admin/TenantsPage'))
const TenantDetailPage   = lazy(() => import('./pages/admin/TenantDetailPage'))
const PlansPage          = lazy(() => import('./pages/admin/PlanManagementPage'))
const UsersPage          = lazy(() => import('./pages/admin/UsersPage'))
const FeatureFlagsPage   = lazy(() => import('./pages/admin/FeatureFlagsPage'))
const WebhooksPage       = lazy(() => import('./pages/admin/WebhooksPage'))
const RateLimitsPage     = lazy(() => import('./pages/admin/RateLimitsPage'))
const SSOPage            = lazy(() => import('./pages/admin/SSOPage'))
const EmailSettingsPage  = lazy(() => import('./pages/admin/EmailSettings'))
const MetricsPage        = lazy(() => import('./pages/admin/MetricsPage'))
const AuditLogPage       = lazy(() => import('./pages/admin/AuditLogPage'))
const SecurityEventsPage = lazy(() => import('./pages/admin/SecurityEventsPage'))
const RevenuePage        = lazy(() => import('./pages/admin/RevenuePage'))
const InvoicesPage       = lazy(() => import('./pages/admin/InvoicesPage'))
const DunningPage        = lazy(() => import('./pages/admin/DunningPage'))
const BillingSettingsPage = lazy(() => import('./pages/admin/BillingSettingsPage'))
const ErasurePage        = lazy(() => import('./pages/admin/ErasurePage'))
const ImpersonationPage  = lazy(() => import('./pages/admin/ImpersonationPage'))
const CrmPage            = lazy(() => import('./pages/admin/CrmPage'))

function PageLoader() {
  return (
    <div className="h-screen bg-surface flex items-center justify-center">
      <Spinner className="w-8 h-8" />
    </div>
  )
}

function LegacyJdDetailRedirect() {
  const { id } = useParams()
  return <Navigate to={`/requisitions/${id}`} replace />
}

function LegacyJdPipelineRedirect() {
  const { id } = useParams()
  return <Navigate to={`/requisitions/${id}?tab=pipeline`} replace />
}

function LegacyHandoffRedirect() {
  const { id } = useParams()
  return <Navigate to={`/requisitions/${id}/handoff`} replace />
}

function HomePage() {
  const { user, loading } = useAuth()
  const { isHiringManager, isViewer } = usePermissions()
  if (!loading && user && isHiringManager) return <Navigate to="/requisitions" replace />
  if (!loading && user && isViewer) return <Navigate to="/candidates" replace />
  return (
    <Shell>
      <OnboardingGate>
        <Dashboard />
      </OnboardingGate>
    </Shell>
  )
}

function Shell({ children }) {
  return (
    <ProtectedRoute>
      <VerifyEmailGate>
      <MFAEnrollGate>
      <SubscriptionProvider>
        <AppShell>
          <InvitedUserWelcome />
          <ErrorBoundary>
            {children}
          </ErrorBoundary>
        </AppShell>
      </SubscriptionProvider>
      </MFAEnrollGate>
      </VerifyEmailGate>
    </ProtectedRoute>
  )
}

function OnboardingGate({ children }) {
  const { isOnboardingComplete, statusLoading } = useOnboarding()
  const { user, tenant, loading: authLoading } = useAuth()

  // Wait for auth, onboarding status, and tenant to all be loaded before deciding
  const isLoading = authLoading || statusLoading || !user || !tenant

  if (isLoading) {
    return <PageLoader />
  }

  // If user is authenticated and onboarding is not complete (from backend or tenant data),
  // show the onboarding wizard
  const tenantOnboardingComplete = tenant?.onboarding_completed === true
  if (user && !isOnboardingComplete && !tenantOnboardingComplete) {
    return <OnboardingWizard />
  }

  return children
}

function App() {
  const location = useLocation()

  return (
    <MotionConfig reducedMotion="user">
    <AuthProvider>
        <BrandingProvider>
        <NotificationProvider>
          <UserPreferencesProvider>
          <OnboardingProvider>
            <ErrorBoundary>
              <Suspense fallback={<PageLoader />}>
                <Routes>
              {/* Public routes */}
              <Route path="/login"      element={<LoginPage />} />
              <Route path="/register"   element={<RegisterPage />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />
              <Route path="/reset-password/:token" element={<ResetPasswordPage />} />
              <Route path="/verify-email/:token" element={<VerifyEmailPage />} />
              <Route path="/check-email" element={<CheckEmailPage />} />
              <Route path="/handoff/:token" element={<Suspense fallback={<PageLoader />}><PublicHandoffPage /></Suspense>} />
              
              {/* Onboarding direct-access route */}
              <Route path="/onboarding" element={<ProtectedRoute><VerifyEmailGate><OnboardingWizard /></VerifyEmailGate></ProtectedRoute>} />
              
              {/* New routes */}
              <Route path="/"           element={<HomePage />} />
              <Route path="/analyze"    element={<Shell><OnboardingGate><RequireWriteAccess><AnalyzePage /></RequireWriteAccess></OnboardingGate></Shell>} />
              <Route path="/jd-library" element={<Navigate to="/requisitions" replace />} />
              <Route path="/jd-library/:id" element={<LegacyJdDetailRedirect />} />
              <Route path="/requisitions/:id/handoff" element={<Shell><OnboardingGate><HandoffPackage /></OnboardingGate></Shell>} />
              <Route path="/jd-library/:id/handoff" element={<LegacyHandoffRedirect />} />
              <Route path="/jd-library/:id/candidates" element={<LegacyJdPipelineRedirect />} />
              <Route path="/requisitions" element={<Shell><OnboardingGate><RequirePlanFeature feature="requisitions"><RequisitionsPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/requisitions/open-requests" element={<Shell><OnboardingGate><RequirePlanFeature feature="requisitions"><OpenRequestsPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/requisitions/:id" element={<Shell><OnboardingGate><RequirePlanFeature feature="requisitions"><RequisitionDetailPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              
              {/* Existing routes */}
              <Route path="/report"     element={<Shell><OnboardingGate><ReportPage /></OnboardingGate></Shell>} />
              <Route path="/candidates" element={<Shell><OnboardingGate><CandidatesPage /></OnboardingGate></Shell>} />
              <Route path="/candidates/:id" element={<Shell><OnboardingGate><CandidateProfilePage /></OnboardingGate></Shell>} />
              <Route path="/pipeline"    element={<Shell><OnboardingGate><RequirePlanFeature feature="pipeline"><KanbanBoard /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/projects"    element={<Navigate to="/requisitions" replace />} />
              <Route path="/projects/:id" element={<Navigate to="/requisitions" replace />} />
              <Route path="/compare"    element={<Shell><OnboardingGate><RequirePlanFeature feature="compare"><ComparePage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/team"       element={<Shell><OnboardingGate><TeamPage /></OnboardingGate></Shell>} />
              <Route path="/team-skills" element={<Shell><OnboardingGate><TeamSkillsPage /></OnboardingGate></Shell>} />
              <Route path="/transcript" element={<Shell><OnboardingGate><RequirePlanFeature feature="transcript_analysis"><TranscriptPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/video"      element={<Shell><OnboardingGate><RequirePlanFeature feature="video_analysis"><VideoPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/analytics" element={<Shell><OnboardingGate><RequirePlanFeature feature="analytics"><AnalyticsLayout /></RequirePlanFeature></OnboardingGate></Shell>}>
                <Route index element={<AnalyticsOverviewPage />} />
                <Route path="explore" element={<AnalyticsExplorePage />} />
                <Route path="reports" element={<ReportBuilderPage />} />
                <Route path="docs" element={<AnalyticsDocsPage />} />
              </Route>
              <Route path="/voice-screening" element={<Shell><OnboardingGate><RequirePlanFeature feature="ai_interviews"><VoiceScreeningPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/recruiter-interviews" element={<Shell><OnboardingGate><RequirePlanFeature feature="ai_interviews"><RecruiterInterviewPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/recruiter-interviews/:id" element={<Shell><OnboardingGate><RecruiterSessionDetailPage /></OnboardingGate></Shell>} />
              <Route path="/ai-interviews" element={<Shell><OnboardingGate><RequirePlanFeature feature="ai_interviews"><InterviewPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/ai-interviews/:id" element={<Shell><OnboardingGate><RequirePlanFeature feature="ai_interviews"><InterviewDetailPage /></RequirePlanFeature></OnboardingGate></Shell>} />
              <Route path="/interviews/comparison" element={<Navigate to="/compare" replace />} />
              <Route path="/settings"   element={<Shell><OnboardingGate><SettingsPage /></OnboardingGate></Shell>} />
              {/* Admin portal - standalone layout (no recruiter nav) */}
              <Route path="/admin" element={<PlatformAdminRoute><MFAEnrollGate><AdminLayout /></MFAEnrollGate></PlatformAdminRoute>}>
                <Route index element={<AdminOverviewPage />} />
                <Route path="tenants/:id" element={<TenantDetailPage />} />
                <Route path="tenants" element={<TenantsPage />} />
                <Route path="plans" element={<PlansPage />} />
                <Route path="users" element={<UsersPage />} />
                <Route path="features" element={<FeatureFlagsPage />} />
                <Route path="webhooks" element={<WebhooksPage />} />
                <Route path="rate-limits" element={<RateLimitsPage />} />
                <Route path="sso" element={<SSOPage />} />
                <Route path="email" element={<EmailSettingsPage />} />
                <Route path="metrics" element={<MetricsPage />} />
                <Route path="audit" element={<AuditLogPage />} />
                <Route path="security" element={<SecurityEventsPage />} />
                <Route path="billing" element={<RevenuePage />} />
                <Route path="billing-settings" element={<BillingSettingsPage />} />
                <Route path="invoices" element={<InvoicesPage />} />
                <Route path="dunning" element={<DunningPage />} />
                <Route path="crm" element={<CrmPage />} />
                <Route path="erasure" element={<ErasurePage />} />
                <Route path="impersonation" element={<ImpersonationPage />} />
              </Route>

              {/* Legacy redirects for unified AI Interview */}
              <Route path="/batch"      element={<Navigate to="/analyze" replace />} />
              <Route path="/templates"  element={<Navigate to="/requisitions" replace />} />
              <Route path="/dashboard-old" element={<Navigate to="/" replace />} />
              
              {/* Catch all */}
              <Route path="*" element={
                <div className="flex flex-col items-center justify-center min-h-[60vh] text-slate-500">
                  <h1 className="text-6xl font-bold text-slate-300 mb-4">404</h1>
                  <p className="text-lg font-medium mb-2">Page not found</p>
                  <p className="text-sm mb-4">The page you're looking for doesn't exist.</p>
                  <Link to="/" className="px-4 py-2 bg-brand-500 text-white rounded-lg hover:bg-brand-600">Go Home</Link>
                </div>
              } />
              </Routes>
              </Suspense>
            </ErrorBoundary>
          </OnboardingProvider>
          </UserPreferencesProvider>
        </NotificationProvider>
        </BrandingProvider>
    </AuthProvider>
    </MotionConfig>
  )
}

export default App
