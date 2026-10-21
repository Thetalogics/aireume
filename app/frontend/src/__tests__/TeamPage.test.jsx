import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import TeamPage from '../pages/TeamPage'
import { getTeamMembers, getTenantAuditLogs, getTrainingStatus } from '../lib/api'

vi.mock('../contexts/useAuth', () => ({
  useAuth: () => ({ user: { role: 'admin' } }),
}))

vi.mock('../lib/api', () => ({
  getTeamMembers: vi.fn(),
  getTenantAuditLogs: vi.fn(),
  getTrainingStatus: vi.fn(),
  inviteTeamMember: vi.fn(),
  startTraining: vi.fn(),
}))

describe('Team prompt personalization', () => {
  beforeEach(() => {
    getTeamMembers.mockResolvedValue([])
    getTenantAuditLogs.mockResolvedValue({ entries: [] })
    getTrainingStatus.mockResolvedValue({
      labeled_count: 10, trained: false, model_name: null, last_trained: null,
    })
  })

  it('explains how labeled hiring outcomes personalize the prompt', async () => {
    render(<TeamPage />)

    expect(await screen.findByRole('heading', { name: 'Prompt personalization' })).toBeInTheDocument()
    expect(screen.getByText(/hired.*rejected.*report page/i)).toBeInTheDocument()
    expect(screen.getByText(/up to 50.*Ollama Modelfile SYSTEM prompt/i)).toBeInTheDocument()
    expect(screen.getByText(/model weights stay unchanged/i)).toBeInTheDocument()
    expect(screen.queryByText(/fine.tune|train custom model|custom ai training|learn your company/i)).not.toBeInTheDocument()
  })

  it.each([9, 10])('requires at least 10 labeled outcomes (%i available)', async (count) => {
    getTrainingStatus.mockResolvedValue({
      labeled_count: count, trained: false, model_name: null, last_trained: null,
    })
    render(<TeamPage />)

    const button = await screen.findByRole('button', { name: 'Personalize ARIA' })
    if (count < 10) expect(button).toBeDisabled()
    else expect(button).toBeEnabled()
  })

  it.each([
    [false, 'Prompt personalization not completed'],
    [true, 'Prompt personalization ready'],
  ])('describes completion without implying weight training (%s)', async (trained, description) => {
    getTrainingStatus.mockResolvedValue({
      labeled_count: 10, trained, model_name: 'aria-42', last_trained: trained ? '2026-10-21T00:00:00Z' : null,
    })
    render(<TeamPage />)

    expect(await screen.findByText(description)).toBeInTheDocument()
    expect(screen.getByText('aria-42')).toBeInTheDocument()
    expect(screen.queryByText(/custom model active|no custom model/i)).not.toBeInTheDocument()
  })
})
