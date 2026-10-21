import { UserPreferencesContext } from './useUserPreferences'
import { useState, useCallback, useEffect } from 'react'
import {
  getUserPreferences,
  patchUserPreferences,
  markModalSeen as markModalSeenApi,
} from '../lib/api'
import { useAuth } from './useAuth'

const DEFAULT_PREFERENCES = {
  notifications: {
    emailOnComplete: true,
    emailOnBatchComplete: true,
    marketing: false,
  },
  seen_modals: {},
  invited_welcome_dismissed: false,
}

export function UserPreferencesProvider({ children }) {
  const { user, loading: authLoading } = useAuth()
  const userId = user?.id
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (authLoading) return
    setPreferences(DEFAULT_PREFERENCES)
    if (!userId) {
      setPreferences(DEFAULT_PREFERENCES)
      setLoading(false)
      return
    }
    setLoading(true)
    let cancelled = false
    ;(async () => {
      try {
        const data = await getUserPreferences()
        if (!cancelled && data?.preferences) {
          setPreferences({ ...DEFAULT_PREFERENCES, ...data.preferences, notifications: {
            ...DEFAULT_PREFERENCES.notifications,
            ...(data.preferences.notifications || {}),
          } })
        }
      } catch {
        // keep defaults
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [authLoading, userId])

  const updatePreferences = useCallback(async (patch) => {
    const data = await patchUserPreferences(patch)
    if (data?.preferences) setPreferences(data.preferences)
    return data?.preferences
  }, [])

  const markModalSeen = useCallback(async (modalId) => {
    setPreferences((prev) => ({
      ...prev,
      seen_modals: { ...prev.seen_modals, [modalId]: true },
    }))
    try {
      const data = await markModalSeenApi(modalId)
      if (data?.preferences) setPreferences(data.preferences)
    } catch {
      // optimistic update kept
    }
  }, [])

  const hasSeenModal = useCallback(
    (modalId) => Boolean(preferences.seen_modals?.[modalId]),
    [preferences.seen_modals],
  )

  return (
    <UserPreferencesContext.Provider
      value={{
        preferences,
        loading,
        updatePreferences,
        markModalSeen,
        hasSeenModal,
      }}
    >
      {children}
    </UserPreferencesContext.Provider>
  )
}
