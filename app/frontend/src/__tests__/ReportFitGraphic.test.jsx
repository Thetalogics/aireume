import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ReportFitGraphic from '../components/patterns/ReportFitGraphic'

const result = {
  fit_score: 82,
  final_recommendation: 'Shortlist',
  fit_summary: 'Strong Python and API experience. Minor gap on Kubernetes.',
  concerns: ['Limited K8s'],
  dealbreakers: [],
  differentiators: ['Led platform migration'],
  hiring_decision: { verdict: 'Shortlist', action_items: ['Phone screen'] },
  skill_analysis: {
    matched_required: ['Python', 'FastAPI'],
    missing_required: ['Kubernetes'],
  },
  score_breakdown: {
    experience_match: { actual_years: 8, required_years: 5, explanation: '8 years exceeds the 5-year bar.' },
  },
}

describe('ReportFitGraphic', () => {
  it('shows the score, the requirement list, and the year bar', () => {
    render(<ReportFitGraphic result={result} />)
    expect(screen.getByLabelText('Score 82 of 100')).toBeInTheDocument()
    expect(screen.getByText('Advance')).toBeInTheDocument()
    expect(screen.getByText('Phone screen')).toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
    expect(screen.getByText('Kubernetes')).toBeInTheDocument()
    expect(screen.getAllByText('Found')).toHaveLength(2)
    expect(screen.getByText('Missing')).toBeInTheDocument()
    expect(screen.getByText('8 years exceeds the 5-year bar.')).toBeInTheDocument()
    expect(screen.getByText(/No deal-breakers/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /play again/i })).not.toBeInTheDocument()
  })

  it('renders nothing without a score', () => {
    const { container } = render(<ReportFitGraphic result={{ fit_summary: 'No score yet' }} />)
    expect(container).toBeEmptyDOMElement()
  })
})
