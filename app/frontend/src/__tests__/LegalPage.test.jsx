import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import LegalPage from '../pages/LegalPage'

function renderLegal(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/legal/:document" element={<LegalPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LegalPage', () => {
  it.each([
    ['/legal/terms', 'Terms of Service'],
    ['/legal/privacy', 'Privacy Notice'],
    ['/legal/subprocessors', 'Subprocessors'],
  ])('renders %s', (path, heading) => {
    renderLegal(path)
    expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    expect(screen.getByText('support@thetalogics.com')).toHaveAttribute('href', 'mailto:support@thetalogics.com')
  })
})
