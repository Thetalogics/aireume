import { BrandingContext } from './useBranding'
import { useEffect, useState, useCallback } from 'react'
import { useAuth } from './useAuth'
import { getTenantBranding } from '../lib/api'

async function resolveHostBranding() {
  const host = window.location.hostname
  if (!host || host === 'localhost' || host === '127.0.0.1') return null
  try {
    const res = await fetch(`/api/branding/resolve?host=${encodeURIComponent(host)}`)
    if (!res.ok) return null
    const data = await res.json()
    return data.branding || null
  } catch {
    return null
  }
}

function applyBranding(branding) {
  if (!branding) {
    document.title = 'ARIA'
    return
  }
  const name = branding.brand_name || 'ARIA'
  document.title = name
  if (branding.brand_favicon_url) {
    let link = document.querySelector("link[rel='icon']")
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = branding.brand_favicon_url
  }
  if (branding.brand_primary_color) {
    document.documentElement.style.setProperty('--aria-brand-primary', branding.brand_primary_color)
  } else {
    document.documentElement.style.removeProperty('--aria-brand-primary')
  }
}

export function BrandingProvider({ children }) {
  const { user } = useAuth()
  const [branding, setBranding] = useState(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      if (user) {
        const data = await getTenantBranding()
        const b = data.branding || null
        setBranding(b)
        applyBranding(b)
      } else {
        const hostBrand = await resolveHostBranding()
        setBranding(hostBrand)
        applyBranding(hostBrand)
      }
    } catch {
      setBranding(null)
      applyBranding(null)
    } finally {
      setLoading(false)
    }
  }, [user])

  useEffect(() => {
    refresh()
  }, [refresh])

  return (
    <BrandingContext.Provider value={{ branding, loading, refresh }}>
      {children}
    </BrandingContext.Provider>
  )
}
