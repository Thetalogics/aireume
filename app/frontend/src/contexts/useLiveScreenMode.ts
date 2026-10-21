import { createContext, useContext } from 'react'

export const LiveScreenModeContext = createContext({
  active: false,
  setActive: () => {},
})

export function useLiveScreenMode() {
  return useContext(LiveScreenModeContext)
}
