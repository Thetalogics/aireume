import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { useSearchParams, useOutletContext } from 'react-router-dom'
import { getSkillTrends, computeSkillTrends } from '../lib/api'
import AnalyticsHub from '../components/patterns/AnalyticsHub'
import { VALID_SLICE_IDS } from '../components/patterns/analyticsConstants'
import usePermissions from '../hooks/usePermissions'
import { createAnalyticsView } from '../lib/api'
import { Button } from '../components/ui'
import { ANALYTICS } from '../lib/uxLabels'

function parseSlice(raw) {
  if (!raw || raw === 'reports') return 'screening'
  if (!VALID_SLICE_IDS.has(raw) || raw === 'reports') return 'screening'
  return raw
}

export default function AnalyticsExplorePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const shellContext = useOutletContext() || {}
  const { registerRefresh, setGeneratedAt } = shellContext
  const permissions = usePermissions()
  const hubRef = useRef(null)

  const hubSlice = parseSlice(searchParams.get('slice'))
  const [activeSlice, setActiveSlice] = useState(hubSlice)
  const period = searchParams.get('period') || 'last_30_days'
  const startDate = searchParams.get('start_date') || ''
  const endDate = searchParams.get('end_date') || ''
  const compare = searchParams.get('compare') === '1'
  const requisitionId = searchParams.get('requisition_id') ? Number(searchParams.get('requisition_id')) : null
  const recruiterId = searchParams.get('recruiter_id') ? Number(searchParams.get('recruiter_id')) : null
  const useCustomRange = Boolean(startDate && endDate)

  const [trendData, setTrendData] = useState(null)
  const [trendLoading, setTrendLoading] = useState(false)
  const [trendRoleCategory, setTrendRoleCategory] = useState(null)
  const [computing, setComputing] = useState(false)
  const [savingView, setSavingView] = useState(false)

  useEffect(() => {
    setActiveSlice(parseSlice(searchParams.get('slice')))
  }, [searchParams])

  const syncUrl = useCallback((patch) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev)
      Object.entries(patch).forEach(([key, value]) => {
        if (value === null || value === undefined || value === '' || (key === 'slice' && value === 'screening')) {
          params.delete(key)
        } else if (key === 'compare') {
          if (value) params.set('compare', '1')
          else params.delete('compare')
        } else {
          params.set(key, String(value))
        }
      })
      return params
    }, { replace: true })
  }, [setSearchParams])

  const handleSliceChange = (nextSlice) => {
    if (nextSlice === 'reports') return
    setActiveSlice(nextSlice)
    syncUrl({ slice: nextSlice })
  }

  const handleFilterChange = ({ requisitionId: reqId, recruiterId: recId }) => {
    syncUrl({ requisition_id: reqId, recruiter_id: recId })
  }

  const fetchTrends = useCallback(async () => {
    setTrendLoading(true)
    try {
      const params = { months: 6 }
      if (trendRoleCategory) params.role_category = trendRoleCategory
      const result = await getSkillTrends(params)
      setTrendData(result)
    } catch {
      setTrendData(null)
    } finally {
      setTrendLoading(false)
    }
  }, [trendRoleCategory])

  useEffect(() => {
    if (activeSlice === 'screening') fetchTrends()
  }, [activeSlice, fetchTrends])

  const handleComputeSnapshots = async () => {
    if (!permissions.isAdmin) return
    setComputing(true)
    try {
      await computeSkillTrends()
      await fetchTrends()
    } finally {
      setComputing(false)
    }
  }

  const handleRefreshAll = useCallback(async () => {
    await Promise.all([
      hubRef.current?.reload?.(),
      activeSlice === 'screening' ? fetchTrends() : Promise.resolve(),
    ])
  }, [activeSlice, fetchTrends])

  useEffect(() => {
    registerRefresh?.(handleRefreshAll)
  }, [handleRefreshAll, registerRefresh])

  const saveView = async () => {
    setSavingView(true)
    try {
      await createAnalyticsView({
        name: `Explore: ${activeSlice}`,
        view_type: 'explore',
        slice: activeSlice,
        filters: {
          period,
          start_date: useCustomRange ? startDate : null,
          end_date: useCustomRange ? endDate : null,
          compare,
          requisition_id: requisitionId,
          recruiter_id: recruiterId,
        },
      })
    } finally {
      setSavingView(false)
    }
  }

  const hubQuery = useMemo(() => ({
    period,
    start_date: useCustomRange ? startDate : undefined,
    end_date: useCustomRange ? endDate : undefined,
    requisition_id: requisitionId,
    recruiter_id: recruiterId,
    compare,
  }), [period, startDate, endDate, useCustomRange, requisitionId, recruiterId, compare])

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="secondary" size="sm" disabled={savingView} onClick={saveView}>
          {ANALYTICS.pinViewLabel}
        </Button>
      </div>
      <AnalyticsHub
        ref={hubRef}
        period={hubQuery.period}
        startDate={useCustomRange ? startDate : null}
        endDate={useCustomRange ? endDate : null}
        compare={compare}
        requisitionId={requisitionId}
        recruiterId={recruiterId}
        initialSlice={hubSlice}
        activeSlice={activeSlice}
        onSliceChange={handleSliceChange}
        onFilterChange={handleFilterChange}
        onGeneratedAt={setGeneratedAt}
        permissions={permissions}
        trendData={trendData}
        trendLoading={trendLoading}
        trendRoleCategory={trendRoleCategory}
        onTrendRoleCategoryChange={setTrendRoleCategory}
        onComputeSnapshots={handleComputeSnapshots}
        computing={computing}
        hideReportsTab
      />
    </div>
  )
}
