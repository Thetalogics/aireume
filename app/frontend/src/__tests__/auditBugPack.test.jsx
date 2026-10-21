import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'

vi.mock('axios', async () => {
  const mockAxiosInstance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  }
  return {
    default: {
      create: vi.fn(() => mockAxiosInstance),
      post: vi.fn(),
    },
    ...mockAxiosInstance,
  }
})

vi.mock('nanoid', () => ({ nanoid: () => 'audit-upload-id' }))
vi.mock('../contexts/useOnboarding', () => ({
  useOnboarding: () => ({ completeChecklistItem: vi.fn() }),
}))
vi.mock('../hooks/usePermissions', () => ({
  default: () => ({ canWrite: true }),
}))
vi.mock('../hooks/useSubscription', () => ({
  useSubscription: () => ({ isFeatureAvailable: () => false }),
}))
vi.mock('../contexts/useNotification', () => ({
  useNotification: () => ({
    trackEnrichmentJob: vi.fn(),
    updateEnrichmentJob: vi.fn(),
    completeEnrichmentJob: vi.fn(),
    addNotification: vi.fn(),
  }),
}))
vi.mock('../contexts/useLiveScreenMode', () => ({
  useLiveScreenMode: () => ({ setActive: vi.fn(), active: false }),
}))
vi.mock('../hooks/useEnrichmentPolling', () => ({
  useEnrichmentPolling: () => {},
}))
vi.mock('../lib/toast', () => ({ showSuccess: vi.fn(), showError: vi.fn() }))
vi.mock('../components/ScoreGauge', () => ({ default: () => null }))
vi.mock('../components/ResultCard', () => ({ default: () => null }))
vi.mock('../components/InterviewScorecard', () => ({ default: () => null }))
vi.mock('../components/Timeline', () => ({ default: () => null }))
vi.mock('../components/AnimatedScore', () => ({ default: () => null }))
vi.mock('../components/StreamingText', () => ({ default: () => null }))
vi.mock('../components/Skeleton', () => ({ default: () => null }))
vi.mock('../components/EvaluationChecklist', () => ({ default: () => null }))
vi.mock('../components/InterviewInitiateModal', () => ({ default: () => null }))
vi.mock('../components/RequireWriteAccess', () => ({ ViewerReadOnlyBanner: () => null }))
vi.mock('../components/patterns', () => ({
  EnrichmentBanner: () => null,
  ReportActionBar: () => null,
  AnalysisStageTracker: () => null,
  RescoreSheet: () => null,
  LiveScreenCallShell: () => null,
  LiveScreenKitReadinessGate: () => null,
  ConsolidatedScoreHero: ({ result }) => <div>{result?.candidate_name || result?.contact_info?.name}</div>,
}))
vi.mock('../components/OAuthButtons', () => ({ default: () => null }))
vi.mock('../contexts/useAuth', async (original) => ({
  ...await original(),
  useAuth: () => ({ register: vi.fn(), loading: false, logout: vi.fn(), user: null }),
  AuthProvider: ({ children }) => children,
}))

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    getScreeningResult: vi.fn(),
    getCandidateAuditLog: vi.fn().mockResolvedValue([]),
    getInterviewSessions: vi.fn().mockResolvedValue({ sessions: [] }),
    getInterviewScorecard: vi.fn().mockResolvedValue(null),
  }
})

describe('BUG-008 ReportPage stale id', () => {
  it('fetches the report for the new ?id= when the URL changes without remounting', async () => {
    const { getScreeningResult } = await import('../lib/api')
    getScreeningResult.mockImplementation(async (id) => ({
      result_id: Number(id),
      id: Number(id),
      candidate_name: `Candidate ${id}`,
      analysis_result: { fit_score: 70 },
      strengths: ['x'],
      narrative_status: 'ready',
      contact_info: { name: `Candidate ${id}` },
    }))

    const { default: ReportPage } = await import('../pages/ReportPage')
    function NextIdButton() {
      const navigate = useNavigate()
      return <button type="button" onClick={() => navigate('/report?id=22')}>next-id</button>
    }
    const hydrated = {
      result_id: 11,
      id: 11,
      candidate_name: 'Candidate 11',
      analysis_result: { fit_score: 88 },
      strengths: ['old'],
      narrative_status: 'ready',
      contact_info: { name: 'Candidate 11' },
      score_breakdown: {},
    }
    render(
      <MemoryRouter
        initialEntries={[{
          pathname: '/report',
          search: '?id=11',
          state: { result: hydrated },
        }]}
      >
        <Routes>
          <Route path="/report" element={<><NextIdButton /><ReportPage /></>} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getAllByText(/Candidate 11/i).length).toBeGreaterThan(0)
    })
    getScreeningResult.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'next-id' }))
    await new Promise((r) => setTimeout(r, 50))
    expect(getScreeningResult).toHaveBeenCalledWith('22')
  })
})

describe('BUG-009 SSE fetch refresh', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('retries analyzeResumeStream after refreshing an expired access token', async () => {
    const { default: axios } = await import('axios')
    axios.post.mockResolvedValue({ data: {} })
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"stage":"complete","result":{"fit_score":1}}\n\n'))
        controller.enqueue(encoder.encode('data: [DONE]\n\n'))
        controller.close()
      },
    })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Not authenticated' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        body: stream,
      })
    vi.stubGlobal('fetch', fetchMock)
    document.cookie = 'csrf_token=test-csrf'
    const { analyzeResumeStream } = await import('../lib/api')
    const file = new File(['resume'], 'resume.pdf', { type: 'application/pdf' })
    try {
      await analyzeResumeStream(file, 'We need a python developer with cloud experience and more words here for jd.')
    } catch {
      /* 401 without refresh is the bug under test */
    }
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(axios.post).toHaveBeenCalledWith(
      expect.stringMatching(/auth\/refresh/),
      expect.anything(),
      expect.anything(),
    )
  })
})

describe('BUG-014 public handoff passcode', () => {
  it('sends a passcode when opening a protected public handoff link', async () => {
    const api = (await import('../lib/api')).default
    api.get.mockResolvedValue({ data: { candidates: [] } })
    const { getPublicHandoff } = await import('../lib/api')
    await getPublicHandoff('tok123', { passcode: 's3cret' })
    const [url, config] = api.get.mock.calls[0] || []
    const sent =
      String(url || '').includes('passcode=') ||
      config?.headers?.['X-Handoff-Passcode'] === 's3cret' ||
      config?.params?.passcode === 's3cret'
    expect(sent).toBe(true)
  })
})

describe('BUG-015 createRequisitionFromFile PDF', () => {
  it('does not POST raw PDF bytes as jd_text', async () => {
    const api = (await import('../lib/api')).default
    api.post.mockResolvedValue({ data: { id: 1, title: 'Role' } })
    const { createRequisitionFromFile } = await import('../lib/api')
    const pdf = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34])], 'jd.pdf', {
      type: 'application/pdf',
    })
    if (typeof pdf.text !== 'function') {
      pdf.text = async () => '%PDF-1.4'
    }
    await createRequisitionFromFile('Role', pdf, [], null)
    const payload = api.post.mock.calls[0][1]
    const jdText = payload?.jd_text || ''
    expect(payload instanceof FormData || !String(jdText).includes('%PDF')).toBe(true)
  })
})

describe('BUG-016 logout clears screening storage', () => {
  it('clears ARIA sessionStorage keys on logout', async () => {
    const store = {}
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v) },
        removeItem: (k) => { delete store[k] },
        clear: () => { Object.keys(store).forEach((k) => delete store[k]) },
      },
    })
    window.sessionStorage.setItem('aria_batch_results', '[]')
    window.sessionStorage.setItem('aria_active_jd', '{}')
    window.sessionStorage.setItem('report_99', '{}')

    const api = (await import('../lib/api')).default
    api.get.mockRejectedValue({ response: { status: 401 } })
    api.post.mockResolvedValue({ data: {} })
    const { AuthProvider } = await vi.importActual('../contexts/AuthContext')
    const { useAuth } = await vi.importActual('../contexts/useAuth')
    function LogoutProbe() {
      const { logout } = useAuth()
      return <button type="button" onClick={() => logout()}>logout</button>
    }
    render(
      <AuthProvider>
        <LogoutProbe />
      </AuthProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'logout' }))
    await waitFor(() => {
      expect(window.sessionStorage.getItem('aria_batch_results')).toBeNull()
      expect(window.sessionStorage.getItem('aria_active_jd')).toBeNull()
      expect(window.sessionStorage.getItem('report_99')).toBeNull()
    })
  })
})

describe('BUG-018 chunk 0 uploaded first', () => {
  it('finishes chunk 0 before starting later chunks', async () => {
    const { ChunkedUploader } = await import('../lib/uploadChunked')
    const file = {
      name: 'big.pdf',
      size: 25 * 1024 * 1024,
      slice: () => new Blob(['x']),
    }
    const uploader = new ChunkedUploader(file)
    const startedAt = {}
    const endedAt = {}
    let clock = 0
    uploader.uploadChunk = vi.fn(async (index) => {
      startedAt[index] = ++clock
      await new Promise((r) => setTimeout(r, 20))
      endedAt[index] = ++clock
      return { ok: true }
    })
    await uploader.uploadChunks()
    expect(endedAt[0]).toBeGreaterThan(0)
    expect(startedAt[1]).toBeGreaterThan(0)
    expect(startedAt[2]).toBeGreaterThan(0)
    expect(endedAt[0]).toBeLessThan(startedAt[1])
    expect(endedAt[0]).toBeLessThan(startedAt[2])
  })
})

describe('BUG-019 register password copy', () => {
  it('uses minLength 10 to match the password policy', async () => {
    const { default: RegisterPage } = await import('../pages/RegisterPage')
    render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>,
    )
    const input = document.getElementById('register-password')
    expect(input).toHaveAttribute('minLength', '10')
  })
})
