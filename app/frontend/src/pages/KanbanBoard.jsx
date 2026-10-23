import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Columns, User, Briefcase, ChevronDown, ChevronRight, Inbox } from 'lucide-react'
import EmptyState from '../components/EmptyState'
import { getCandidatePipeline, updateResultStatus } from '../lib/api'
import { getScoreColor, PIPELINE_STAGES } from '../lib/constants'
import { personLabel } from '../lib/personName'
import usePermissions from '../hooks/usePermissions'
import useFeatureGuide from '../hooks/useFeatureGuide'
import FeatureGuideModal from '../components/onboarding/FeatureGuideModal'
import { ViewerReadOnlyBanner } from '../components/RequireWriteAccess'

/** Coerce any value to a render-safe string. */
function safeStr(v) {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try { return JSON.stringify(v) } catch { return String(v) }
}

const COLUMN_CONFIG = {
  pending:     {
    label: 'Pending',
    headerBg: 'bg-amber-50',
    headerText: 'text-amber-800',
    headerBorder: 'border-amber-200',
    badgeBg: 'bg-amber-100',
    badgeText: 'text-amber-700',
    ring: 'ring-amber-200',
    dropHighlight: 'bg-amber-50/60 border-amber-300',
  },
  'in-review': {
    label: 'In Review',
    headerBg: 'bg-blue-50',
    headerText: 'text-blue-800',
    headerBorder: 'border-blue-200',
    badgeBg: 'bg-blue-100',
    badgeText: 'text-blue-700',
    ring: 'ring-blue-200',
    dropHighlight: 'bg-blue-50/60 border-blue-300',
  },
  shortlisted: {
    label: 'Shortlisted',
    headerBg: 'bg-green-50',
    headerText: 'text-green-800',
    headerBorder: 'border-green-200',
    badgeBg: 'bg-green-100',
    badgeText: 'text-green-700',
    ring: 'ring-green-200',
    dropHighlight: 'bg-green-50/60 border-green-300',
  },
  rejected:    {
    label: 'Rejected',
    headerBg: 'bg-red-50',
    headerText: 'text-red-800',
    headerBorder: 'border-red-200',
    badgeBg: 'bg-red-100',
    badgeText: 'text-red-700',
    ring: 'ring-red-200',
    dropHighlight: 'bg-red-50/60 border-red-300',
  },
  hired:       {
    label: 'Hired',
    headerBg: 'bg-indigo-50',
    headerText: 'text-indigo-800',
    headerBorder: 'border-indigo-200',
    badgeBg: 'bg-indigo-100',
    badgeText: 'text-indigo-700',
    ring: 'ring-indigo-200',
    dropHighlight: 'bg-indigo-50/60 border-indigo-300',
  },
}

/** Sort candidates within a column by best_score descending */
function sortColumnsByScore(cols) {
  const sorted = {}
  for (const status of Object.keys(cols)) {
    sorted[status] = [...(cols[status] || [])].sort((a, b) => {
      const sa = a.best_score ?? a.fit_score ?? a.score ?? -1
      const sb = b.best_score ?? b.fit_score ?? b.score ?? -1
      return sb - sa
    })
  }
  return sorted
}

function ScoreBadge({ score }) {
  if (score == null) return <span className="text-slate-400 text-xs font-medium">—</span>
  const color = getScoreColor(score)
  return <span className={`px-2 py-0.5 rounded-full text-xs font-bold ring-1 ${color.text} ${color.bg} ${color.ring}`}>{score}</span>
}

function Toast({ message, type = 'error', onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000)
    return () => clearTimeout(t)
  }, [onDone])

  const bg = type === 'error' ? 'bg-red-600' : 'bg-brand-900'
  return (
    <div className={`fixed bottom-6 right-6 z-50 ${bg} text-white px-5 py-3 rounded-2xl shadow-brand-lg text-sm font-semibold card-animate`}>
      {message}
    </div>
  )
}

function ConfirmDialog({ candidateName, targetLabel, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white dark:bg-slate-800 rounded-2xl shadow-brand-lg ring-1 ring-brand-100 dark:ring-slate-700 p-6 max-w-sm w-full mx-4 card-animate">
        <h3 className="text-lg font-bold text-brand-900 mb-2">Confirm Move</h3>
        <p className="text-sm text-slate-600 mb-5">
          Move <span className="font-semibold text-brand-900">{candidateName || 'this candidate'}</span> to{' '}
          <span className="font-semibold">{targetLabel}</span>?
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm font-semibold text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm font-bold text-white bg-brand-600 hover:bg-brand-700 rounded-xl transition-colors"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}

export default function KanbanBoard() {
  const navigate = useNavigate()
  const { canWrite } = usePermissions()
  const pipelineGuide = useFeatureGuide('pipeline')
  const [columns, setColumns] = useState({})
  const [counts, setCounts] = useState({})
  const [loading, setLoading] = useState(true)
  const [dragOverColumn, setDragOverColumn] = useState(null)
  const [draggingId, setDraggingId] = useState(null)
  const [toast, setToast] = useState(null)
  const [collapsedCols, setCollapsedCols] = useState(
    () => JSON.parse(localStorage.getItem('kanban-collapsed') || '{}')
  )
  const [confirmDialog, setConfirmDialog] = useState(null)

  const toggleCollapse = (status) => {
    setCollapsedCols(prev => {
      const next = { ...prev, [status]: !prev[status] }
      localStorage.setItem('kanban-collapsed', JSON.stringify(next))
      return next
    })
  }

  const fetchPipeline = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getCandidatePipeline()
      setColumns(sortColumnsByScore(data.columns || {}))
      setCounts(data.counts || {})
    } catch {
      setToast({ message: 'Failed to load pipeline', type: 'error' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPipeline()
  }, [fetchPipeline])

  const handleDragStart = (e, candidate, sourceStatus) => {
    const payload = JSON.stringify({
      id: candidate.id,
      latest_result_id: candidate.latest_result_id,
      sourceStatus,
    })
    e.dataTransfer.setData('application/json', payload)
    e.dataTransfer.effectAllowed = 'move'
    setDraggingId(candidate.id)
  }

  const handleDragEnd = () => {
    setDraggingId(null)
    setDragOverColumn(null)
  }

  const handleDragOver = (e, status) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOverColumn(status)
  }

  const handleDragLeave = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX
    const y = e.clientY
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
      setDragOverColumn(null)
    }
  }

  const executeDrop = async (payload, targetStatus) => {
    if (!canWrite) return
    const { id, latest_result_id, sourceStatus } = payload
    if (!id || !latest_result_id || sourceStatus === targetStatus) return

    const candidate = columns[sourceStatus]?.find(c => c.id === id)
    if (!candidate) return

    const previousColumns = { ...columns }
    const previousCounts = { ...counts }

    setColumns(prev => {
      const next = { ...prev }
      next[sourceStatus] = next[sourceStatus].filter(c => c.id !== id)
      next[targetStatus] = [...(next[targetStatus] || []), { ...candidate }]
      return sortColumnsByScore(next)
    })
    setCounts(prev => ({
      ...prev,
      [sourceStatus]: Math.max(0, (prev[sourceStatus] || 0) - 1),
      [targetStatus]: (prev[targetStatus] || 0) + 1,
    }))

    try {
      await updateResultStatus(latest_result_id, targetStatus)
    } catch {
      setColumns(previousColumns)
      setCounts(previousCounts)
      setToast({ message: 'Failed to update status. Please try again.', type: 'error' })
    }
  }

  const handleDrop = async (e, targetStatus) => {
    e.preventDefault()
    setDragOverColumn(null)
    setDraggingId(null)

    const raw = e.dataTransfer.getData('application/json')
    if (!raw) return

    let payload
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }

    const { id, sourceStatus } = payload
    if (!id || sourceStatus === targetStatus) return

    // Destructive move — show confirmation
    if (targetStatus === 'rejected' || targetStatus === 'hired') {
      const candidate = columns[sourceStatus]?.find(c => c.id === id)
      setConfirmDialog({
        candidateName: candidate?.name || 'this candidate',
        targetLabel: COLUMN_CONFIG[targetStatus]?.label || targetStatus,
        payload,
        targetStatus,
      })
      return
    }

    // Non-destructive — proceed immediately
    executeDrop(payload, targetStatus)
  }

  if (loading) {
    return (
      <div className="h-[calc(100vh-4rem)] flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col text-slate-900 dark:text-slate-100">
      <main className="flex-1 overflow-hidden px-4 sm:px-6 lg:px-8 py-6">
        <ViewerReadOnlyBanner />
        <div className="flex items-center gap-3 mb-5">
          <div className="w-10 h-10 rounded-xl bg-brand-50 ring-1 ring-brand-200 flex items-center justify-center">
            <Columns className="w-5 h-5 text-brand-600" />
          </div>
          <div>
            <h2 className="text-2xl font-extrabold text-brand-900 dark:text-slate-100 tracking-tight">Pipeline</h2>
            <p className="text-slate-500 dark:text-slate-400 text-sm font-medium">
              {canWrite ? 'Drag and drop candidates between columns' : 'Read-only pipeline view'}
            </p>
          </div>
        </div>

        <div className="flex gap-4 overflow-x-auto pb-2 h-full">
          {PIPELINE_STAGES.map(status => {
            const cfg = COLUMN_CONFIG[status]
            const items = columns[status] || []
            const count = counts[status] || 0
            const isDropTarget = dragOverColumn === status
            const isCollapsed = !!collapsedCols[status]

            return (
              <div
                key={status}
                className={`flex-shrink-0 flex flex-col rounded-2xl border transition-all duration-200 ${
                  isCollapsed ? 'w-20' : 'w-72'
                } ${
                  isDropTarget
                    ? `${cfg.dropHighlight} border-2`
                    : 'bg-white/80 backdrop-blur-md border-brand-100'
                }`}
                onDragOver={(e) => handleDragOver(e, status)}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, status)}
              >
                {/* Column Header — clickable to toggle collapse */}
                <button
                  type="button"
                  className={`px-4 py-3 rounded-t-2xl border-b ${cfg.headerBg} ${cfg.headerBorder} flex items-center justify-between cursor-pointer select-none hover:opacity-80 transition-opacity w-full text-left`}
                  onClick={() => toggleCollapse(status)}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {isCollapsed ? (
                      <ChevronRight className={`w-4 h-4 flex-shrink-0 ${cfg.headerText}`} />
                    ) : (
                      <ChevronDown className={`w-4 h-4 flex-shrink-0 ${cfg.headerText}`} />
                    )}
                    {!isCollapsed && (
                      <span className={`text-sm font-bold ${cfg.headerText} truncate`}>{cfg.label}</span>
                    )}
                  </div>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${cfg.badgeBg} ${cfg.badgeText} ring-1 ${cfg.ring} flex-shrink-0`}>
                    {count}
                  </span>
                </button>

                {/* Card List — hidden when collapsed */}
                {!isCollapsed && (
                  <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
                    {items.length === 0 ? (
                      <EmptyState
                        icon={Inbox}
                        title="No candidates"
                        description="Drag candidates here or change their status"
                        className="py-8"
                      />
                    ) : (
                      items.map(candidate => (
                        <button
                          type="button"
                          key={candidate.id}
                          draggable={canWrite}
                          onDragStart={(e) => handleDragStart(e, candidate, status)}
                          onDragEnd={handleDragEnd}
                          onClick={() => navigate(`/candidates/${candidate.id}`)}
                          className={`bg-white dark:bg-slate-800 rounded-xl ring-1 ring-brand-100 dark:ring-slate-700 shadow-brand-sm p-3 cursor-pointer hover:shadow-brand transition-shadow select-none w-full text-left ${
                            draggingId === candidate.id ? 'opacity-50' : ''
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div className="min-w-0">
                              <p className="text-sm font-bold text-brand-900 truncate">
                                {personLabel(candidate.name, candidate.email)}
                              </p>
                              {candidate.current_role && (
                                <p className="text-xs text-slate-500 font-medium truncate flex items-center gap-1 mt-0.5">
                                  <Briefcase className="w-3 h-3 text-slate-400" />
                                  {safeStr(candidate.current_role)}
                                </p>
                              )}
                            </div>
                            <ScoreBadge score={candidate.best_score} />
                          </div>

                          {candidate.matched_skills && candidate.matched_skills.length > 0 && (
                            <div className="flex flex-wrap gap-1 mb-2">
                              {candidate.matched_skills.slice(0, 3).map((skill, i) => (
                                <span
                                  key={i}
                                  className="px-1.5 py-0.5 bg-green-50 text-green-700 text-[10px] rounded font-semibold ring-1 ring-green-100"
                                >
                                  {safeStr(skill)}
                                </span>
                              ))}
                            </div>
                          )}

                          {candidate.jd_name && (
                            <p className="text-[11px] text-slate-400 font-medium truncate">
                              {safeStr(candidate.jd_name)}
                            </p>
                          )}
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </main>

      {confirmDialog && (
        <ConfirmDialog
          candidateName={confirmDialog.candidateName}
          targetLabel={confirmDialog.targetLabel}
          onConfirm={() => {
            executeDrop(confirmDialog.payload, confirmDialog.targetStatus)
            setConfirmDialog(null)
          }}
          onCancel={() => setConfirmDialog(null)}
        />
      )}

      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onDone={() => setToast(null)}
        />
      )}
      <FeatureGuideModal
        open={pipelineGuide.open}
        guide={pipelineGuide.guide}
        onDismiss={pipelineGuide.dismiss}
      />
    </div>
  )
}
