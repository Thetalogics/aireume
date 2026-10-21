import { NotificationContext } from './useNotification'
import { useState, useCallback, useRef } from 'react'

export function NotificationProvider({ children }) {
  const [analysisProgress, setAnalysisProgress] = useState({
    isActive: false,
    completed: 0,
    total: 0,
    items: [],
  })
  const [enrichmentJobs, setEnrichmentJobs] = useState([])
  const [queueJobs, setQueueJobs] = useState([])
  const [notifications, setNotifications] = useState([])
  const completeTimeoutRef = useRef(null)

  const startBatchAnalysis = useCallback((total) => {
    if (completeTimeoutRef.current) {
      clearTimeout(completeTimeoutRef.current)
      completeTimeoutRef.current = null
    }
    setAnalysisProgress({
      isActive: true,
      completed: 0,
      total,
      items: [],
    })
  }, [])

  const updateProgress = useCallback((filename, status) => {
    setAnalysisProgress((prev) => {
      const existingIndex = prev.items.findIndex((item) => item.filename === filename)
      let newItems
      if (existingIndex >= 0) {
        newItems = prev.items.map((item, idx) =>
          idx === existingIndex ? { ...item, status } : item
        )
      } else {
        newItems = [...prev.items, { filename, status }]
      }

      const completedCount = newItems.filter(
        (item) => item.status === 'completed' || item.status === 'error'
      ).length

      return {
        ...prev,
        completed: completedCount,
        items: newItems,
      }
    })
  }, [])

  const trackEnrichmentJob = useCallback((job) => {
    const id = job.id || `enrich-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    setEnrichmentJobs((prev) => {
      const exists = prev.findIndex((j) => j.id === id)
      const entry = { ...job, id, updatedAt: new Date().toISOString() }
      if (exists >= 0) {
        const next = [...prev]
        next[exists] = { ...next[exists], ...entry }
        return next
      }
      return [entry, ...prev].slice(0, 30)
    })
    return id
  }, [])

  const updateEnrichmentJob = useCallback((id, patch) => {
    setEnrichmentJobs((prev) =>
      prev.map((j) => (j.id === id ? { ...j, ...patch, updatedAt: new Date().toISOString() } : j))
    )
  }, [])

  const completeEnrichmentJob = useCallback((id, patch = {}) => {
    setEnrichmentJobs((prev) =>
      prev.map((j) =>
        j.id === id
          ? { ...j, ...patch, status: patch.status || 'ready', updatedAt: new Date().toISOString() }
          : j
      )
    )
  }, [])

  const removeEnrichmentJob = useCallback((id) => {
    setEnrichmentJobs((prev) => prev.filter((j) => j.id !== id))
  }, [])

  const addNotification = useCallback((notification) => {
    const id = notification.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setNotifications((prev) => [
      {
        id,
        type: notification.type || 'info',
        title: notification.title || '',
        message: notification.message || '',
        href: notification.href || null,
        read: false,
        createdAt: new Date().toISOString(),
      },
      ...prev,
    ].slice(0, 50))
    return id
  }, [])

  const trackQueueBatch = useCallback((batch) => {
    const entries = (batch.jobs || []).map((j) => ({
      id: j.job_id,
      batchId: batch.batch_id,
      filename: j.filename,
      status: j.status || 'queued',
      progress: 0,
      updatedAt: new Date().toISOString(),
    }))
    setQueueJobs((prev) => [...entries, ...prev].slice(0, 100))
    addNotification({
      type: 'info',
      title: 'Background analysis started',
      message: `${entries.length} resume${entries.length !== 1 ? 's' : ''} queued for processing.`,
      href: '/analyze',
    })
  }, [addNotification])

  const updateQueueJob = useCallback((jobId, patch) => {
    setQueueJobs((prev) =>
      prev.map((j) =>
        j.id === jobId ? { ...j, ...patch, updatedAt: new Date().toISOString() } : j
      )
    )
  }, [])

  const removeQueueJob = useCallback((jobId) => {
    setQueueJobs((prev) => prev.filter((j) => j.id !== jobId))
  }, [])

  const completeQueueBatch = useCallback((batchId) => {
    setQueueJobs((prev) => {
      const batchJobs = prev.filter((j) => j.batchId === batchId)
      const failed = batchJobs.filter((j) => j.status === 'failed').length
      const succeeded = batchJobs.length - failed
      if (batchJobs.length > 0) {
        addNotification({
          type: failed > 0 ? 'warning' : 'success',
          title: 'Background batch complete',
          message:
            failed > 0
              ? `${succeeded} of ${batchJobs.length} resumes processed (${failed} failed).`
              : `All ${batchJobs.length} background analyses finished.`,
          href: '/candidates',
        })
      }
      return prev.filter((j) => j.batchId !== batchId)
    })
  }, [addNotification])

  const markNotificationRead = useCallback((id) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    )
  }, [])

  const markAllNotificationsRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })))
  }, [])

  const removeNotification = useCallback((id) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id))
  }, [])

  const clearNotifications = useCallback(() => setNotifications([]), [])

  const resetProgress = useCallback(() => {
    if (completeTimeoutRef.current) {
      clearTimeout(completeTimeoutRef.current)
      completeTimeoutRef.current = null
    }
    setAnalysisProgress({ isActive: false, completed: 0, total: 0, items: [] })
  }, [])

  const completeBatchAnalysis = useCallback(() => {
    setAnalysisProgress((prev) => {
      const errors = prev.items.filter((i) => i.status === 'error').length
      const succeeded = prev.total - errors
      addNotification({
        type: errors > 0 ? 'warning' : 'success',
        title: 'Batch analysis complete',
        message:
          errors > 0
            ? `${succeeded} of ${prev.total} resumes analyzed (${errors} failed).`
            : `All ${prev.total} resumes analyzed successfully.`,
        href: '/candidates',
      })
      return { ...prev, isActive: false, completed: prev.total }
    })
    if (completeTimeoutRef.current) {
      clearTimeout(completeTimeoutRef.current)
    }
    completeTimeoutRef.current = setTimeout(resetProgress, 3000)
  }, [addNotification, resetProgress])

  const unreadCount = notifications.filter((n) => !n.read).length

  const value = {
    analysisProgress,
    enrichmentJobs,
    queueJobs,
    notifications,
    unreadCount,
    startBatchAnalysis,
    updateProgress,
    completeBatchAnalysis,
    resetProgress,
    trackEnrichmentJob,
    updateEnrichmentJob,
    completeEnrichmentJob,
    removeEnrichmentJob,
    trackQueueBatch,
    updateQueueJob,
    removeQueueJob,
    completeQueueBatch,
    addNotification,
    markNotificationRead,
    markAllNotificationsRead,
    removeNotification,
    clearNotifications,
  }

  return (
    <NotificationContext.Provider value={value}>
      {children}
    </NotificationContext.Provider>
  )
}
