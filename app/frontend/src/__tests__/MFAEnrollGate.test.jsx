import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import MFAEnrollGate from '../components/MFAEnrollGate'

vi.mock('../contexts/useAuth', () => ({
  useAuth: () => ({
    user: { mfa_required: true, mfa_enabled: false },
    loading: false,
  }),
}))

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>
}

describe('MFAEnrollGate', () => {
  it('sends unenrolled users from recruiter pages to the security tab', () => {
    render(
      <MemoryRouter initialEntries={['/candidates']}>
        <MFAEnrollGate>
          <LocationProbe />
        </MFAEnrollGate>
      </MemoryRouter>
    )
    expect(screen.getByTestId('loc')).toHaveTextContent('/settings?tab=security')
  })

  it('allows the security tab', () => {
    render(
      <MemoryRouter initialEntries={['/settings?tab=security']}>
        <MFAEnrollGate>
          <div>enroll here</div>
        </MFAEnrollGate>
      </MemoryRouter>
    )
    expect(screen.getByText('enroll here')).toBeInTheDocument()
  })
})
