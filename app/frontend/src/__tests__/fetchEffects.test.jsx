import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import CandidatesPage from '../pages/CandidatesPage'
import ComparisonMatrix from '../components/ComparisonMatrix'
import HandoffPackage from '../components/HandoffPackage'
import InterviewDetailPage from '../pages/InterviewDetailPage'
import VoiceScreeningPage from '../pages/VoiceScreeningPage'
import RecruiterInterviewPage from '../pages/RecruiterInterviewPage'
import * as api from '../lib/api'

vi.mock('../lib/api', async (original) => ({
  ...await original(), getCandidates: vi.fn(), compareCandidates: vi.fn(), getHandoffPackage: vi.fn(),
  getRecruiterSession: vi.fn(), getVoiceSession: vi.fn(),
  getVoiceSettings: vi.fn().mockResolvedValue({}), getVoiceAnalytics: vi.fn().mockResolvedValue(null), getVoiceSessions: vi.fn(),
  getRecruiterConfig: vi.fn().mockResolvedValue({}), getRecruiterAnalytics: vi.fn().mockResolvedValue(null), getRecruiterSessions: vi.fn(),
}))
vi.mock('../hooks/useFeatureGuide', () => ({ default: () => ({ open: false }) }))
vi.mock('../hooks/usePermissions', () => ({ default: () => ({ canWrite: true }) }))
vi.mock('../contexts/useOnboarding', () => ({ useOnboarding: () => ({ completeChecklistItem: vi.fn() }) }))
beforeEach(() => {
  vi.clearAllMocks()
  api.getCandidates.mockResolvedValue({ candidates: [], total: 0 })
})

function NavigateButton({ to }) {
  const navigate = useNavigate()
  return <button onClick={() => navigate(to)}>Change route</button>
}

it('fetches candidates once on mount and once per URL/filter change', async () => {
  render(<MemoryRouter initialEntries={['/candidates?status=shortlisted']}>
    <NavigateButton to="/candidates?status=rejected&requisition_id=7" /><CandidatesPage />
  </MemoryRouter>)
  await waitFor(() => expect(api.getCandidates).toHaveBeenCalled())
  expect(api.getCandidates).toHaveBeenCalledTimes(1)
  expect(api.getCandidates).toHaveBeenLastCalledWith({ search: '', page: 1, page_size: 20, status: 'shortlisted' })
  fireEvent.click(screen.getByText('Change route'))
  await waitFor(() => expect(api.getCandidates).toHaveBeenLastCalledWith({ search: '', page: 1, page_size: 20, status: 'rejected', requisition_id: 7 }))
  expect(api.getCandidates).toHaveBeenCalledTimes(2)
  fireEvent.change(screen.getByPlaceholderText('Filter by skill...'), { target: { value: 'React' } })
  await waitFor(() => expect(api.getCandidates).toHaveBeenLastCalledWith(expect.objectContaining({ skill: 'React', requisition_id: 7, status: 'rejected' })))
  expect(api.getCandidates).toHaveBeenCalledTimes(3)
})

it('ignores an obsolete comparison response after candidate inputs change', async () => {
  let resolveOld
  api.compareCandidates.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    .mockResolvedValueOnce({ skills_matrix: [], summary: { new: { name: 'New Candidate' } } })
  const { rerender } = render(<ComparisonMatrix candidateIds={[1, 2]} />)
  rerender(<ComparisonMatrix candidateIds={[3, 4]} />)
  expect(await screen.findByText('New Candidate')).toBeInTheDocument()
  await act(async () => resolveOld({ skills_matrix: [], summary: { old: { name: 'Old Candidate' } } }))
  expect(screen.queryByText('Old Candidate')).not.toBeInTheDocument()
  expect(api.compareCandidates).toHaveBeenCalledTimes(2)
})

it('resets candidate pagination once when filters change and waits for search submission', async () => {
  api.getCandidates.mockResolvedValue({ candidates: [{ id: 1, name: 'Ada', latest_status: 'pending' }], total: 45 })
  render(<MemoryRouter><CandidatesPage /></MemoryRouter>)
  fireEvent.click((await screen.findAllByRole('button', { name: 'Next' }))[0])
  await waitFor(() => expect(api.getCandidates).toHaveBeenLastCalledWith({ search: '', page: 2, page_size: 20 }))
  fireEvent.change(screen.getByPlaceholderText('Filter by skill...'), { target: { value: 'React' } })
  await waitFor(() => expect(api.getCandidates).toHaveBeenLastCalledWith({ search: '', page: 1, page_size: 20, skill: 'React' }))
  expect(api.getCandidates).toHaveBeenCalledTimes(3)
  const search = screen.getByPlaceholderText(/Search by name/)
  fireEvent.change(search, { target: { value: 'Ada' } })
  expect(api.getCandidates).toHaveBeenCalledTimes(3)
  fireEvent.submit(search.closest('form'))
  await waitFor(() => expect(api.getCandidates).toHaveBeenLastCalledWith({ search: 'Ada', page: 1, page_size: 20, skill: 'React' }))
  expect(api.getCandidates).toHaveBeenCalledTimes(4)
})

it('updates a public handoff when its supplied package changes without fetching', async () => {
  const { rerender } = render(<MemoryRouter><HandoffPackage publicMode initialData={{ jd_name: 'First role', candidates: [] }} /></MemoryRouter>)
  expect(screen.getByText(/First role/)).toBeInTheDocument()
  rerender(<MemoryRouter><HandoffPackage publicMode initialData={{ jd_name: 'Second role', candidates: [] }} /></MemoryRouter>)
  expect(await screen.findByText(/Second role/)).toBeInTheDocument()
  expect(api.getHandoffPackage).not.toHaveBeenCalled()
})

it('reloads interview details when only the source URL changes', async () => {
  api.getRecruiterSession.mockResolvedValue({ id: 1, candidate_name: 'Recruiter candidate', status: 'scheduled' })
  api.getVoiceSession.mockResolvedValue({ id: 1, candidate_name: 'Voice candidate', status: 'scheduled' })
  render(<MemoryRouter initialEntries={['/ai-interviews/1?source=recruiter']}>
    <NavigateButton to="/ai-interviews/1?source=voice" />
    <Routes><Route path="/ai-interviews/:id" element={<InterviewDetailPage />} /></Routes>
  </MemoryRouter>)
  await waitFor(() => expect(api.getRecruiterSession).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByText('Change route'))
  await waitFor(() => expect(api.getVoiceSession).toHaveBeenCalledTimes(1))
})

it.each([
  ['voice', VoiceScreeningPage, 'getVoiceSessions'],
  ['recruiter', RecruiterInterviewPage, 'getRecruiterSessions'],
])('fetches %s sessions once on mount and applies the current search', async (_name, Page, fetchName) => {
  api[fetchName].mockResolvedValue([{ id: 1, candidate_name: 'Ada', status: 'scheduled' }])
  render(<MemoryRouter><Page /></MemoryRouter>)
  await act(async () => new Promise(resolve => setTimeout(resolve, 400)))
  await waitFor(() => expect(api[fetchName]).toHaveBeenCalledTimes(1))
  const search = screen.getByPlaceholderText(/search/i)
  fireEvent.change(search, { target: { value: 'Ada' } })
  await waitFor(() => expect(api[fetchName]).toHaveBeenLastCalledWith({ limit: 50, search: 'Ada' }))
  expect(api[fetchName]).toHaveBeenCalledTimes(2)
})
