import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const apiMocks = vi.hoisted(() => ({
  getCandidates: vi.fn().mockResolvedValue({ candidates: [], total: 0 }),
  getCandidate: vi.fn(),
  viewCandidateResume: vi.fn(),
  downloadCandidateResume: vi.fn(),
  updateResultStatus: vi.fn(),
  getVoiceSettings: vi.fn().mockResolvedValue({ bot_name: 'Aria' }),
  updateVoiceSettings: vi.fn(),
  getVoiceSessions: vi.fn().mockResolvedValue([
    { id: 1, status: 'scheduled', candidate_name: 'Ada Lovelace', phone_number: '555-0100' },
  ]),
  getVoiceSession: vi.fn(),
  rescheduleVoiceCall: vi.fn(),
  cancelVoiceSession: vi.fn(),
  getVoiceAnalytics: vi.fn().mockResolvedValue(null),
  bulkCancelVoiceSessions: vi.fn(),
  exportVoiceSessions: vi.fn(),
  getNextAvailableSlot: vi.fn(),
  getCandidateNotes: vi.fn().mockResolvedValue([]),
  addCandidateNote: vi.fn(),
  suggestInterviewOpening: vi.fn(),
  updateOrganization: vi.fn(),
  selectOnboardingPlan: vi.fn(),
  getAvailablePlans: vi.fn().mockResolvedValue([]),
  seedSampleData: vi.fn(),
  inviteTeamDuringOnboarding: vi.fn(),
  createBillingCheckout: vi.fn(),
}))

vi.mock('../lib/api', () => apiMocks)

vi.mock('../hooks/useKeyboardShortcuts', () => ({
  useKeyboardShortcuts: () => {},
}))

vi.mock('../hooks/useOptimisticUpdate', () => ({
  useOptimisticUpdate: () => ({ optimisticUpdate: vi.fn() }),
}))

vi.mock('../hooks/usePrefetch', () => ({
  usePrefetch: () => ({ prefetchCandidate: vi.fn(), cancelPrefetch: vi.fn() }),
}))

vi.mock('../contexts/useOnboarding', () => ({
  useOnboarding: () => ({
    isOnboardingComplete: false,
    markOnboardingComplete: vi.fn(),
    skipOnboarding: vi.fn(),
    completeChecklistItem: vi.fn(),
  }),
}))

vi.mock('../hooks/useFeatureGuide', () => ({
  default: () => ({ open: false, guide: null, dismiss: vi.fn() }),
}))

vi.mock('../hooks/usePermissions', () => ({
  default: () => ({ canWrite: true, isAdmin: true }),
}))

vi.mock('../hooks/useConfirm', () => ({
  default: () => ({ confirm: vi.fn(), dialog: null }),
}))

vi.mock('../contexts/useAuth', () => ({
  useAuth: () => ({ tenant: { name: 'Acme' } }),
}))

vi.mock('../hooks/useSubscription', () => ({
  useSubscription: () => ({ isFeatureAvailable: () => false }),
}))

import CandidatesPage from '../pages/CandidatesPage'
import OnboardingWizard from '../components/OnboardingWizard'
import VoiceScreeningPage from '../pages/VoiceScreeningPage'

describe('undefined-identifier regressions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders Candidates page skill filter without crashing', () => {
    render(
      <MemoryRouter>
        <CandidatesPage />
      </MemoryRouter>
    )
    expect(screen.getByPlaceholderText('Filter by skill...')).toBeInTheDocument()
  })

  it('mounts OnboardingWizard without an undefined useSubscription reference', () => {
    render(
      <MemoryRouter>
        <OnboardingWizard />
      </MemoryRouter>
    )
    expect(screen.getByText('Organization Name')).toBeInTheDocument()
  })

  it('renders Voice Screening session filters without crashing', async () => {
    render(
      <MemoryRouter>
        <VoiceScreeningPage />
      </MemoryRouter>
    )
    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getAllByText('Scheduled').length).toBeGreaterThan(0)
  })
})
