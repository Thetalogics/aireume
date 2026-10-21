import { useState, useEffect } from 'react'
import { getAnalyticsMetrics } from '../../lib/api'

let glossaryCache = null

export function useMetricGlossary() {
  const [metrics, setMetrics] = useState(glossaryCache || [])
  useEffect(() => {
    if (glossaryCache) return
    getAnalyticsMetrics()
      .then((data) => {
        glossaryCache = data.metrics || []
        setMetrics(glossaryCache)
      })
      .catch(() => setMetrics([]))
  }, [])
  return metrics
}
