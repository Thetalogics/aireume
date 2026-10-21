import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { NotificationProvider } from '../contexts/NotificationContext'
import { useNotification } from '../contexts/useNotification'
import { UserPreferencesProvider } from '../contexts/UserPreferencesContext'
import { useUserPreferences } from '../contexts/useUserPreferences'
import { usePermissions } from '../hooks/usePermissions'
import { getUserPreferences } from '../lib/api'

const auth = vi.hoisted(() => ({ user: { id: 1, role: 'viewer' }, loading: false }))
vi.mock('../contexts/useAuth', () => ({ useAuth: () => auth }))
vi.mock('../lib/api', () => ({ getUserPreferences: vi.fn(), patchUserPreferences: vi.fn(), markModalSeen: vi.fn() }))

beforeEach(() => {
  auth.user = { id: 1, role: 'viewer' }
  auth.loading = false
  vi.clearAllMocks()
})
afterEach(() => vi.useRealTimers())

it('replaces an earlier completion timer and keeps a newly started batch active', () => {
  vi.useFakeTimers()
  const { result } = renderHook(useNotification, { wrapper: NotificationProvider })
  act(() => result.current.startBatchAnalysis(2))
  act(() => result.current.updateProgress('one.pdf', 'completed'))
  act(() => result.current.updateProgress('two.pdf', 'error'))
  act(() => result.current.completeBatchAnalysis())
  expect(result.current.analysisProgress.completed).toBe(2)
  expect(result.current.notifications[0].message).toBe('1 of 2 resumes analyzed (1 failed).')
  act(() => vi.advanceTimersByTime(1000))
  act(() => result.current.completeBatchAnalysis())
  act(() => result.current.startBatchAnalysis(3))
  act(() => vi.advanceTimersByTime(3000))
  expect(result.current.analysisProgress).toMatchObject({ isActive: true, total: 3, completed: 0 })
  act(() => result.current.completeBatchAnalysis())
  act(() => vi.advanceTimersByTime(3000))
  expect(result.current.analysisProgress).toMatchObject({ isActive: false, total: 0 })
})

it('resets preferences and exposes loading while a different user is refreshed', async () => {
  getUserPreferences.mockResolvedValueOnce({ preferences: { seen_modals: { first: true } } })
  let resolveNext
  getUserPreferences.mockImplementationOnce(() => new Promise(resolve => { resolveNext = resolve }))
  const { result, rerender } = renderHook(useUserPreferences, { wrapper: UserPreferencesProvider })
  await waitFor(() => expect(result.current.hasSeenModal('first')).toBe(true))
  auth.user = { id: 2, role: 'viewer' }
  rerender()
  expect(result.current.loading).toBe(true)
  expect(result.current.hasSeenModal('first')).toBe(false)
  await act(async () => resolveNext({ preferences: { seen_modals: { second: true } } }))
  expect(result.current.hasSeenModal('second')).toBe(true)
  expect(getUserPreferences).toHaveBeenCalledTimes(2)
  auth.user = { ...auth.user, name: 'Updated name' }
  rerender()
  expect(getUserPreferences).toHaveBeenCalledTimes(2)
  auth.user = null
  rerender()
  expect(result.current.hasSeenModal('second')).toBe(false)
})

it('derives permissions when the authenticated user role changes or signs out', () => {
  const { result, rerender } = renderHook(usePermissions)
  expect(result.current.canWrite).toBe(false)
  auth.user = { id: 1, role: 'admin' }
  rerender()
  expect(result.current.isAdmin).toBe(true)
  auth.user = null
  rerender()
  expect(result.current.canWrite).toBe(false)
})
