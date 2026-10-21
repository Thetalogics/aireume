import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AnalyticsHub from '../components/patterns/AnalyticsHub'
import { VALID_SLICE_IDS } from '../components/patterns/analyticsConstants'
import { getAnalyticsHub } from '../lib/api'

vi.mock('../components/patterns/ScreeningTrendsCharts', () => ({
  default: () => null,
}))

vi.mock('../lib/api', () => ({
  getAnalyticsHub: vi.fn(() => Promise.resolve({
    generated_at: new Date().toISOString(),
    filter_options: { requisitions: [], recruiters: [] },
    attention: { stale_candidates: [], zero_pipeline_requisitions: [], pending_hm_review: 0 },
    slices: {
      screening: {
        kpis: { total_analyzed: 0, avg_fit_score: 0, recommendation_shortlist_rate: 0, pipeline_shortlist_rate: 0 },
        recommendation_distribution: {},
        score_distribution: {},
        top_skill_gaps: [],
        drill_down: [],
        drill_down_pagination: { total_count: 0, limit: 50, offset: 0, has_more: false },
        trends: { total_analyzed: 0 },
      },
    },
  })),
  getReportTemplates: vi.fn(() => Promise.resolve({ templates: [] })),
  runAnalyticsReport: vi.fn(),
  getBiExportManifest: vi.fn(() => Promise.resolve({ endpoints: { hub: '/api/analytics/hub' } })),
}))

describe('AnalyticsHub', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses the latest completion callback without refetching for callback-only renders', async () => {
    let finish
    getAnalyticsHub.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const first = vi.fn()
    const latest = vi.fn()
    const { rerender } = render(<MemoryRouter><AnalyticsHub period="last_30_days" onGeneratedAt={first} /></MemoryRouter>)
    rerender(<MemoryRouter><AnalyticsHub period="last_30_days" onGeneratedAt={latest} /></MemoryRouter>)
    finish({ generated_at: 'new result', slices: {}, filter_options: {} })
    await waitFor(() => expect(latest).toHaveBeenCalledWith('new result'))
    expect(first).not.toHaveBeenCalled()
    expect(getAnalyticsHub).toHaveBeenCalledTimes(1)
    rerender(<MemoryRouter><AnalyticsHub period="last_7_days" onGeneratedAt={latest} /></MemoryRouter>)
    await waitFor(() => expect(getAnalyticsHub).toHaveBeenCalledTimes(2))
    expect(getAnalyticsHub).toHaveBeenLastCalledWith(expect.objectContaining({ period: 'last_7_days' }))
  })

  it('exports valid slice ids including reports', () => {
    expect(VALID_SLICE_IDS.has('screening')).toBe(true)
    expect(VALID_SLICE_IDS.has('reports')).toBe(true)
    expect(VALID_SLICE_IDS.has('foo')).toBe(false)
  })

  it('renders screening slice by default', async () => {
    render(
      <MemoryRouter>
        <AnalyticsHub
          period="last_30_days"
          activeSlice="screening"
          permissions={{ role: 'admin', isAdmin: true }}
        />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/Total analyzed/i)).toBeInTheDocument()
  })

  it('renders explore slices without reports tab when hidden', async () => {
    render(
      <MemoryRouter>
        <AnalyticsHub
          period="last_30_days"
          activeSlice="screening"
          permissions={{ role: 'admin', isAdmin: true }}
          hideReportsTab
        />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/Total analyzed/i)).toBeInTheDocument()
    expect(screen.queryByText(/^Reports$/)).not.toBeInTheDocument()
  })
})
