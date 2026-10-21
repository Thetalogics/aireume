import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MfaSettingsPanel from '../components/settings/MfaSettingsPanel'
import { enableMfa, setupMfa } from '../lib/api'

const loadUser = vi.fn()

vi.mock('../contexts/useAuth', () => ({
  useAuth: () => ({
    user: { mfa_required: true, mfa_enabled: false },
    loadUser,
  }),
}))

vi.mock('../lib/api', () => ({
  setupMfa: vi.fn(),
  enableMfa: vi.fn(),
  disableMfa: vi.fn(),
}))

vi.mock('../lib/toast', () => ({
  showSuccess: vi.fn(),
  showError: vi.fn(),
}))

describe('MfaSettingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setupMfa.mockResolvedValue({
      secret: 'JBSWY3DPEHPK3PXP',
      otpauth_url: 'otpauth://totp/ARIA%3Aadmin%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=ARIA',
    })
    enableMfa.mockResolvedValue({ mfa_enabled: true })
  })

  it('renders the server provisioning URI as a local QR code', async () => {
    render(<MfaSettingsPanel />)

    fireEvent.click(screen.getByRole('switch'))

    const qr = await screen.findByTestId('mfa-qr-code')
    expect(qr.querySelector('svg')).toBeInTheDocument()
    expect(screen.getByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument()
    expect(screen.queryByText(/otpauth:\/\//)).not.toBeInTheDocument()
  })

  it('accepts only six digits and clears enrollment material after verification', async () => {
    render(<MfaSettingsPanel />)
    fireEvent.click(screen.getByRole('switch'))
    await screen.findByTestId('mfa-qr-code')

    const input = screen.getByLabelText('Authenticator code')
    fireEvent.change(input, { target: { value: '12a34567' } })
    expect(input).toHaveValue('123456')

    fireEvent.click(screen.getByRole('button', { name: 'Verify and enable' }))

    await waitFor(() => expect(enableMfa).toHaveBeenCalledWith('123456'))
    expect(loadUser).toHaveBeenCalled()
    expect(screen.queryByTestId('mfa-qr-code')).not.toBeInTheDocument()
    expect(screen.queryByText('JBSWY3DPEHPK3PXP')).not.toBeInTheDocument()
  })
})
