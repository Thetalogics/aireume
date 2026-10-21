import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { renderHook } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import NotificationBell from '../components/NotificationBell'
import { NotificationProvider } from '../contexts/NotificationContext'
import { useNotification } from '../contexts/useNotification'

function Harness() {
  const { addNotification } = useNotification()
  return (
    <div>
      <button onClick={() => addNotification({ type: 'success', title: 'Batch complete', message: 'All done', href: '/candidates' })}>
        seed
      </button>
      <NotificationBell />
    </div>
  )
}

const renderBell = () =>
  render(
    <MemoryRouter>
      <NotificationProvider>
        <Harness />
      </NotificationProvider>
    </MemoryRouter>
  )

describe('NotificationBell', () => {
  it('renders with an accessible label and no badge when empty', () => {
    renderBell()
    expect(screen.getByRole('button', { name: /notifications/i })).toBeInTheDocument()
  })

  it('shows an unread badge after a notification is added', () => {
    renderBell()
    fireEvent.click(screen.getByRole('button', { name: /seed/i }))
    expect(screen.getByRole('button', { name: /1 unread/i })).toBeInTheDocument()
  })

  it('opens the dropdown and displays the notification', () => {
    renderBell()
    fireEvent.click(screen.getByRole('button', { name: /seed/i }))
    fireEvent.click(screen.getByRole('button', { name: /1 unread/i }))
    expect(screen.getByText('Batch complete')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /mark all read/i })).toBeInTheDocument()
  })

  it('shows caught-up empty message when opened with no notifications', () => {
    renderBell()
    fireEvent.click(screen.getByRole('button', { name: /^notifications$/i }))
    expect(screen.getByText(/all caught up/i)).toBeInTheDocument()
  })
})

describe('NotificationContext logic', () => {
  const wrapper = ({ children }) => <NotificationProvider>{children}</NotificationProvider>

  it('tracks unread count and marks read', () => {
    const { result } = renderHook(() => useNotification(), { wrapper })

    act(() => { result.current.addNotification({ title: 'A' }) })
    act(() => { result.current.addNotification({ title: 'B' }) })
    expect(result.current.unreadCount).toBe(2)

    const firstId = result.current.notifications[0].id
    act(() => { result.current.markNotificationRead(firstId) })
    expect(result.current.unreadCount).toBe(1)

    act(() => { result.current.markAllNotificationsRead() })
    expect(result.current.unreadCount).toBe(0)
  })

  it('removes and clears notifications', () => {
    const { result } = renderHook(() => useNotification(), { wrapper })
    act(() => { result.current.addNotification({ title: 'X' }) })
    const id = result.current.notifications[0].id
    act(() => { result.current.removeNotification(id) })
    expect(result.current.notifications).toHaveLength(0)

    act(() => { result.current.addNotification({ title: 'Y' }) })
    act(() => { result.current.clearNotifications() })
    expect(result.current.notifications).toHaveLength(0)
  })
})
