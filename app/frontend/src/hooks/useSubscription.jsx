import { createContext, useContext, useCallback } from 'react'

export const SubscriptionContext = createContext(null)

export function useSubscription() {
  const context = useContext(SubscriptionContext)
  if (!context) {
    throw new Error('useSubscription must be used within SubscriptionProvider')
  }
  return context
}

/** Safe plan feature check for components that may render outside SubscriptionProvider (e.g. unit tests). */
export function usePlanFeature(feature, defaultValue = false) {
  const context = useContext(SubscriptionContext)
  if (!context) return defaultValue
  return context.isFeatureAvailable(feature)
}

export function useHasSubscriptionContext() {
  return useContext(SubscriptionContext) !== null
}

// Hook for checking limits before operations
export function useUsageCheck() {
  const { checkActionAllowed, getRemainingAnalyses, subscription } = useSubscription()

  const checkBeforeAnalysis = useCallback(async (fileCount = 1) => {
    const remaining = getRemainingAnalyses()
    
    // Quick local check first
    if (remaining !== Infinity && remaining < fileCount) {
      return {
        allowed: false,
        message: `You only have ${remaining} analyses remaining this month. Please upgrade your plan.`,
        remaining: remaining - fileCount, // Return deficit (negative number)
      }
    }

    // Server check for accurate limits
    const result = await checkActionAllowed(fileCount > 1 ? 'batch_analysis' : 'resume_analysis', fileCount)
    return {
      ...result,
      remaining: remaining - fileCount,
    }
  }, [checkActionAllowed, getRemainingAnalyses])

  return { checkBeforeAnalysis, getRemainingAnalyses }
}
