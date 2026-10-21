import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, Briefcase, Loader2, CheckCircle2, Sparkles,
  ListChecks, Columns3, X, Search,
} from 'lucide-react'
import {
  getRequisition,
  getRequisitionPipeline,
  updateRequisitionIntake,
  suggestRequisitionIntake,
  calibrateRequisition,
  hmApproveRequisition,
  updateRequisitionCandidateStatus,
  submitCandidateToHm,
  recordHmOutcome,
  getRequisitionAnalytics,
  addCandidatesToRequisition,
  getCandidates,
  getRequisitionCriteriaVersions,
  updateRequisitionCriteria,
  updateRequisition,
  checkRequisitionIntakeGate,
  getTeamMembers,
  inviteTeamMember,
  requestRequisitionHm,
  approveRequisitionHmRequest,
  rejectRequisitionHmRequest,
  assignRequisitionRecruiter,
  applyRequisitionFeedback,
  getRequisitionSettings,
} from '../lib/api'
import { Button, Card } from '../components/ui'
import usePermissions from '../hooks/usePermissions'
import { ViewerReadOnlyBanner } from '../components/RequireWriteAccess'
import { REQUISITIONS } from '../lib/uxLabels'
import { showSuccess, showError } from '../lib/toast'
import { useAuth } from '../contexts/useAuth'
import HmOutcomeModal from '../components/HmOutcomeModal'
import RequisitionSourcingPanel from '../components/RequisitionSourcingPanel'
import { JdFullModal, JdReferencePanel, HmRequestModal, HmInviteModal, IntakeWorkflowBar, IntakeForm } from '../components/requisition/RequisitionDetailPanels'
import { resolveRequisitionNextAction, parseScoringWeights } from '../components/requisition/requisitionDetailUtils'
import RequisitionOverviewPanel from '../components/requisition/RequisitionOverviewPanel'
import RequisitionCriteriaPanel from '../components/requisition/RequisitionCriteriaPanel'
import RequisitionPipelineBoard from '../components/requisition/RequisitionPipelineBoard'
import { normalizeIntakeShape } from '../lib/intakeFormUtils'
import useConfirm from '../hooks/useConfirm'

const INTAKE_AUTOSAVE_MS = 2000

const TABS = [
  { id: 'overview', label: REQUISITIONS.overviewTab, icon: Briefcase },
  { id: 'intake', label: REQUISITIONS.intakeTab, icon: ListChecks },
  { id: 'sourcing', label: REQUISITIONS.sourcingTab, icon: Search },
  { id: 'criteria', label: REQUISITIONS.criteriaTab, icon: Sparkles },
  { id: 'pipeline', label: REQUISITIONS.pipelineTab, icon: Columns3 },
]

export default function RequisitionDetailPage() {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { canWrite, isHiringManager, isAdmin, canAssign } = usePermissions()
  const [req, setReq] = useState(null)
  const [intake, setIntake] = useState({})
  const [pipeline, setPipeline] = useState({})
  const [analytics, setAnalytics] = useState(null)
  const [criteriaVersions, setCriteriaVersions] = useState([])
  const [editCriteria, setEditCriteria] = useState(null)
  const [pipelineSync, setPipelineSync] = useState(null)
  const [tab, setTab] = useState(searchParams.get('tab') || 'overview')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [showAddCandidates, setShowAddCandidates] = useState(false)
  const [allCandidates, setAllCandidates] = useState([])
  const [selectedCandidateIds, setSelectedCandidateIds] = useState([])
  const [resumeWeight, setResumeWeight] = useState(40)
  const [interviewWeight, setInterviewWeight] = useState(60)
  const [savingWeights, setSavingWeights] = useState(false)
  const [intakeGate, setIntakeGate] = useState(null)
  const [savedIntakeSnapshot, setSavedIntakeSnapshot] = useState('')
  const [intakeSavedAt, setIntakeSavedAt] = useState(null)
  const [suggestingIntake, setSuggestingIntake] = useState(false)
  const [teamMembers, setTeamMembers] = useState([])
  const [hmSelectId, setHmSelectId] = useState('')
  const [savingHm, setSavingHm] = useState(false)
  const [showHmInvite, setShowHmInvite] = useState(false)
  const [showHmRequest, setShowHmRequest] = useState(false)
  const [showJdModal, setShowJdModal] = useState(false)
  const [hmPipelinePerm, setHmPipelinePerm] = useState('view_only')
  const [outcomeModal, setOutcomeModal] = useState(null)
  const [pendingFeedback, setPendingFeedback] = useState(null)
  const [submitNote, setSubmitNote] = useState('')
  const [assignRecruiterId, setAssignRecruiterId] = useState('')
  const [searchBrief, setSearchBrief] = useState('')
  const [sourcingBriefJson, setSourcingBriefJson] = useState({})
  const [routingPolicy, setRoutingPolicy] = useState({
    submit_to_hm_min_score: 80,
    ai_interview_min_score: 65,
    ai_interview_max_score: 79,
    auto_suggest: true,
  })
  const [savingRouting, setSavingRouting] = useState(false)

  const hmCandidates = teamMembers.filter(
    (m) => m.role === 'hiring_manager' || m.role === 'admin' || m.role === 'recruiter',
  )
  const recruiterCandidates = teamMembers.filter(
    (m) => m.role === 'recruiter' || m.role === 'admin' || m.role === 'ta_lead',
  )

  const intakeDirty = savedIntakeSnapshot !== JSON.stringify(intake || {})
  const intakeRef = useRef(intake)
  const { confirm, dialog } = useConfirm()

  const canSaveIntake = (canWrite || isHiringManager) && req?.intake_status !== 'approved'
  const canEditIntake = canSaveIntake

  useEffect(() => {
    intakeRef.current = intake
  }, [intake])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [r, pipe, stats, versions, gate] = await Promise.all([
        getRequisition(id),
        getRequisitionPipeline(id),
        getRequisitionAnalytics(id).catch(() => null),
        getRequisitionCriteriaVersions(id).catch(() => []),
        checkRequisitionIntakeGate(id).catch(() => null),
      ])
      setReq(r)
      setIntakeGate(gate)
      setHmSelectId(r.primary_hiring_manager_id ? String(r.primary_hiring_manager_id) : '')
      setAssignRecruiterId(r.assigned_recruiter_id ? String(r.assigned_recruiter_id) : '')
      const brief = r.search_brief_json || {}
      setSourcingBriefJson(brief)
      setSearchBrief(brief.latest_strategy || '')
      if (brief.pending_feedback) {
        setPendingFeedback(brief.pending_feedback)
      } else {
        setPendingFeedback(null)
      }
      const policy = r.routing_policy_json || {}
      setRoutingPolicy({
        submit_to_hm_min_score: policy.submit_to_hm_min_score ?? 80,
        ai_interview_min_score: policy.ai_interview_min_score ?? 65,
        ai_interview_max_score: policy.ai_interview_max_score ?? 79,
        auto_suggest: policy.auto_suggest !== false,
      })
      const loadedIntake = normalizeIntakeShape(r.intake_json || {})
      setIntake(loadedIntake)
      setSavedIntakeSnapshot(JSON.stringify(loadedIntake))
      const weights = parseScoringWeights(r.scoring_weights)
      setResumeWeight(weights.resume)
      setInterviewWeight(weights.interview)
      setPipeline(pipe.pipeline || {})
      setPipelineSync(pipe.sync || null)
      setAnalytics(stats)
      setCriteriaVersions(Array.isArray(versions) ? versions : [])
    } catch {
      setReq(null)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    getRequisitionSettings()
      .then((s) => setHmPipelinePerm(s.hm_pipeline_permission || 'view_only'))
      .catch(() => setHmPipelinePerm('view_only'))
  }, [])

  useEffect(() => {
    if (!intakeDirty || !canSaveIntake) return undefined
    const onBeforeUnload = (event) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [intakeDirty, canSaveIntake])

  const persistIntake = useCallback(async ({ silent = false } = {}) => {
    if (!canSaveIntake) return true
    setSaving(true)
    try {
      const updated = await updateRequisitionIntake(id, intakeRef.current, 'pending_hm')
      setReq(updated)
      const saved = normalizeIntakeShape(updated.intake_json || {})
      setIntake(saved)
      setSavedIntakeSnapshot(JSON.stringify(saved))
      setIntakeSavedAt(Date.now())
      const gate = await checkRequisitionIntakeGate(id).catch(() => null)
      setIntakeGate(gate)
      if (!silent) {
        showSuccess(
          gate?.intake_has_minimum_content
            ? (gate?.blocks && gate?.requires_hm_approval
              ? `${REQUISITIONS.intakeSaved} — HM approval required before screening (tenant policy).`
              : `${REQUISITIONS.intakeSaved} — you can screen candidates. HM approval locks criteria v1.`)
            : `${REQUISITIONS.intakeSaved} — add screen-focus topics or must-haves to unlock screening.`,
        )
      }
      return true
    } catch {
      if (!silent) showError('Failed to save intake')
      return false
    } finally {
      setSaving(false)
    }
  }, [id, canSaveIntake])

  const saveIntake = useCallback(() => persistIntake({ silent: false }), [persistIntake])

  const discardIntakeEdits = useCallback(() => {
    try {
      setIntake(normalizeIntakeShape(JSON.parse(savedIntakeSnapshot || '{}')))
    } catch {
      setIntake({})
    }
  }, [savedIntakeSnapshot])

  const leaveIntakeOrConfirm = useCallback(async () => {
    if (!intakeDirty || !canSaveIntake) return true
    const ok = await persistIntake({ silent: true })
    if (ok) return true
    const discard = await confirm({
      title: REQUISITIONS.intakeLeaveUnsavedTitle,
      message: REQUISITIONS.intakeLeaveUnsavedMessage,
      confirmLabel: REQUISITIONS.intakeDiscardLeave,
      cancelLabel: REQUISITIONS.intakeStay,
      danger: true,
    })
    if (discard) {
      discardIntakeEdits()
      return true
    }
    return false
  }, [intakeDirty, canSaveIntake, persistIntake, confirm, discardIntakeEdits])

  const requestTabChange = useCallback(async (nextTab) => {
    if (!nextTab || nextTab === tab) return
    if (tab === 'intake' && nextTab !== 'intake') {
      const canLeave = await leaveIntakeOrConfirm()
      if (!canLeave) return
    }
    setTab(nextTab)
  }, [tab, leaveIntakeOrConfirm])

  const guardedNavigate = useCallback(async (to) => {
    const canLeave = await leaveIntakeOrConfirm()
    if (canLeave) navigate(to)
  }, [leaveIntakeOrConfirm, navigate])

  useEffect(() => {
    if (!canSaveIntake || !intakeDirty || tab !== 'intake') return undefined
    const timer = setTimeout(() => {
      void persistIntake({ silent: true })
    }, INTAKE_AUTOSAVE_MS)
    return () => clearTimeout(timer)
  }, [intake, canSaveIntake, intakeDirty, tab, persistIntake])

  const refreshTeamMembers = async () => {
    try {
      const data = await getTeamMembers()
      setTeamMembers(Array.isArray(data) ? data : [])
      return Array.isArray(data) ? data : []
    } catch {
      setTeamMembers([])
      return []
    }
  }

  useEffect(() => {
    if (!canWrite) return
    refreshTeamMembers()
  }, [canWrite])

  const saveHiringManager = async (managerId) => {
    const selectedId = managerId ?? (hmSelectId ? Number(hmSelectId) : null)
    if (!selectedId) return
    setSavingHm(true)
    try {
      const updated = await updateRequisition(id, { primary_hiring_manager_id: selectedId })
      setReq(updated)
      setHmSelectId(String(selectedId))
      const gate = await checkRequisitionIntakeGate(id).catch(() => null)
      setIntakeGate(gate)
      showSuccess('Hiring manager assigned')
    } catch {
      showError('Failed to assign hiring manager')
    } finally {
      setSavingHm(false)
    }
  }

  const assignSelfAsHm = () => {
    if (!user?.id) return
    setHmSelectId(String(user.id))
    saveHiringManager(user.id)
  }

  const handleHmInvited = async (inviteResult) => {
    const members = await refreshTeamMembers()
    const invitedId = inviteResult?.user_id
      || members.find((m) => m.email === inviteResult?.email)?.id
    setShowHmInvite(false)
    if (invitedId) {
      await saveHiringManager(invitedId)
      showSuccess(REQUISITIONS.hmInviteSuccess)
    } else {
      showSuccess('Hiring manager invited — select them from the dropdown and save.')
    }
  }

  const handleHmRequested = async ({ email, notes }) => {
    const updated = await requestRequisitionHm(id, { email, notes })
    setReq(updated)
    const gate = await checkRequisitionIntakeGate(id).catch(() => null)
    setIntakeGate(gate)
    setShowHmRequest(false)
    showSuccess(REQUISITIONS.hmRequestSuccess)
  }

  const handleApproveHmRequest = async () => {
    setSavingHm(true)
    try {
      const updated = await approveRequisitionHmRequest(id)
      setReq(updated)
      setHmSelectId(updated.primary_hiring_manager_id ? String(updated.primary_hiring_manager_id) : '')
      await refreshTeamMembers()
      const gate = await checkRequisitionIntakeGate(id).catch(() => null)
      setIntakeGate(gate)
      showSuccess('Hiring manager approved and assigned')
    } catch {
      showError('Failed to approve HM request')
    } finally {
      setSavingHm(false)
    }
  }

  const handleRejectHmRequest = async () => {
    const notes = window.prompt('Optional reason for rejection:') || null
    setSavingHm(true)
    try {
      const updated = await rejectRequisitionHmRequest(id, notes)
      setReq(updated)
      const gate = await checkRequisitionIntakeGate(id).catch(() => null)
      setIntakeGate(gate)
      showSuccess('HM request rejected')
    } catch {
      showError('Failed to reject HM request')
    } finally {
      setSavingHm(false)
    }
  }

  const focusHmAssignment = async () => {
    await requestTabChange('overview')
    requestAnimationFrame(() => {
      document.getElementById('hm-assignment')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }

  const pendingPipelineCount = analytics?.funnel?.pending
    ?? (pipeline?.pending?.length ?? 0)

  const nextAction = resolveRequisitionNextAction({
    req,
    intakeGate,
    canWrite,
    isHiringManager,
    isAdmin,
    pendingCount: pendingPipelineCount,
  })

  const handleNextAction = (action) => {
    switch (action) {
      case 'hm':
        focusHmAssignment()
        break
      case 'intake':
        void requestTabChange('intake')
        break
      case 'screen':
        openScreenCandidate()
        break
      default:
        break
    }
  }

  const screenCandidateDisabled = Boolean(intakeGate?.blocks)
  const screenCandidateTitle = screenCandidateDisabled
    ? (intakeGate?.warning || REQUISITIONS.screenCandidateBlocked)
    : (req?.intake_gate_warning || undefined)

  const openScreenCandidate = async () => {
    if (screenCandidateDisabled) return
    const canLeave = await leaveIntakeOrConfirm()
    if (!canLeave) return
    navigate(`/analyze?requisition_id=${id}`)
  }

  const saveHiringWeights = async () => {
    setSavingWeights(true)
    try {
      const updated = await updateRequisition(id, {
        scoring_weights: {
          resume_weight: resumeWeight / 100,
          interview_weight: interviewWeight / 100,
        },
      })
      setReq(updated)
      const weights = parseScoringWeights(updated.scoring_weights)
      setResumeWeight(weights.resume)
      setInterviewWeight(weights.interview)
    } catch {
      showError('Failed to save hiring signal weights')
    } finally {
      setSavingWeights(false)
    }
  }

  const suggestIntake = async () => {
    setSuggestingIntake(true)
    try {
      const { intake_json: suggested } = await suggestRequisitionIntake(id)
      setIntake(normalizeIntakeShape(suggested || {}))
      showSuccess(REQUISITIONS.intakeSuggestDone)
    } catch {
      showError('Could not suggest intake from job description')
    } finally {
      setSuggestingIntake(false)
    }
  }

  const handleCalibrate = async () => {
    if (intakeDirty && canSaveIntake) {
      const ok = await persistIntake({ silent: true })
      if (!ok) {
        showError('Save intake before calibrating criteria')
        return
      }
    }
    setSaving(true)
    try {
      const updated = await calibrateRequisition(id)
      setReq(updated)
      await requestTabChange('criteria')
      showSuccess('Criteria calibrated — you can screen candidates')
      await load()
    } catch {
      showError('Calibration failed')
    } finally {
      setSaving(false)
    }
  }

  const handleHmApproval = async (approved) => {
    setSaving(true)
    try {
      const intakePayload = intakeDirty ? intake : null
      const updated = await hmApproveRequisition(id, approved, null, intakePayload)
      setReq(updated)
      if (intakePayload) {
        setSavedIntakeSnapshot(JSON.stringify(intakePayload))
      }
      await load()
      showSuccess(approved ? 'Intake approved — criteria v1 locked' : 'Changes requested')
    } catch {
      showError('Approval failed')
    } finally {
      setSaving(false)
    }
  }

  const handleOutcomeClick = (candidateId, outcome) => {
    if (outcome === 'reject') {
      setOutcomeModal({ candidateId, outcome })
      return
    }
    handleOutcome(candidateId, outcome, null, null)
  }

  const handleOutcome = async (candidateId, outcome, reasonCode, notes) => {
    try {
      const result = await recordHmOutcome(id, candidateId, outcome, reasonCode, notes)
      if (result?.feedback_suggestions) {
        setPendingFeedback(result.feedback_suggestions)
      }
      await load()
      showSuccess(`Recorded: ${outcome}`)
    } catch (err) {
      showError(err.response?.data?.detail || 'Failed to record outcome')
    } finally {
      setOutcomeModal(null)
    }
  }

  const handleApplyFeedback = async () => {
    if (!pendingFeedback) return
    setSaving(true)
    try {
      const updated = await applyRequisitionFeedback(id, pendingFeedback, false)
      setReq(updated)
      setPendingFeedback(null)
      const brief = updated.search_brief_json || {}
      setSourcingBriefJson(brief)
      setSearchBrief(brief.latest_strategy || '')
      showSuccess('Sourcing brief updated from HM feedback')
    } catch {
      showError('Failed to apply feedback')
    } finally {
      setSaving(false)
    }
  }

  const handleAssignRecruiter = async () => {
    if (!assignRecruiterId) return
    setSaving(true)
    try {
      const updated = await assignRequisitionRecruiter(id, Number(assignRecruiterId))
      setReq(updated)
      showSuccess('Recruiter assigned')
    } catch {
      showError('Failed to assign recruiter')
    } finally {
      setSaving(false)
    }
  }

  const saveRoutingPolicy = async () => {
    setSavingRouting(true)
    try {
      const updated = await updateRequisition(id, { routing_policy_json: routingPolicy })
      setReq(updated)
      showSuccess('Routing thresholds saved')
    } catch {
      showError('Failed to save routing policy')
    } finally {
      setSavingRouting(false)
    }
  }

  const saveCriteria = async () => {
    if (!editCriteria) return
    setSaving(true)
    try {
      const updated = await updateRequisitionCriteria(id, editCriteria)
      setReq(updated)
      setEditCriteria(null)
      await load()
    } catch {
      showError('Failed to save criteria')
    } finally {
      setSaving(false)
    }
  }

  const handleStatusChange = async (candidateId, status) => {
    try {
      await updateRequisitionCandidateStatus(id, candidateId, status)
      await load()
    } catch {
      showError('Failed to update status')
    }
  }

  const handleSubmit = async (candidateId) => {
    try {
      await submitCandidateToHm(id, candidateId, { recruiter_note: submitNote || undefined })
      setSubmitNote('')
      await load()
    } catch {
      showError('Submit failed')
    }
  }

  useEffect(() => {
    const t = searchParams.get('tab')
    if (t) void requestTabChange(t)
  }, [searchParams, requestTabChange])

  const openAddCandidates = async () => {
    try {
      const data = await getCandidates()
      setAllCandidates(Array.isArray(data) ? data : data?.candidates || [])
      setShowAddCandidates(true)
    } catch {
      setAllCandidates([])
      setShowAddCandidates(true)
    }
  }

  const handleAddCandidates = async () => {
    if (!selectedCandidateIds.length) return
    try {
      await addCandidatesToRequisition(id, selectedCandidateIds)
      setShowAddCandidates(false)
      setSelectedCandidateIds([])
      await load()
    } catch {
      showError('Failed to add candidates')
    }
  }

  const canWritePipeline = canWrite
    || (isHiringManager && hmPipelinePerm !== 'view_only')

  if (loading) {
    return (
      <div className="flex justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
      </div>
    )
  }

  if (!req) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-16 text-center">
        <p className="text-slate-500">Requisition not found</p>
        <Link to="/requisitions" className="text-brand-600 text-sm font-semibold mt-2 inline-block">Back</Link>
      </div>
    )
  }

  const criteria = req.calibrated_criteria_json || {}

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {!canWrite && !isHiringManager && <ViewerReadOnlyBanner />}

      <button
        type="button"
        onClick={() => void guardedNavigate('/requisitions')}
        className="inline-flex items-center gap-1 text-sm font-semibold text-brand-600 mb-4 hover:text-brand-800"
      >
        <ArrowLeft className="w-4 h-4" />
        All requisitions
      </button>

      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-extrabold text-brand-900 tracking-tight">{req.title}</h1>
          <p className="text-sm text-slate-500 mt-1 capitalize">
            {req.status?.replace(/_/g, ' ')}
            {req.client_name ? ` · ${req.client_name}` : ''}
            {req.location ? ` · ${req.location}` : ''}
          </p>
          {req.intake_gate_warning && (
            <div className="mt-2 text-sm text-amber-700 bg-amber-50 ring-1 ring-amber-200 rounded-xl px-3 py-2 flex flex-wrap items-center gap-2">
              <span className="flex-1 min-w-[12rem]">{req.intake_gate_warning}</span>
              {canWrite && !intakeGate?.hm_assigned && (
                <button
                  type="button"
                  onClick={focusHmAssignment}
                  className="shrink-0 text-xs font-semibold text-amber-900 underline hover:no-underline"
                >
                  Assign hiring manager
                </button>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-nowrap items-center gap-2 overflow-x-auto max-w-full pb-1 shrink-0">
          {canSaveIntake && tab === 'intake' && (
            <Button onClick={saveIntake} disabled={saving || !intakeDirty}>
              {saving ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Saving…
                </>
              ) : intakeSavedAt && !intakeDirty ? (
                <>
                  <CheckCircle2 className="w-4 h-4" />
                  Saved
                </>
              ) : (
                'Save intake'
              )}
            </Button>
          )}
          {canWrite && tab === 'intake' && intakeDirty && !saving && (
            <span className="text-xs font-semibold text-amber-700 self-center">{REQUISITIONS.intakeUnsaved}</span>
          )}
          {canWrite && tab === 'intake' && saving && (
            <span className="text-xs font-semibold text-brand-700 self-center">{REQUISITIONS.intakeSaving}</span>
          )}
          {canWrite && (
            <Button variant="secondary" onClick={handleCalibrate} disabled={saving} className="shrink-0 whitespace-nowrap">
              <Sparkles className="w-4 h-4" />
              {REQUISITIONS.calibrateCta}
            </Button>
          )}
          {isHiringManager && req.intake_status === 'pending_hm' && (
            <>
              <Button onClick={() => handleHmApproval(true)} disabled={saving}>
                <CheckCircle2 className="w-4 h-4" />
                {REQUISITIONS.intakeHmSaveApprove}
              </Button>
              <Button variant="ghost" onClick={() => handleHmApproval(false)} disabled={saving}>
                {REQUISITIONS.requestChangesCta}
              </Button>
            </>
          )}
          {canWrite && (
            <Button
              variant="ghost"
              onClick={() => void guardedNavigate(`/requisitions/${id}/handoff`)}
              title={REQUISITIONS.hmReviewPackHint}
              className="shrink-0 whitespace-nowrap"
            >
              {REQUISITIONS.hmReviewPackCta}
            </Button>
          )}
          {canWrite && (
            <Button variant="ghost" onClick={openAddCandidates} className="shrink-0 whitespace-nowrap">
              Add candidates
            </Button>
          )}
          {canWrite && (
            <Button
              variant="ghost"
              disabled={screenCandidateDisabled}
              title={screenCandidateTitle}
              aria-disabled={screenCandidateDisabled}
              className={`shrink-0 whitespace-nowrap ${screenCandidateDisabled ? 'opacity-50 cursor-not-allowed' : ''}`}
              onClick={openScreenCandidate}
            >
              Screen candidate
            </Button>
          )}
        </div>
      </div>

      <div className="flex gap-1 mb-6 p-1 bg-brand-50/80 rounded-xl ring-1 ring-brand-100 w-fit flex-wrap">
        {TABS.map(({ id: tabId, label, icon: Icon }) => (
          <button
            key={tabId}
            type="button"
            onClick={() => void requestTabChange(tabId)}
            className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-colors ${
              tab === tabId ? 'bg-white text-brand-800 shadow-sm' : 'text-slate-600 hover:text-brand-700'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <RequisitionOverviewPanel
          nextAction={nextAction}
          onNextAction={handleNextAction}
          canWrite={canWrite}
          canAssign={canAssign}
          isAdmin={isAdmin}
          req={req}
          hmSelectId={hmSelectId}
          setHmSelectId={setHmSelectId}
          hmCandidates={hmCandidates}
          saveHiringManager={saveHiringManager}
          savingHm={savingHm}
          onShowHmRequest={() => setShowHmRequest(true)}
          onShowHmInvite={() => setShowHmInvite(true)}
          assignSelfAsHm={assignSelfAsHm}
          onApproveHmRequest={handleApproveHmRequest}
          onRejectHmRequest={handleRejectHmRequest}
          assignRecruiterId={assignRecruiterId}
          setAssignRecruiterId={setAssignRecruiterId}
          recruiterCandidates={recruiterCandidates}
          onAssignRecruiter={handleAssignRecruiter}
          saving={saving}
          pendingFeedback={pendingFeedback}
          onApplyFeedback={handleApplyFeedback}
          analytics={analytics}
          resumeWeight={resumeWeight}
          setResumeWeight={setResumeWeight}
          interviewWeight={interviewWeight}
          setInterviewWeight={setInterviewWeight}
          saveHiringWeights={saveHiringWeights}
          savingWeights={savingWeights}
          onViewFullJd={() => setShowJdModal(true)}
        />
      )}

      {tab === 'intake' && (
        <Card className="p-6 space-y-4">
          <JdReferencePanel jdText={req.jd_text} onViewFull={() => setShowJdModal(true)} />
          <IntakeWorkflowBar intakeGate={intakeGate} req={req} />
          {req.intake_status === 'changes_requested' && (
            <p className="text-sm text-amber-800 bg-amber-50 ring-1 ring-amber-200 rounded-xl px-3 py-2">
              {REQUISITIONS.intakeChangesRequested}
            </p>
          )}
          {req.intake_status === 'approved' && (
            <p className="text-sm text-emerald-800 bg-emerald-50 ring-1 ring-emerald-200 rounded-xl px-3 py-2">
              Intake approved by the hiring manager. Fields are locked; update criteria on the Criteria tab if needed.
            </p>
          )}
          <IntakeForm
            intake={intake}
            onChange={setIntake}
            readOnly={!canEditIntake}
            onSuggest={canEditIntake && canWrite ? suggestIntake : null}
            suggesting={suggestingIntake}
            onSave={canSaveIntake ? saveIntake : null}
            saving={saving}
            intakeDirty={intakeDirty}
            intakeSavedAt={intakeSavedAt}
          />
          {canWrite && (
            <p className="text-xs text-slate-500 pt-2 border-t border-brand-50">
              Sourcing strategy and channels live on the{' '}
              <button type="button" className="text-brand-600 font-semibold hover:underline" onClick={() => void requestTabChange('sourcing')}>
                Sourcing tab
              </button>
              .
            </p>
          )}
        </Card>
      )}

      {tab === 'sourcing' && (
        <RequisitionSourcingPanel
          req={{ ...req, search_brief_json: sourcingBriefJson }}
          searchBrief={searchBrief}
          onSearchBriefChange={(next) => {
            setSourcingBriefJson(next)
            setSearchBrief(next.latest_strategy || '')
          }}
          onSave={async (payload) => {
            setSaving(true)
            try {
              const updated = await updateRequisition(id, { search_brief_json: payload })
              setReq(updated)
              const saved = updated.search_brief_json || {}
              setSourcingBriefJson(saved)
              setSearchBrief(saved.latest_strategy || '')
              showSuccess('Sourcing plan saved')
            } catch {
              showError('Failed to save sourcing plan')
            } finally {
              setSaving(false)
            }
          }}
          saving={saving}
          canWrite={canWrite}
          onAddCandidates={() => setShowAddCandidates(true)}
          onScreen={openScreenCandidate}
        />
      )}

      {tab === 'criteria' && (
        <RequisitionCriteriaPanel
          req={req}
          canWrite={canWrite}
          editCriteria={editCriteria}
          setEditCriteria={setEditCriteria}
          saveCriteria={saveCriteria}
          saving={saving}
          criteria={criteria}
          criteriaVersions={criteriaVersions}
          routingPolicy={routingPolicy}
          setRoutingPolicy={setRoutingPolicy}
          saveRoutingPolicy={saveRoutingPolicy}
          savingRouting={savingRouting}
        />
      )}

      {tab === 'pipeline' && (
        <RequisitionPipelineBoard
          pipelineSync={pipelineSync}
          pipeline={pipeline}
          requisitionId={id}
          onStatusChange={handleStatusChange}
          onSubmit={handleSubmit}
          onOutcome={handleOutcomeClick}
          canWritePipeline={canWritePipeline}
          isHiringManager={isHiringManager}
        />
      )}

      {showHmRequest && (
        <HmRequestModal
          onClose={() => setShowHmRequest(false)}
          onSubmitted={handleHmRequested}
        />
      )}

      {showHmInvite && (
        <HmInviteModal
          onClose={() => setShowHmInvite(false)}
          onInvited={handleHmInvited}
        />
      )}

      {showJdModal && (
        <JdFullModal
          title={`${REQUISITIONS.jdModalTitle} · ${req.title}`}
          jdText={req.jd_text}
          onClose={() => setShowJdModal(false)}
        />
      )}

      <HmOutcomeModal
        open={Boolean(outcomeModal)}
        outcome={outcomeModal?.outcome}
        saving={saving}
        onClose={() => setOutcomeModal(null)}
        onConfirm={({ outcome, reasonCode, notes }) => {
          handleOutcome(outcomeModal.candidateId, outcome, reasonCode, notes)
        }}
      />

      {dialog}

      {showAddCandidates && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <Card className="w-full max-w-lg max-h-[80vh] overflow-hidden flex flex-col p-5">
            <h3 className="font-bold text-brand-900 mb-3">Add candidates to pipeline</h3>
            <div className="overflow-y-auto flex-1 space-y-2 mb-4">
              {allCandidates.length === 0 ? (
                <p className="text-sm text-slate-500">No candidates in your tenant yet.</p>
              ) : (
                allCandidates.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-sm p-2 rounded-lg hover:bg-brand-50">
                    <input
                      type="checkbox"
                      checked={selectedCandidateIds.includes(c.id)}
                      onChange={(e) => {
                        setSelectedCandidateIds((prev) =>
                          e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id),
                        )
                      }}
                    />
                    <span>{c.name || c.email || `Candidate #${c.id}`}</span>
                  </label>
                ))
              )}
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="ghost" onClick={() => setShowAddCandidates(false)}>Cancel</Button>
              <Button onClick={handleAddCandidates} disabled={!selectedCandidateIds.length}>Add</Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
