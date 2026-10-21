import { act, renderHook } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import useAnalyzePage from '../components/analyze/useAnalyzePage'
import * as api from '../lib/api'

const state = vi.hoisted(() => ({
  location: { search: '', state: null }, hasRequisitions: false,
  notifications: { addNotification: vi.fn(), updateEnrichmentJob: vi.fn(), completeEnrichmentJob: vi.fn() },
}))
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn(), useLocation: () => state.location }))
vi.mock('../hooks/useSubscription', () => ({
  useUsageCheck: () => ({ getRemainingAnalyses: () => 10 }),
  useSubscription: () => ({ isFeatureAvailable: (feature) => feature === 'requisitions' && state.hasRequisitions }),
}))
vi.mock('../components/usePlanLimits', () => ({ usePlanLimits: () => ({ batchSize: 10 }) }))
vi.mock('../contexts/useNotification', () => ({ useNotification: () => state.notifications }))
vi.mock('../contexts/useOnboarding', () => ({ useOnboarding: () => ({ completeChecklistItem: vi.fn() }) }))
vi.mock('../hooks/useFeatureGuide', () => ({ default: () => ({ open: false }) }))
vi.mock('../lib/api', async (original) => ({ ...await original(),
  checkHealth: vi.fn().mockResolvedValue({}), getRequisitionsForPicker: vi.fn().mockResolvedValue([]),
  getRequisitionSettings: vi.fn(), getNarrative: vi.fn(),
}))

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  state.location = { search: '', state: null }
  state.hasRequisitions = false
})
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

it('restores a draft after requisition settings permit ad-hoc screening, only once', async () => {
  let stored = JSON.stringify({ jd_text: 'Saved draft', weights: { skill: 100 }, role_category: 'engineering' })
  vi.spyOn(localStorage, 'getItem').mockImplementation(key => key === 'aria_draft_jd' ? stored : null)
  vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => { if (key === 'aria_draft_jd') stored = value })
  state.hasRequisitions = true
  api.getRequisitionSettings.mockResolvedValue({ screening_mode: 'allow_ad_hoc' })
  const { result, rerender } = renderHook(useAnalyzePage)
  await act(async () => {})
  expect(result.current.jdText).toBe('Saved draft')
  act(() => result.current.setJdText('Recruiter edit'))
  rerender()
  expect(result.current.jdText).toBe('Recruiter edit')
  expect(api.getRequisitionSettings).toHaveBeenCalledTimes(1)
})

it('initializes from route state and preserves manually selected weights and back navigation', async () => {
  state.location = { search: '', state: { jd_text: 'Role from route', weights: { skills: 100 }, role_category: 'tech' } }
  const { result } = renderHook(useAnalyzePage)
  await act(async () => {})
  expect(result.current.jdText).toBe('Role from route')
  expect(result.current.currentStep).toBe(2)
  act(() => result.current.handleWeightsChange({ skills: 70, experience: 30 }))
  act(() => result.current.setJdParseResult({ seniority: 'senior' }))
  expect(result.current.weights).toEqual({ skills: 70, experience: 30 })
  act(() => result.current.setSkillsConfirmed(true))
  act(() => result.current.setCurrentStep(1))
  expect(result.current.currentStep).toBe(1)
})

it('cancels the next narrative poll on unmount after the first poll reschedules', async () => {
  state.location = { search: '?restored=true', state: null }
  vi.spyOn(sessionStorage, 'getItem').mockImplementation(key => key === 'aria_batch_results' ? JSON.stringify({
    timestamp: Date.now(), results: [{ screeningResultId: 7, filename: 'resume.pdf', result: { narrative_status: 'pending', interview_kit_status: 'pending' } }],
  }) : null)
  api.getNarrative.mockResolvedValue({ status: 'pending', interview_kit_status: 'pending' })
  const { result, unmount } = renderHook(useAnalyzePage)
  await act(async () => vi.advanceTimersByTimeAsync(2000))
  expect(result.current.analysisDone).toBe(true)
  expect(api.getNarrative).toHaveBeenCalledTimes(1)
  unmount()
  await act(async () => vi.advanceTimersByTimeAsync(3000))
  expect(api.getNarrative).toHaveBeenCalledTimes(1)
})
