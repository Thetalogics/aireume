import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ReportPage from '../pages/ReportPage'
import api, { getScreeningResult } from '../lib/api'

const stable = vi.hoisted(() => ({ noop: vi.fn() }))
vi.mock('../lib/api', async (original) => ({ ...await original(), default: { get: vi.fn() },
  getScreeningResult: vi.fn(), getCandidateAuditLog: vi.fn().mockResolvedValue([]),
  getInterviewSessions: vi.fn().mockResolvedValue({ sessions: [] }),
}))
vi.mock('../contexts/useOnboarding', () => ({ useOnboarding: () => ({ completeChecklistItem: stable.noop }) }))
vi.mock('../hooks/usePermissions', () => ({ default: () => ({ canWrite: true }) }))
vi.mock('../hooks/useSubscription', () => ({ useSubscription: () => ({ isFeatureAvailable: () => false }) }))
vi.mock('../contexts/useNotification', () => ({ useNotification: () => ({ trackEnrichmentJob: stable.noop, updateEnrichmentJob: stable.noop, completeEnrichmentJob: stable.noop, addNotification: stable.noop }) }))
vi.mock('../contexts/useLiveScreenMode', () => ({ useLiveScreenMode: () => ({ setActive: stable.noop }) }))
vi.mock('../hooks/useEnrichmentPolling', () => ({ useEnrichmentPolling: () => {} }))
vi.mock('../components/patterns', () => ({
  EnrichmentBanner: () => null, ReportActionBar: () => null, AnalysisStageTracker: () => null, RescoreSheet: () => null,
  ConsolidatedScoreHero: ({ result }) => <div>{result.candidate_name}</div>,
  LiveScreenCallShell: ({ resumeBlobUrl, resumeText, onExit }) => <div><output>{resumeBlobUrl || resumeText}</output><button onClick={onExit}>Exit screen</button></div>,
  LiveScreenKitReadinessGate: ({ open, onStartWithFallback }) => open && <button onClick={onStartWithFallback}>Use fallback</button>,
}))

function report(id) {
  return { id, result_id: id, candidate_id: id, candidate_name: `Candidate ${id}`, analysis_result: { fit_score: 75 }, strengths: [], narrative_status: 'ready', interview_kit_status: 'ready', contact_info: { name: `Candidate ${id}` } }
}
function NextReport() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/report', { state: { result: report(2) } })}>Next report</button>
}
function mountReport() {
  return render(<MemoryRouter initialEntries={[{ pathname: '/report', state: { result: report(1) } }]}><NextReport /><ReportPage /></MemoryRouter>)
}
async function openScreen() {
  fireEvent.click(await screen.findByRole('button', { name: 'Open kit' }))
  const fallback = screen.queryByRole('button', { name: 'Use fallback' })
  if (fallback) fireEvent.click(fallback)
  await screen.findByRole('button', { name: 'Exit screen' })
}

beforeEach(() => {
  vi.clearAllMocks()
  getScreeningResult.mockImplementation(async id => report(Number(id)))
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:resume')
    static revokeObjectURL = vi.fn()
  })
})
afterEach(() => vi.unstubAllGlobals())

it('loads a new route-state report even when the search string is unchanged', async () => {
  mountReport()
  await screen.findAllByText('Candidate 1')
  fireEvent.click(screen.getByText('Next report'))
  expect((await screen.findAllByText('Candidate 2')).length).toBeGreaterThan(0)
})

it('ignores a report fetch that finishes after navigation to a different report', async () => {
  let finishOld
  getScreeningResult.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
  render(<MemoryRouter initialEntries={['/report?id=1']}><NextReport /><ReportPage /></MemoryRouter>)
  await waitFor(() => expect(getScreeningResult).toHaveBeenCalledWith('1'))
  fireEvent.click(screen.getByText('Next report'))
  await screen.findAllByText('Candidate 2')
  await act(async () => finishOld(report(1)))
  expect(screen.queryAllByText('Candidate 1')).toHaveLength(0)
})

it('creates one resume URL per screening session and revokes it exactly once on exit', async () => {
  api.get.mockResolvedValue({ data: new Blob(['pdf'], { type: 'application/pdf' }), headers: {} })
  const { unmount } = mountReport()
  await openScreen()
  await waitFor(() => expect(screen.getByText('blob:resume')).toBeInTheDocument())
  expect(api.get).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByText('Exit screen'))
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1)
  await openScreen()
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
  unmount()
  expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
})

it('does not allocate a resume URL when the request finishes after screening closes', async () => {
  let resolveResume
  api.get.mockImplementation(() => new Promise(resolve => { resolveResume = resolve }))
  mountReport()
  await openScreen()
  fireEvent.click(screen.getByText('Exit screen'))
  await act(async () => resolveResume({ data: new Blob(['pdf'], { type: 'application/pdf' }), headers: {} }))
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
