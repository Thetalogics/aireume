import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ResultCard from '../components/ResultCard'

// Make requestAnimationFrame fire callbacks synchronously so
// StreamingText typewriter animation completes immediately in tests
const originalRAF = window.requestAnimationFrame
const originalCAF = window.cancelAnimationFrame

beforeAll(() => {
  vi.stubGlobal('requestAnimationFrame', (cb) => {
    cb(Date.now())
    return 0
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
})

afterAll(() => {
  vi.stubGlobal('requestAnimationFrame', originalRAF)
  vi.stubGlobal('cancelAnimationFrame', originalCAF)
})

const mockResult = {
  fit_score: 85,
  strengths: ['Strong Python skills', 'Leadership experience'],
  concerns: ['Limited cloud experience'],
  education_analysis: 'Good educational background',
  risk_signals: [],
  final_recommendation: 'Shortlist'
}

describe('ResultCard', () => {
  it('renders the recommendation badge', () => {
    render(<ResultCard result={mockResult} />)
    expect(screen.getByText('Advance')).toBeInTheDocument()
  })

  it('displays strengths', () => {
    render(<ResultCard result={mockResult} />)
    expect(screen.getByText('Strong Python skills')).toBeInTheDocument()
  })

  it('displays concerns', () => {
    render(<ResultCard result={mockResult} />)
    expect(screen.getByText('Limited cloud experience')).toBeInTheDocument()
  })

  it('displays concerns section heading', () => {
    render(<ResultCard result={mockResult} />)
    expect(screen.getByText('Concerns')).toBeInTheDocument()
  })

  it('toggles education analysis on click', () => {
    render(<ResultCard result={mockResult} />)
    const button = screen.getByText('Education Analysis')
    fireEvent.click(button)
    expect(screen.getByText('Good educational background')).toBeInTheDocument()
  })

  it('shows correct colors for different recommendations', () => {
    const { rerender } = render(<ResultCard result={{ ...mockResult, final_recommendation: 'Reject' }} />)
    expect(screen.getByText('Not a fit')).toBeInTheDocument()

    rerender(<ResultCard result={{ ...mockResult, final_recommendation: 'Consider' }} />)
    expect(screen.getByText('Consider')).toBeInTheDocument()
  })

  it('displays fit_summary banner when provided', () => {
    const resultWithSummary = {
      ...mockResult,
      fit_summary: 'Strong candidate with excellent technical skills and leadership experience.'
    }
    render(<ResultCard result={resultWithSummary} />)
    expect(screen.getByText('Executive Summary')).toBeInTheDocument()
    // StreamingText renders the summary twice: an aria-hidden visual span and an
    // sr-only live region for screen readers — so there are two matching nodes.
    expect(
      screen.getAllByText('Strong candidate with excellent technical skills and leadership experience.').length
    ).toBeGreaterThanOrEqual(1)
  })

  it('displays risk flags when provided', () => {
    const resultWithRiskFlags = {
      ...mockResult,
      risk_summary: {
        risk_flags: [
          { flag: 'Frequent job changes', detail: '3 jobs in 2 years', severity: 'medium' },
          { flag: 'Employment gap', detail: '6 months gap', severity: 'low' }
        ]
      }
    }
    render(<ResultCard result={resultWithRiskFlags} />)
    expect(screen.getByText('Risk Flags')).toBeInTheDocument()
    expect(screen.getByText('Frequent job changes')).toBeInTheDocument()
    expect(screen.getByText('Employment gap')).toBeInTheDocument()
  })

  it('displays seniority alignment when provided', () => {
    const resultWithSeniority = {
      ...mockResult,
      score_breakdown: {
        skill_match: { score: 85, confidence_weighted: false, avg_confidence: 1.0 },
        experience_match: 80,
        education: 75,
        timeline: 90
      },
      risk_summary: {
        seniority_alignment: 'Well-aligned for Senior Engineer role'
      }
    }
    render(<ResultCard result={resultWithSeniority} />)
    expect(screen.getByText('Seniority Alignment:')).toBeInTheDocument()
    expect(screen.getByText('Well-aligned for Senior Engineer role')).toBeInTheDocument()
  })

  it('renders detailed experience_match objects without crashing', () => {
    const resultWithDetailedExp = {
      ...mockResult,
      score_breakdown: {
        skill_match: {
          score: 72,
          confidence_weighted: true,
          avg_confidence: 0.9,
          required_total: 5,
          required_matched: 4,
          nice_total: 2,
          nice_matched: 1,
          missing_required: [],
          matched_details: [],
          proficiency_adjustments: [],
        },
        experience_match: {
          score: 60,
          actual_years: 4,
          required_years: 5,
          explanation: 'Slightly under required years',
        },
        education: 75,
        timeline: 90,
      },
    }
    render(<ResultCard result={resultWithDetailedExp} />)
    expect(screen.getByText('Score Breakdown')).toBeInTheDocument()
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()
  })

  it('displays skill depth counts when provided', () => {
    const resultWithSkillDepth = {
      ...mockResult,
      matched_skills: ['Python', 'React'],
      skill_depth: { 'Python': 8, 'React': 3 }
    }
    render(<ResultCard result={resultWithSkillDepth} />)
    expect(screen.getByText('(8x)')).toBeInTheDocument()
    expect(screen.getByText('(3x)')).toBeInTheDocument()
  })

  it('uses score_rationales when explainability is missing', () => {
    const resultWithRationales = {
      ...mockResult,
      score_rationales: {
        skill_rationale: 'Strong technical skills match',
        experience_rationale: 'Relevant industry experience',
        overall_rationale: 'Good overall fit'
      }
    }
    render(<ResultCard result={resultWithRationales} />)
    expect(screen.getByText('Score Rationales — Why this score?')).toBeInTheDocument()
    // Click to expand the collapsible section
    const button = screen.getByText('Score Rationales — Why this score?')
    fireEvent.click(button)
    expect(screen.getByText('Strong technical skills match')).toBeInTheDocument()
    expect(screen.getByText('Relevant industry experience')).toBeInTheDocument()
  })

  it('falls back to weaknesses when concerns is not provided', () => {
    const resultWithWeaknesses = {
      ...mockResult,
      concerns: undefined,
      weaknesses: ['Old weakness format']
    }
    render(<ResultCard result={resultWithWeaknesses} />)
    expect(screen.getByText('Old weakness format')).toBeInTheDocument()
  })

  it('renders tiered skill analysis with O*NET hot skills without crashing', () => {
    const resultWithTieredSkills = {
      ...mockResult,
      skill_analysis: {
        matched_required: ['Python'],
        missing_required: ['Kubernetes'],
        required_match_pct: 50,
        nice_to_have_match_pct: 50,
        matched_nice_to_have: ['Docker'],
        missing_nice_to_have: ['Terraform'],
      },
      onet_hot_skills: ['Python', 'Kubernetes'],
    }
    render(<ResultCard result={resultWithTieredSkills} />)
    expect(screen.getByText('Must-Have Skills')).toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
    expect(screen.getByText('Good-to-Have Skills')).toBeInTheDocument()
    expect(screen.getByText('Docker')).toBeInTheDocument()
  })
})
