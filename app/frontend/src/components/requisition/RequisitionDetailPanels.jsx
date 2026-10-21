import { countJdWords, resolveRequisitionNextAction, parseScoringWeights } from './requisitionDetailUtils'
import { useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronUp, FileText, Loader2, Sparkles, UserPlus, Wand2, X } from 'lucide-react'
import { Button } from '../ui'
import { REQUISITIONS } from '../../lib/uxLabels'
import { requestRequisitionHm, inviteTeamMember } from '../../lib/api'
import { showError } from '../../lib/toast'
import { splitIntakeListInput } from '../../lib/intakeFormUtils'



const NEXT_ACTION_TONES = {
  brand: 'bg-brand-50 ring-brand-200 text-brand-900',
  amber: 'bg-amber-50 ring-amber-200 text-amber-950',
  emerald: 'bg-emerald-50 ring-emerald-200 text-emerald-950',
  slate: 'bg-slate-50 ring-slate-200 text-slate-800',
}

function RequisitionNextActionBanner({ action, onAction }) {
  if (!action) return null
  const tone = NEXT_ACTION_TONES[action.tone] || NEXT_ACTION_TONES.brand
  return (
    <div className={`rounded-xl ring-1 px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-3 ${tone}`}>
      <div className="flex-1 min-w-0">
        <p className="text-[10px] font-bold uppercase tracking-wider opacity-70">{action.stepLabel}</p>
        <p className="text-sm font-bold mt-0.5">{action.title}</p>
        <p className="text-xs mt-1 opacity-80 leading-relaxed">{action.description}</p>
      </div>
      <Button size="sm" className="shrink-0" onClick={() => onAction(action.action)}>
        {action.cta}
      </Button>
    </div>
  )
}

function JdFullModal({ title, jdText, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-3xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-brand-50">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-5 h-5 text-brand-600 shrink-0" />
            <h3 className="font-bold text-brand-900 truncate">{title || REQUISITIONS.jdModalTitle}</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 hover:bg-brand-50 rounded-lg shrink-0"
          >
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4 flex-1">
          {jdText?.trim() ? (
            <p className="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">{jdText}</p>
          ) : (
            <p className="text-sm text-slate-500">{REQUISITIONS.jdEmpty}</p>
          )}
        </div>
        <div className="px-5 py-3 border-t border-brand-50 flex justify-end">
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  )
}

function JdPreviewSection({ jdText, onViewFull }) {
  const [expanded, setExpanded] = useState(false)
  const wordCount = countJdWords(jdText)
  if (!jdText?.trim()) {
    return <p className="text-sm text-slate-500">{REQUISITIONS.jdEmpty}</p>
  }
  return (
    <div>
      <p className={`text-sm text-slate-600 whitespace-pre-wrap leading-relaxed ${expanded ? '' : 'line-clamp-6'}`}>
        {jdText}
      </p>
      <div className="flex flex-wrap items-center gap-3 mt-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs font-semibold text-brand-600 hover:text-brand-800 inline-flex items-center gap-1"
        >
          {expanded ? (
            <>
              <ChevronUp className="w-3.5 h-3.5" />
              {REQUISITIONS.jdShowLess}
            </>
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5" />
              {REQUISITIONS.jdShowMore}
            </>
          )}
        </button>
        <button
          type="button"
          onClick={onViewFull}
          className="text-xs font-semibold text-brand-600 hover:text-brand-800"
        >
          {REQUISITIONS.jdViewFullCta} ({REQUISITIONS.jdWordCount(wordCount)}) →
        </button>
      </div>
    </div>
  )
}

function JdReferencePanel({ jdText, onViewFull }) {
  const [open, setOpen] = useState(false)
  const wordCount = countJdWords(jdText)
  return (
    <div className="rounded-xl ring-1 ring-brand-100 bg-brand-50/40 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-brand-50/80 transition-colors"
      >
        <div className="min-w-0">
          <p className="text-sm font-semibold text-brand-900">{REQUISITIONS.jdReferenceLabel}</p>
          <p className="text-xs text-slate-500 mt-0.5 truncate">
            {jdText?.trim()
              ? `${REQUISITIONS.jdWordCount(wordCount)} · ${REQUISITIONS.jdReferenceHint}`
              : REQUISITIONS.jdEmpty}
          </p>
        </div>
        <ChevronDown className={`w-4 h-4 text-slate-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && jdText?.trim() && (
        <div className="px-4 pb-4 border-t border-brand-100">
          <div className="mt-3 max-h-64 overflow-y-auto rounded-xl bg-white ring-1 ring-brand-100 p-3">
            <p className="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">{jdText}</p>
          </div>
          <button
            type="button"
            onClick={onViewFull}
            className="text-xs font-semibold text-brand-600 hover:text-brand-800 mt-2"
          >
            {REQUISITIONS.jdViewFullCta} ({REQUISITIONS.jdWordCount(wordCount)}) →
          </button>
        </div>
      )}
    </div>
  )
}

const COLUMN_STYLES = {
  pending: { header: 'bg-amber-50 text-amber-800 border-amber-200', badge: 'bg-amber-100 text-amber-700' },
  'in-review': { header: 'bg-blue-50 text-blue-800 border-blue-200', badge: 'bg-blue-100 text-blue-700' },
  shortlisted: { header: 'bg-green-50 text-green-800 border-green-200', badge: 'bg-green-100 text-green-700' },
  rejected: { header: 'bg-red-50 text-red-800 border-red-200', badge: 'bg-red-100 text-red-700' },
  hired: { header: 'bg-indigo-50 text-indigo-800 border-indigo-200', badge: 'bg-indigo-100 text-indigo-700' },
}

function HmRequestModal({ onClose, onSubmitted }) {
  const [email, setEmail] = useState('')
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!email.trim()) return
    setLoading(true)
    setError('')
    try {
      await onSubmitted({ email: email.trim(), notes: notes.trim() || null })
    } catch (err) {
      setError(err.response?.data?.detail || 'Request failed')
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-brand-900">{REQUISITIONS.hmRequestTitle}</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="p-1 hover:bg-brand-50 rounded-lg">
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>
        <p className="text-sm text-slate-500 mb-4">{REQUISITIONS.hmRequestHint}</p>
        <label className="block text-sm mb-3">
          <span className="font-semibold text-slate-700">HM email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="hiring.manager@company.com"
            className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
          />
        </label>
        <label className="block text-sm mb-4">
          <span className="font-semibold text-slate-700">Notes for admin (optional)</span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            placeholder="e.g. Finance director for this FP&A opening"
            className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm resize-none"
          />
        </label>
        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={loading || !email.trim()}>
            {loading ? 'Submitting…' : REQUISITIONS.hmRequestCta}
          </Button>
        </div>
      </div>
    </div>
  )
}

function HmInviteModal({ onClose, onInvited }) {
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleInvite = async () => {
    if (!email.trim()) return
    setLoading(true)
    setError('')
    try {
      const data = await inviteTeamMember(email.trim(), 'hiring_manager')
      await onInvited(data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Invitation failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl ring-1 ring-brand-100 shadow-brand-xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-brand-900">{REQUISITIONS.hmInviteTitle}</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="p-1 hover:bg-brand-50 rounded-lg">
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>
        <p className="text-sm text-slate-500 mb-4">{REQUISITIONS.hmInviteHint}</p>
        <label className="block text-sm mb-4">
          <span className="font-semibold text-slate-700">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="hiring.manager@company.com"
            className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm"
          />
        </label>
        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={handleInvite} disabled={loading || !email.trim()}>
            {loading ? 'Inviting…' : REQUISITIONS.hmInviteCta}
          </Button>
        </div>
      </div>
    </div>
  )
}

function CriteriaVersionDiff({ versions }) {
  if (versions.length < 2) return null
  const [newer, older] = versions
  const newerCriteria = newer.criteria_json || {}
  const olderCriteria = older.criteria_json || {}
  const listKeys = ['must_haves', 'good_to_haves', 'deal_breakers']

  const diffFor = (key) => {
    const a = new Set(olderCriteria[key] || [])
    const b = new Set(newerCriteria[key] || [])
    return {
      added: [...b].filter((x) => !a.has(x)),
      removed: [...a].filter((x) => !b.has(x)),
    }
  }

  const hasChanges = listKeys.some((k) => {
    const d = diffFor(k)
    return d.added.length > 0 || d.removed.length > 0
  })
  if (!hasChanges) return null

  return (
    <div className="rounded-xl bg-brand-50/60 ring-1 ring-brand-100 p-4 space-y-3">
      <p className="text-xs font-semibold text-slate-500 uppercase">
        Changes v{older.version} → v{newer.version}
      </p>
      {listKeys.map((key) => {
        const { added, removed } = diffFor(key)
        if (!added.length && !removed.length) return null
        return (
          <div key={key} className="text-sm">
            <p className="font-semibold text-slate-700 capitalize">{key.replace(/_/g, ' ')}</p>
            {added.map((item) => (
              <p key={`add-${item}`} className="text-emerald-700">+ {item}</p>
            ))}
            {removed.map((item) => (
              <p key={`rem-${item}`} className="text-red-600">− {item}</p>
            ))}
          </div>
        )
      })}
    </div>
  )
}


function CriteriaEditForm({ criteria, onChange, readOnly }) {
  const fields = [
    { key: 'must_haves', label: 'Must-haves (one per line)', rows: 6 },
    { key: 'good_to_haves', label: 'Good-to-haves (one per line)', rows: 4 },
    { key: 'deal_breakers', label: 'Deal-breakers (one per line)', rows: 2 },
  ]
  const getValue = (key) => {
    const v = criteria[key]
    if (Array.isArray(v)) return v.join('\n')
    return v || ''
  }
  const setValue = (key, raw) => {
    onChange({
      ...criteria,
      [key]: raw.split('\n').map((s) => s.trim()).filter(Boolean),
    })
  }
  return (
    <div className="space-y-4">
      {fields.map(({ key, label, rows }) => (
        <label key={key} className="block text-sm">
          <span className="font-semibold text-slate-700">{label}</span>
          <textarea
            value={getValue(key)}
            onChange={(e) => setValue(key, e.target.value)}
            rows={rows}
            disabled={readOnly}
            className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm resize-none disabled:opacity-60 font-mono"
          />
        </label>
      ))}
    </div>
  )
}

function IntakeWorkflowBar({ intakeGate, req }) {
  const intakeDone = intakeGate?.intake_has_minimum_content && intakeGate?.hm_assigned
  const canScreen = intakeGate?.intake_screening_ready && !intakeGate?.blocks
  const criteriaVersion = req?.current_criteria_version || 0
  const hmApproved = req?.intake_status === 'approved'
  const criteriaReady = criteriaVersion >= 1
  const steps = [
    {
      key: 'intake',
      label: REQUISITIONS.intakeStepIntake,
      done: intakeDone,
      active: !intakeDone,
    },
    {
      key: 'screen',
      label: REQUISITIONS.intakeStepScreen,
      done: canScreen,
      active: intakeDone && !canScreen,
    },
    {
      key: 'refine',
      label: hmApproved
        ? REQUISITIONS.intakeStepRefine
        : criteriaReady
          ? `3. Criteria v${criteriaVersion} (awaiting HM)`
          : REQUISITIONS.intakeStepRefineDraft,
      done: hmApproved,
      active: canScreen && !hmApproved,
    },
  ]
  return (
    <div className="flex flex-wrap gap-2 mb-4">
      {steps.map((step) => (
        <span
          key={step.key}
          className={`inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full ring-1 ${
            step.done
              ? 'bg-emerald-50 text-emerald-800 ring-emerald-200'
              : step.active
                ? 'bg-brand-50 text-brand-800 ring-brand-200'
                : 'bg-slate-50 text-slate-500 ring-slate-200'
          }`}
        >
          {step.done ? <CheckCircle2 className="w-3.5 h-3.5" /> : null}
          {step.label}
          {step.key === 'screen' && canScreen && !intakeGate?.intake_approved && intakeGate?.requires_hm_approval === false && (
            <span className="text-[10px] font-normal opacity-70">(approval optional)</span>
          )}
        </span>
      ))}
    </div>
  )
}

function IntakeForm({
  intake,
  onChange,
  readOnly,
  onSuggest,
  suggesting,
  onSave,
  saving,
  intakeDirty,
  intakeSavedAt,
}) {
  const fields = [
    { key: 'screen_focus_topics', label: 'What should the screen focus on? (one topic per line)', rows: 4, list: true },
    { key: 'must_haves', label: 'Must-have skills (one per line)', rows: 4 },
    { key: 'good_to_haves', label: 'Nice-to-have skills (one per line)', rows: 3 },
    { key: 'deal_breakers', label: 'Deal-breakers (one per line)', rows: 2 },
    { key: 'environment', label: 'Work environment', rows: 2 },
    { key: 'seniority_bar', label: 'Seniority bar', rows: 1 },
    { key: 'team_context', label: 'Team context', rows: 2 },
    { key: 'success_criteria_90d', label: '90-day success criteria', rows: 2 },
    { key: 'hm_notes', label: 'HM notes', rows: 3 },
  ]

  const getValue = (key) => {
    const v = intake[key]
    if (Array.isArray(v)) return v.join('\n')
    return v || ''
  }

  const setValue = (key, raw) => {
    const listKeys = ['must_haves', 'good_to_haves', 'deal_breakers', 'screen_focus_topics']
    if (listKeys.includes(key)) {
      onChange({
        ...intake,
        [key]: splitIntakeListInput(raw),
      })
    } else {
      onChange({ ...intake, [key]: raw })
    }
  }

  return (
    <div className="space-y-4">
      {!readOnly && onSuggest && (
        <div className="rounded-xl bg-brand-50/80 ring-1 ring-brand-100 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-brand-900">Start from the job description</p>
            <p className="text-xs text-slate-600 mt-0.5">{REQUISITIONS.intakeSaveHint}</p>
          </div>
          <Button type="button" variant="secondary" onClick={onSuggest} disabled={suggesting}>
            {suggesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
            {REQUISITIONS.intakeSuggestCta}
          </Button>
        </div>
      )}
      {fields.map(({ key, label, rows }) => (
        <label key={key} className="block text-sm">
          <span className="font-semibold text-slate-700">{label}</span>
          <textarea
            value={getValue(key)}
            onChange={(e) => setValue(key, e.target.value)}
            rows={rows}
            disabled={readOnly}
            className="mt-1 w-full rounded-xl border border-brand-200 px-3 py-2 text-sm resize-none disabled:opacity-60"
          />
        </label>
      ))}
      {!readOnly && onSave && (
        <div className="pt-4 border-t border-brand-50 flex flex-wrap items-center gap-3">
          <Button type="button" onClick={onSave} disabled={saving || !intakeDirty}>
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
          {intakeDirty && !saving && (
            <span className="text-xs font-semibold text-amber-700">{REQUISITIONS.intakeUnsaved}</span>
          )}
          {saving && (
            <span className="text-xs font-semibold text-brand-700">{REQUISITIONS.intakeSaving}</span>
          )}
          <p className="text-xs text-slate-500 w-full">
            {REQUISITIONS.intakeAutoSaveHint} {REQUISITIONS.intakeSaveHint}
          </p>
        </div>
      )}
    </div>
  )
}


export {
  RequisitionNextActionBanner,
  JdFullModal,
  JdPreviewSection,
  JdReferencePanel,
  HmRequestModal,
  HmInviteModal,
  CriteriaVersionDiff,
  CriteriaEditForm,
  IntakeWorkflowBar,
  IntakeForm,
}
