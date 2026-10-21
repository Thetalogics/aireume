import { createContext, useContext } from 'react'

export const BrandingContext = createContext({ branding: null, loading: false })

export function useBranding() {
  return useContext(BrandingContext)
}
