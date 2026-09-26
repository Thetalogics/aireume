/**
 * Tests for the useSubscription hook and SubscriptionContext
 * @jest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { createContext, useContext } from 'react'
import { SubscriptionProvider, useSubscription, useUsageCheck } from '../useSubscription'
import * as api from '../../lib/api'

// Mock the API module
vi.mock('../../lib/api', () => ({
  getSubscription: vi.fn(),
  getAvailablePlans: vi.fn(),
  checkUsage: vi.fn(),
}))

// Create a mock Auth context to satisfy useAuth dependency
const MockAuthContext = createContext(null)

function MockAuthProvider({ children }) {
  const mockAuthValue = {
    user: { id: 1, email: 'test@example.com' },
    loading: false,
    tenant: { id: 1, name: 'Test Tenant' },
  }
  return (
    <MockAuthContext.Provider value={mockAuthValue}>
      {children}
    </MockAuthContext.Provider>
  )
}

// Mock the AuthContext module
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => useContext(MockAuthContext),
}))

// Mock subscription data
const mockSubscription = {
  current_plan: {
    plan: {
      id: 2,
      name: 'growth',
      display_name: 'Growth',
      description: 'Growth tier for teams',
      price_monthly: 4900,
      price_yearly: 47000,
      currency: 'USD',
      features: ['100 analyses', '5 team members', 'API access'],
      limits: {
        analyses_per_month: 100,
        batch_size: 20,
        team_members: 5,
        storage_gb: 10,
        api_access: true,
        custom_weights: true,
      }
    },
    status: 'active',
    billing_cycle: 'monthly',
    current_period_start: '2026-04-01T00:00:00Z',
    current_period_end: '2026-05-01T00:00:00Z',
    price: 4900,
  },
  enabled_features: ['api_access', 'custom_weights', 'requisitions', 'pipeline', 'compare', 'export_excel', 'email_generation'],
  usage: {
    analyses_used: 25,
    analyses_limit: 100,
    storage_used_mb: 512.5,
    storage_limit_gb: 10,
    team_members_count: 3,
    team_members_limit: 5,
    percent_used: 25.0,
  },
  available_plans: [
    {
      id: 1,
      name: 'free',
      display_name: 'Free',
      description: 'Free tier',
      price_monthly: 0,
      price_yearly: 0,
      currency: 'USD',
      features: ['5 analyses', '1 team member'],
      limits: { analyses_per_month: 5, batch_size: 3, team_members: 1 }
    },
    {
      id: 2,
      name: 'pro',
      display_name: 'Pro',
      description: 'Pro tier',
      price_monthly: 4900,
      price_yearly: 47000,
      currency: 'USD',
      features: ['100 analyses', '5 team members', 'API access'],
      limits: { analyses_per_month: 100, batch_size: 20, team_members: 5, api_access: true }
    },
  ],
  days_until_reset: 15,
}

const mockUsageAllowed = { allowed: true, current_usage: 25, limit: 100 }
const mockUsageDenied = { allowed: false, current_usage: 100, limit: 100, message: 'Limit exceeded' }

describe('useSubscription', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Default successful mocks
    api.getSubscription.mockResolvedValue(mockSubscription)
    api.getAvailablePlans.mockResolvedValue(mockSubscription.available_plans)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('initial state', () => {
    it('should start with null subscription', () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      expect(result.current.subscription).toBeNull()
      expect(result.current.loading).toBe(true)
    })

    it('should fetch subscription on mount', async () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(api.getSubscription).toHaveBeenCalled()
      expect(result.current.subscription).toEqual(mockSubscription)
    })
  })

  describe('getUsageStats', () => {
    it('should return null when subscription not loaded', () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      expect(result.current.getUsageStats()).toBeNull()
    })

    it('should return usage stats when subscription loaded', async () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      const stats = result.current.getUsageStats()
      expect(stats).toEqual({
        analysesUsed: 25,
        analysesLimit: 100,
        storageUsedMB: 512.5,
        storageLimitGB: 10,
        teamMembers: 3,
        teamMembersLimit: 5,
        percentUsed: 25.0,
        daysUntilReset: 15,
      })
    })
  })

  describe('getCurrentPlan', () => {
    it('should return null when subscription not loaded', () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      expect(result.current.getCurrentPlan()).toBeNull()
    })

    it('should return current plan when subscription loaded', async () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      const plan = result.current.getCurrentPlan()
      expect(plan.plan.name).toBe('growth')
      expect(plan.status).toBe('active')
      expect(plan.billing_cycle).toBe('monthly')
    })
  })

  describe('isFeatureAvailable', () => {
    it('should return false when subscription not loaded', () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      expect(result.current.isFeatureAvailable('api_access')).toBe(false)
    })

    it('should return true for available features', async () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      expect(result.current.isFeatureAvailable('api_access')).toBe(true)
      expect(result.current.isFeatureAvailable('custom_weights')).toBe(true)
    })

    it('should return false for unavailable features', async () => {
      // Mock free plan subscription
      const freeSubscription = {
        ...mockSubscription,
        enabled_features: ['requisitions', 'pipeline', 'compare', 'export_excel', 'email_generation'],
        current_plan: {
          ...mockSubscription.current_plan,
          plan: {
            ...mockSubscription.current_plan.plan,
            limits: { ...mockSubscription.current_plan.plan.limits, api_access: false }
          }
        }
      }
      api.getSubscription.mockResolvedValue(freeSubscription)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      expect(result.current.isFeatureAvailable('api_access')).toBe(false)
    })

    it('should handle unlimited analyses', async () => {
      const unlimitedSubscription = {
        ...mockSubscription,
        current_plan: {
          ...mockSubscription.current_plan,
          plan: {
            ...mockSubscription.current_plan.plan,
            limits: { ...mockSubscription.current_plan.plan.limits, analyses_per_month: -1 }
          }
        }
      }
      api.getSubscription.mockResolvedValue(unlimitedSubscription)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      expect(result.current.isFeatureAvailable('unlimited_analyses')).toBe(true)
    })
  })

  describe('getRemainingAnalyses', () => {
    it('should return 0 when subscription not loaded', () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      expect(result.current.getRemainingAnalyses()).toBe(0)
    })

    it('should return remaining count for limited plans', async () => {
      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      // 100 - 25 = 75
      expect(result.current.getRemainingAnalyses()).toBe(75)
    })

    it('should return Infinity for unlimited plans', async () => {
      const unlimitedSubscription = {
        ...mockSubscription,
        usage: { ...mockSubscription.usage, analyses_limit: -1 }
      }
      api.getSubscription.mockResolvedValue(unlimitedSubscription)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      expect(result.current.getRemainingAnalyses()).toBe(Infinity)
    })
  })

  describe('checkActionAllowed', () => {
    it('should call checkUsage API', async () => {
      api.checkUsage.mockResolvedValue(mockUsageAllowed)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      await act(async () => {
        await result.current.checkActionAllowed('resume_analysis', 1)
      })

      expect(api.checkUsage).toHaveBeenCalledWith('resume_analysis', 1)
    })

    it('should return allowed result', async () => {
      api.checkUsage.mockResolvedValue(mockUsageAllowed)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      let checkResult
      await act(async () => {
        checkResult = await result.current.checkActionAllowed('resume_analysis', 1)
      })

      expect(checkResult.allowed).toBe(true)
    })

    it('should handle API errors gracefully', async () => {
      api.checkUsage.mockRejectedValue(new Error('Network error'))

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      let checkResult
      await act(async () => {
        checkResult = await result.current.checkActionAllowed('resume_analysis', 1)
      })

      // Should fail closed
      expect(checkResult.allowed).toBe(false)
    })
  })

  describe('fetchSubscription', () => {
    it('should refresh subscription data', async () => {
      api.getSubscription.mockResolvedValueOnce(mockSubscription)
        .mockResolvedValueOnce({
          ...mockSubscription,
          usage: { ...mockSubscription.usage, analyses_used: 50 }
        })

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      expect(result.current.getUsageStats().analysesUsed).toBe(25)

      // Force refresh
      await act(async () => {
        await result.current.fetchSubscription(true)
      })

      expect(result.current.getUsageStats().analysesUsed).toBe(50)
    })

    it('should use cache for subsequent calls', async () => {
      api.getSubscription.mockResolvedValue(mockSubscription)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      // Second call should use cache
      await act(async () => {
        await result.current.fetchSubscription(false)
      })

      // API should only be called once (on mount)
      expect(api.getSubscription).toHaveBeenCalledTimes(1)
    })
  })

  describe('refreshAfterAnalysis', () => {
    it('should optimistically update usage', async () => {
      api.getSubscription.mockResolvedValue(mockSubscription)

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.subscription).not.toBeNull()
      })

      expect(result.current.getUsageStats().analysesUsed).toBe(25)

      // Optimistic update
      await act(async () => {
        await result.current.refreshAfterAnalysis(1)
      })

      // Should be 26 immediately (optimistic)
      expect(result.current.getUsageStats().analysesUsed).toBe(26)
    })
  })

  describe('error handling', () => {
    it('should handle subscription fetch error', async () => {
      api.getSubscription.mockRejectedValue(new Error('API Error'))

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.error).toBe('API Error')
      expect(result.current.subscription).toBeNull()
    })

    it('should handle network error gracefully', async () => {
      api.getSubscription.mockRejectedValue({ message: 'Network error' })

      const wrapper = ({ children }) => (
        <MockAuthProvider>
          <SubscriptionProvider>{children}</SubscriptionProvider>
        </MockAuthProvider>
      )
      const { result } = renderHook(() => useSubscription(), { wrapper })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.error).toBeDefined()
    })
  })
})


describe('useUsageCheck', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getSubscription.mockResolvedValue(mockSubscription)
    api.getAvailablePlans.mockResolvedValue(mockSubscription.available_plans)
    api.checkUsage.mockResolvedValue(mockUsageAllowed)
  })

  it('should provide checkBeforeAnalysis function', async () => {
    const wrapper = ({ children }) => (
      <MockAuthProvider>
        <SubscriptionProvider>{children}</SubscriptionProvider>
      </MockAuthProvider>
    )
    const { result } = renderHook(() => useUsageCheck(), { wrapper })

    await waitFor(() => {
      expect(result.current).toBeDefined()
    })

    expect(typeof result.current.checkBeforeAnalysis).toBe('function')
    expect(typeof result.current.getRemainingAnalyses).toBe('function')
  })

  it('should check usage before analysis with local check', async () => {
    // Mock checkUsage to return allowed
    api.checkUsage.mockResolvedValue(mockUsageAllowed)
    
    const wrapper = ({ children }) => (
      <MockAuthProvider>
        <SubscriptionProvider>{children}</SubscriptionProvider>
      </MockAuthProvider>
    )
    const { result: subscriptionResult } = renderHook(() => useSubscription(), { wrapper })
    const { result } = renderHook(() => useUsageCheck(), { wrapper })

    // Wait for subscription to load
    await waitFor(() => {
      expect(subscriptionResult.current.subscription).not.toBeNull()
    })

    await waitFor(() => {
      expect(result.current).toBeDefined()
    })

    let checkResult
    await act(async () => {
      checkResult = await result.current.checkBeforeAnalysis(1)
    })

    expect(checkResult.allowed).toBe(true)
    expect(checkResult.remaining).toBe(74) // 75 - 1
  })

  it('should deny when not enough remaining locally', async () => {
    const atLimitSubscription = {
      ...mockSubscription,
      usage: { ...mockSubscription.usage, analyses_used: 99 }
    }
    api.getSubscription.mockResolvedValue(atLimitSubscription)

    const wrapper = ({ children }) => (
      <MockAuthProvider>
        <SubscriptionProvider>{children}</SubscriptionProvider>
      </MockAuthProvider>
    )
    const { result } = renderHook(() => useUsageCheck(), { wrapper })

    await waitFor(() => {
      expect(result.current).toBeDefined()
      expect(result.current.getRemainingAnalyses()).toBe(1) // Wait for subscription to load
    })

    let checkResult
    await act(async () => {
      checkResult = await result.current.checkBeforeAnalysis(5) // Would need 5, only 1 remaining
    })

    expect(checkResult.allowed).toBe(false)
    expect(checkResult.remaining).toBe(-4)
    expect(checkResult.message).toContain('only have 1 analyses remaining')
  })

  it('should use the authoritative server check when the local check passes', async () => {
    api.checkUsage.mockResolvedValue({ allowed: true, current_usage: 25, limit: 100 })

    const wrapper = ({ children }) => (
      <MockAuthProvider>
        <SubscriptionProvider>{children}</SubscriptionProvider>
      </MockAuthProvider>
    )
    const { result } = renderHook(() => useUsageCheck(), { wrapper })

    await waitFor(() => {
      expect(result.current.getRemainingAnalyses()).toBe(75)
    })

    let checkResult
    await act(async () => {
      checkResult = await result.current.checkBeforeAnalysis(1)
    })

    expect(api.checkUsage).toHaveBeenCalledWith('resume_analysis', 1)
    expect(checkResult.allowed).toBe(true)
  })
})
