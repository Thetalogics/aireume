import { createContext, useContext } from 'react'

export const UserPreferencesContext = createContext(null)

export function useUserPreferences() {
  const ctx = useContext(UserPreferencesContext)
  if (!ctx) throw new Error('useUserPreferences must be used within UserPreferencesProvider')
  return ctx
}
