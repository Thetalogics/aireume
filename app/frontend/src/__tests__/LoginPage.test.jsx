import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const loginMock = vi.fn()
const navigateMock = vi.fn()

vi.mock('../contexts/useAuth', () => ({
  useAuth: () => ({ login: loginMock }),
}))

vi.mock('../lib/api', () => ({
  getSSOConfig: vi.fn().mockResolvedValue(null),
  getOAuthProviders: vi.fn().mockResolvedValue({ providers: [] }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})

import LoginPage from '../pages/LoginPage'

const renderPage = () =>
  render(<MemoryRouter><LoginPage /></MemoryRouter>)

describe('LoginPage', () => {
  beforeEach(() => {
    loginMock.mockReset()
    navigateMock.mockReset()
  })

  it('passes the tenant slug through to login (multi-tenant scoping)', async () => {
    loginMock.mockResolvedValue({ user: { role: 'admin' } })
    renderPage()

    fireEvent.change(screen.getByPlaceholderText('your-company'), { target: { value: 'acme' } })
    fireEvent.change(screen.getByLabelText(/^email$/i), { target: { value: 'hr@acme.com' } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'Secret123!' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith('hr@acme.com', 'Secret123!', 'acme', undefined)
    })
  })

  it('shows an error message when login fails', async () => {
    loginMock.mockRejectedValue({ response: { data: { detail: 'Invalid email or password' } } })
    renderPage()

    fireEvent.change(screen.getByPlaceholderText('your-company'), { target: { value: 'acme' } })
    fireEvent.change(screen.getByLabelText(/^email$/i), { target: { value: 'hr@acme.com' } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByText(/invalid email or password/i)).toBeInTheDocument()
  })

  it('toggles password visibility', () => {
    renderPage()
    const pw = screen.getByPlaceholderText('••••••••')
    expect(pw).toHaveAttribute('type', 'password')
    fireEvent.click(screen.getByRole('button', { name: /show password/i }))
    expect(pw).toHaveAttribute('type', 'text')
  })
})
