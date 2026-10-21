import { useState, useEffect, useCallback, memo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Briefcase, Plus, Loader2, Users, ChevronRight, AlertTriangle, Pencil, Trash2 } from 'lucide-react'
import { listRequisitions, createRequisition, getTeamMembers, deleteRequisition, createRequisitionOpenRequest } from '../lib/api'
import { useOnboarding } from '../contexts/useOnboarding'
import useFeatureGuide from '../hooks/useFeatureGuide'
import FeatureGuideModal from '../components/onboarding/FeatureGuideModal'
import { Button } from '../components/ui'
import { PageHeaderCard } from '../components/patterns/PageHeader'
import usePermissions from '../hooks/usePermissions'
import { ViewerReadOnlyBanner } from '../components/RequireWriteAccess'
import { REQUISITIONS } from '../lib/uxLabels'
import { showSuccess, showError } from '../lib/toast'
import useConfirm from '../hooks/useConfirm'

const STATUS_STYLES = {
  draft: 'bg-slate-100 text-slate-700 ring-slate-200',
  intake_in_progress: 'bg-amber-50 text-amber-700 ring-amber-200',
  calibrated: 'bg-blue-50 text-blue-700 ring-blue-200',
  sourcing: 'bg-green-50 text-green-700 ring-green-200',
  interviewing: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  offer: 'bg-purple-50 text-purple-700 ring-purple-200',
  filled: 'bg-slate-50 text-slate-600 ring-slate-200',
  cancelled: 'bg-red-50 text-red-700 ring-red-200',
}

const FILTERS = ['all', 'draft', 'intake_in_progress', 'calibrated', 'sourcing', 'interviewing', 'offer', 'filled']

const RequisitionRow = memo(function RequisitionRow({ r, canWrite, deletingId, onEdit, onDelete }) {
  return (
    <div className="flex items-center gap-4 bg-white/90 dark:bg-dark-card rounded-2xl ring-1 ring-brand-100 dark:ring-white/10 p-4 hover:ring-brand-300 dark:hover:ring-brand-500/40 transition-all group">
      <Link to={`/requisitions/${r.id}`} className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="font-bold text-brand-900 dark:text-dark-text-primary truncate">{r.title}</h3>
          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 capitalize ${STATUS_STYLES[r.status] || STATUS_STYLES.draft}`}>
            {(r.status || 'draft').replace(/_/g, ' ')}
          </span>
          {!r.is_calibrated && (
            <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 rounded-full ring-1 ring-amber-200 dark:ring-amber-800">
              <AlertTriangle className="w-3 h-3" />
              Needs calibration
            </span>
          )}
        </div>
        <p className="text-xs text-slate-500 dark:text-dark-text-secondary mt-0.5 truncate">
          {[r.client_name, r.location].filter(Boolean).join(' · ') || 'No client set'}
        </p>
      </Link>
      <div className="flex items-center gap-2 shrink-0 text-sm text-slate-500 dark:text-dark-text-secondary">
        <span className="inline-flex items-center gap-1">
          <Users className="w-4 h-4" />
          {r.candidate_count ?? 0}
        </span>
        {canWrite && (
          <>
            <button
              type="button"
              onClick={(e) => onEdit(e, r)}
              className="p-2 rounded-lg text-slate-400 hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-dark-card-elevated transition-colors"
              title="Edit requisition"
              aria-label={`Edit ${r.title}`}
            >
              <Pencil className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={(e) => onDelete(e, r)}
              disabled={deletingId === r.id}
              className="p-2 rounded-lg text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors disabled:opacity-50"
              title="Delete requisition"
              aria-label={`Delete ${r.title}`}
            >
              {deletingId === r.id ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
            </button>
          </>
        )}
        <Link
          to={`/requisitions/${r.id}`}
          className="p-1 text-brand-400 hover:text-brand-600 dark:text-brand-300"
          aria-label={`Open ${r.title}`}
        >
          <ChevronRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
        </Link>
      </div>
    </div>
  )
})

export default function RequisitionsPage() {
  const navigate = useNavigate()
  const { confirm, dialog } = useConfirm()
  const { completeChecklistItem } = useOnboarding()
  const requisitionsGuide = useFeatureGuide('requisitions')
  const { canWrite, isHiringManager } = usePermissions()
  const [reqs, setReqs] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [filter, setFilter] = useState('all')
  const [form, setForm] = useState({ title: '', jd_text: '', client_name: '', location: '', primary_hiring_manager_id: '' })
  const [teamMembers, setTeamMembers] = useState([])
  const [deletingId, setDeletingId] = useState(null)
  const [showHmRequestOpening, setShowHmRequestOpening] = useState(false)
  const [hmRequestForm, setHmRequestForm] = useState({ title: '', jd_text: '', notes: '' })
  const [requestingOpening, setRequestingOpening] = useState(false)

  const handleDelete = async (e, req) => {
    e.preventDefault()
    e.stopPropagation()
    const ok = await confirm({
      title: 'Delete requisition',
      message: `Delete requisition "${req.title}"? This cannot be undone.`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    setDeletingId(req.id)
    try {
      await deleteRequisition(req.id)
      setReqs((prev) => prev.filter((r) => r.id !== req.id))
    } catch (err) {
      showError(err.response?.data?.detail || err.message || 'Failed to delete requisition')
    } finally {
      setDeletingId(null)
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listRequisitions(
        filter === 'all' ? null : filter,
        isHiringManager,
      )
      setReqs(Array.isArray(data) ? data : [])
    } catch {
      setReqs([])
    } finally {
      setLoading(false)
    }
  }, [filter, isHiringManager])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!canWrite) return
    getTeamMembers()
      .then((data) => setTeamMembers(Array.isArray(data) ? data : []))
      .catch(() => setTeamMembers([]))
  }, [canWrite])

  const hmCandidates = teamMembers.filter(
    (m) => m.role === 'hiring_manager' || m.role === 'admin' || m.role === 'recruiter',
  )

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!form.title.trim() || !form.jd_text.trim()) return
    setCreating(true)
    try {
      const req = await createRequisition({
        title: form.title.trim(),
        jd_text: form.jd_text.trim(),
        client_name: form.client_name.trim() || null,
        location: form.location.trim() || null,
        status: 'draft',
        primary_hiring_manager_id: form.primary_hiring_manager_id
          ? Number(form.primary_hiring_manager_id)
          : null,
        hiring_manager_ids: form.primary_hiring_manager_id
          ? [Number(form.primary_hiring_manager_id)]
          : [],
      })
      setShowCreate(false)
      setForm({ title: '', jd_text: '', client_name: '', location: '', primary_hiring_manager_id: '' })
      completeChecklistItem('createdJob')
      navigate(`/requisitions/${req.id}`)
    } catch (err) {
      showError(err.response?.data?.detail?.message || err.response?.data?.detail || 'Failed to create requisition')
    } finally {
      setCreating(false)
    }
  }

  const pageTitle = isHiringManager ? REQUISITIONS.hmPageTitle : REQUISITIONS.pageTitle
  const pageSubtitle = isHiringManager ? REQUISITIONS.hmPageSubtitle : REQUISITIONS.pageSubtitle

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {!canWrite && !isHiringManager && <ViewerReadOnlyBanner />}

      <PageHeaderCard
        title={pageTitle}
        subtitle={pageSubtitle}
        icon={Briefcase}
        actions={
          <>
            {isHiringManager && (
              <Button variant="secondary" onClick={() => setShowHmRequestOpening(true)}>
                {REQUISITIONS.requestOpeningCta}
              </Button>
            )}
            {canWrite && (
              <Button onClick={() => setShowCreate(true)}>
                <Plus className="w-4 h-4" />
                {REQUISITIONS.createCta}
              </Button>
            )}
          </>
        }
      />

      <div className="flex gap-2 mb-6 flex-wrap">
        {FILTERS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setFilter(s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold capitalize transition-colors ${
              filter === s ? 'bg-brand-600 text-white' : 'text-slate-600 hover:bg-brand-50 ring-1 ring-brand-100'
            }`}
          >
            {s === 'all' ? 'All' : s.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {canWrite && showCreate && (
        <form onSubmit={handleCreate} className="mb-8 bg-white/90 rounded-2xl ring-1 ring-brand-100 p-6 space-y-4">
          <h2 className="font-bold text-brand-900">{REQUISITIONS.createCta}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="block text-sm sm:col-span-2">
              <span className="font-semibold text-slate-700">Title</span>
              <input
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
                required
              />
            </label>
            <label className="block text-sm">
              <span className="font-semibold text-slate-700">Client (optional)</span>
              <input
                value={form.client_name}
                onChange={(e) => setForm((f) => ({ ...f, client_name: e.target.value }))}
                className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="font-semibold text-slate-700">Location (optional)</span>
              <input
                value={form.location}
                onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
                className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm sm:col-span-2">
              <span className="font-semibold text-slate-700">Primary hiring manager (optional)</span>
              <select
                value={form.primary_hiring_manager_id}
                onChange={(e) => setForm((f) => ({ ...f, primary_hiring_manager_id: e.target.value }))}
                className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm bg-white"
              >
                <option value="">Assign later on requisition Overview</option>
                {hmCandidates.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.email} ({m.role.replace(/_/g, ' ')})
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm sm:col-span-2">
              <span className="font-semibold text-slate-700">Job description</span>
              <textarea
                value={form.jd_text}
                onChange={(e) => setForm((f) => ({ ...f, jd_text: e.target.value }))}
                rows={6}
                className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm resize-none font-mono"
                required
                placeholder="Paste the JD — intake will refine must-haves with the hiring manager"
              />
            </label>
          </div>
          <div className="flex gap-2">
            <Button type="submit" disabled={creating}>
              {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Create
            </Button>
            <Button type="button" variant="ghost" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {isHiringManager && showHmRequestOpening && (
        <form
          onSubmit={async (e) => {
            e.preventDefault()
            setRequestingOpening(true)
            try {
              await createRequisitionOpenRequest(hmRequestForm)
              setShowHmRequestOpening(false)
              setHmRequestForm({ title: '', jd_text: '', notes: '' })
              showSuccess('Opening request submitted — TA will assign a recruiter.')
            } catch {
              showError('Failed to submit opening request')
            } finally {
              setRequestingOpening(false)
            }
          }}
          className="mb-8 bg-white/90 rounded-2xl ring-1 ring-brand-100 p-6 space-y-4"
        >
          <h2 className="font-bold text-brand-900">{REQUISITIONS.requestOpeningCta}</h2>
          <label className="block text-sm">
            <span className="font-semibold text-slate-700">Title</span>
            <input
              value={hmRequestForm.title}
              onChange={(e) => setHmRequestForm((f) => ({ ...f, title: e.target.value }))}
              className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
              required
            />
          </label>
          <label className="block text-sm">
            <span className="font-semibold text-slate-700">Job description</span>
            <textarea
              value={hmRequestForm.jd_text}
              onChange={(e) => setHmRequestForm((f) => ({ ...f, jd_text: e.target.value }))}
              rows={6}
              className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
              required
            />
          </label>
          <label className="block text-sm">
            <span className="font-semibold text-slate-700">Notes (optional)</span>
            <textarea
              value={hmRequestForm.notes}
              onChange={(e) => setHmRequestForm((f) => ({ ...f, notes: e.target.value }))}
              rows={2}
              className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
            />
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={requestingOpening}>
              {requestingOpening ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Submit request
            </Button>
            <Button type="button" variant="ghost" onClick={() => setShowHmRequestOpening(false)}>Cancel</Button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
        </div>
      ) : reqs.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Briefcase className="w-12 h-12 mx-auto mb-3 text-brand-200" />
          <p className="font-medium">{REQUISITIONS.emptyHint}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {reqs.map((r) => (
            <RequisitionRow
              key={r.id}
              r={r}
              canWrite={canWrite}
              deletingId={deletingId}
              onEdit={(e, row) => {
                e.preventDefault()
                navigate(`/requisitions/${row.id}`)
              }}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
      <FeatureGuideModal
        open={requisitionsGuide.open}
        guide={requisitionsGuide.guide}
        onDismiss={requisitionsGuide.dismiss}
      />
      {dialog}
    </div>
  )
}
