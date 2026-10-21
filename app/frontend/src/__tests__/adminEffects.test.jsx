import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import SettingsPage from '../pages/SettingsPage'
import SSOPage from '../pages/admin/SSOPage'
import * as api from '../lib/api'

const auth = vi.hoisted(() => ({ user: { id: 1, role: 'admin' }, tenant: { name: 'Workspace' } }))
vi.mock('../contexts/useAuth', () => ({ useAuth: () => auth }))
vi.mock('../contexts/useOnboarding', () => ({ useOnboarding: () => ({ completeChecklistItem: vi.fn() }) }))
vi.mock('../contexts/useUserPreferences', () => ({ useUserPreferences: () => ({ preferences: {} }) }))
vi.mock('../hooks/useSubscription', () => ({ useSubscription: () => ({ isFeatureAvailable: () => false, getUsageStats: () => null, getCurrentPlan: () => null, getRemainingAnalyses: () => 0 }) }))
vi.mock('../lib/api', async (original) => ({ ...await original(), getInvoices: vi.fn(), getAdminTenants: vi.fn(), getTenantSSO: vi.fn() }))

beforeEach(() => { vi.clearAllMocks(); auth.user = { id: 1, role: 'admin' } })

it('refreshes billing when administrator access returns without switching tabs', async () => {
  api.getInvoices.mockResolvedValue({ invoices: [], total: 0 })
  const { rerender } = render(<MemoryRouter initialEntries={['/settings?tab=billing']}><SettingsPage /></MemoryRouter>)
  await waitFor(() => expect(api.getInvoices).toHaveBeenCalledTimes(1))
  auth.user = { id: 1, role: 'viewer' }
  rerender(<MemoryRouter><SettingsPage /></MemoryRouter>)
  expect(api.getInvoices).toHaveBeenCalledTimes(1)
  auth.user = { id: 1, role: 'admin' }
  rerender(<MemoryRouter><SettingsPage /></MemoryRouter>)
  await waitFor(() => expect(api.getInvoices).toHaveBeenCalledTimes(2))
})

it('loads SSO for a selected tenant without reloading or resetting the tenant list', async () => {
  api.getAdminTenants.mockResolvedValue({ items: [{ id: 1, name: 'First', slug: 'first' }, { id: 2, name: 'Second', slug: 'second' }] })
  api.getTenantSSO.mockResolvedValue({ enabled: false })
  render(<SSOPage />)
  const selector = await screen.findByLabelText('Select Tenant')
  fireEvent.change(selector, { target: { value: '2' } })
  await waitFor(() => expect(api.getTenantSSO).toHaveBeenLastCalledWith(2))
  expect(selector).toHaveValue('2')
  expect(api.getAdminTenants).toHaveBeenCalledTimes(1)
})
