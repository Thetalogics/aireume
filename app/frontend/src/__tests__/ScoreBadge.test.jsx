import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ScoreBadge from '../components/ScoreBadge'
import RecommendationBadge from '../components/RecommendationBadge'

describe('ScoreBadge a11y', () => {
  it('exposes an aria-label with the numeric score', () => {
    render(<ScoreBadge score={82} animated={false} />)
    expect(screen.getByLabelText(/fit score 82/i)).toBeInTheDocument()
  })
})

describe('RecommendationBadge a11y', () => {
  it('exposes an aria-label with the recommendation', () => {
    render(<RecommendationBadge score={82} recommendation="Shortlist" />)
    expect(screen.getByLabelText(/recommendation:\s*advance/i)).toBeInTheDocument()
  })
})
