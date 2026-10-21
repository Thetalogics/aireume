import { Link } from 'react-router-dom'
import { Users, Eye, LayoutTemplate, ArrowRight } from 'lucide-react'
import ModalOverlay from '../motion/ModalOverlay'
import { useUserPreferences } from '../../contexts/useUserPreferences'
import usePermissions from '../../hooks/usePermissions'
import { trackOnboardingEvent } from '../../lib/onboardingAnalytics'

const ROLE_COPY = {
  recruiter: {
    title: 'Welcome to your workspace',
    body: 'You can screen resumes, manage candidates, and collaborate with your team.',
    primary: { label: 'Screen resumes', href: '/analyze' },
    secondary: { label: 'View candidates', href: '/candidates' },
    Icon: Users,
  },
  viewer: {
    title: 'Welcome — read-only access',
    body: 'Browse screening reports and candidate status. Ask an admin if you need to run analyses.',
    primary: { label: 'Browse candidates', href: '/candidates' },
    secondary: null,
    Icon: Eye,
  },
  hiring_manager: {
    title: 'Welcome, hiring manager',
    body: 'Review requisitions, pipeline progress, and screening summaries for your roles.',
    primary: { label: 'View requisitions', href: '/requisitions' },
    secondary: { label: 'View candidates', href: '/candidates' },
    Icon: LayoutTemplate,
  },
  ta_lead: {
    title: 'Welcome, TA lead',
    body: 'Assign recruiters to requisitions, review HM opening requests, and oversee the hiring pipeline.',
    primary: { label: 'Opening requests', href: '/requisitions/open-requests' },
    secondary: { label: 'View requisitions', href: '/requisitions' },
    Icon: Users,
  },
}

export default function InvitedUserWelcome() {
  const { role, isAdmin } = usePermissions()
  const { preferences, updatePreferences } = useUserPreferences()
  const copy = ROLE_COPY[role]

  const shouldShow = !isAdmin && copy && !preferences.invited_welcome_dismissed

  const dismiss = async () => {
    trackOnboardingEvent('invited_welcome_dismissed', { role })
    await updatePreferences({ invited_welcome_dismissed: true })
  }

  if (!shouldShow) return null

  const Icon = copy.Icon

  return (
    <ModalOverlay isOpen onClose={dismiss} ariaLabel={copy.title}>
      <div className="bg-white dark:bg-dark-card rounded-2xl shadow-brand-lg border border-brand-100 dark:border-white/10 p-6 max-w-md w-[min(100vw-2rem,28rem)]">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-blue-50 dark:bg-blue-950/40 flex items-center justify-center">
            <Icon className="w-5 h-5 text-blue-600 dark:text-blue-300" />
          </div>
          <h2 className="text-lg font-bold text-brand-900 dark:text-brand-100">{copy.title}</h2>
        </div>
        <p className="text-sm text-slate-600 dark:text-dark-text-secondary mb-6">{copy.body}</p>
        <div className="flex flex-col gap-2">
          <Link
            to={copy.primary.href}
            onClick={dismiss}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-brand-600 text-white rounded-xl text-sm font-semibold hover:bg-brand-700 transition-colors"
          >
            {copy.primary.label}
            <ArrowRight className="w-4 h-4" />
          </Link>
          {copy.secondary && (
            <Link
              to={copy.secondary.href}
              onClick={dismiss}
              className="w-full text-center px-4 py-2 text-sm font-medium text-brand-600 dark:text-brand-300 hover:underline"
            >
              {copy.secondary.label}
            </Link>
          )}
          <button
            type="button"
            onClick={dismiss}
            className="text-xs text-slate-400 dark:text-dark-text-secondary hover:text-slate-600 mt-1"
          >
            Dismiss
          </button>
        </div>
      </div>
    </ModalOverlay>
  )
}
