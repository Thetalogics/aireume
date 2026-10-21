import { useState, useEffect } from 'react'
import { FEATURE_GUIDES } from '../lib/featureGuides'
import { useUserPreferences } from '../contexts/useUserPreferences'
import { useSubscription } from '../hooks/useSubscription'
import { trackOnboardingEvent } from '../lib/onboardingAnalytics'

/**
 * Show a first-visit guide modal once per feature (persisted in user preferences).
 * @param {keyof typeof FEATURE_GUIDES} guideKey
 */
export function useFeatureGuide(guideKey) {
  const guide = FEATURE_GUIDES[guideKey]
  const { hasSeenModal, markModalSeen, loading } = useUserPreferences()
  const { isFeatureAvailable } = useSubscription()
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (loading || !guide) return
    if (guide.feature && !isFeatureAvailable(guide.feature)) return
    if (hasSeenModal(guide.id)) return
    const timer = setTimeout(() => setOpen(true), 400)
    return () => clearTimeout(timer)
  }, [loading, guide, guideKey, hasSeenModal, isFeatureAvailable])

  const dismiss = async () => {
    setOpen(false)
    await markModalSeen(guide.id)
    trackOnboardingEvent('feature_guide_seen', { modal_id: guide.id })
  }

  return { open, guide, dismiss, setOpen }
}

export default useFeatureGuide
