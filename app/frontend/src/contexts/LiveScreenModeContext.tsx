import { LiveScreenModeContext } from './useLiveScreenMode'
import { useMemo, useState } from 'react'

export function LiveScreenModeProvider({ children }) {
  const [active, setActive] = useState(false)
  const value = useMemo(() => ({ active, setActive }), [active])
  return (
    <LiveScreenModeContext.Provider value={value}>
      {children}
    </LiveScreenModeContext.Provider>
  )
}
