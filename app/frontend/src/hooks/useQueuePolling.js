import { useEffect, useRef } from 'react'
import { getJobStatus } from '../lib/api'
import { useNotification } from '../contexts/useNotification'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

/**
 * Polls active queue jobs and updates Activity Center state.
 * Mount once near the app shell (e.g. JobCenter).
 */
export function useQueuePolling() {
  const {
    queueJobs,
    updateQueueJob,
    completeQueueBatch,
    trackEnrichmentJob,
  } = useNotification()
  const jobsRef = useRef(queueJobs)
  jobsRef.current = queueJobs

  useEffect(() => {
    const pending = queueJobs.filter((j) => !TERMINAL.has(j.status))
    if (pending.length === 0) return undefined

    let cancelled = false

    const poll = async () => {
      if (cancelled) return
      const snapshot = jobsRef.current.filter((j) => !TERMINAL.has(j.status))

      await Promise.all(
        snapshot.map(async (job) => {
          try {
            const status = await getJobStatus(job.id)
            updateQueueJob(job.id, {
              status: status.status,
              progress: status.progress_percent ?? 0,
              screeningResultId: status.screening_result_id,
            })
            if (status.status === 'completed' && status.screening_result_id) {
              trackEnrichmentJob({
                id: `enrich-${status.screening_result_id}`,
                label: job.filename || 'Resume',
                status: 'processing',
                phase: 'AI enrichment',
                href: `/report?id=${status.screening_result_id}`,
              })
            }
          } catch {
            /* ignore transient poll errors */
          }
        })
      )

      const batchIds = [...new Set(jobsRef.current.map((j) => j.batchId).filter(Boolean))]
      batchIds.forEach((batchId) => {
        const batchJobs = jobsRef.current.filter((j) => j.batchId === batchId)
        if (batchJobs.length > 0 && batchJobs.every((j) => TERMINAL.has(j.status))) {
          completeQueueBatch(batchId)
        }
      })
    }

    poll()
    const id = setInterval(poll, 3000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [queueJobs, updateQueueJob, completeQueueBatch, trackEnrichmentJob])
}
