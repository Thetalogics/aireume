import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Loader2, Save, Volume2, Zap, Phone, Brain, ChevronRight, Settings as SettingsIcon,
} from 'lucide-react'
import { getVoiceSettings, updateVoiceSettings, getRecruiterConfig, updateRecruiterConfig } from '../../lib/api'
import { Button, Card } from '../ui'
import { INTERVIEW } from '../../lib/uxLabels'
import { useOnboarding } from '../../contexts/useOnboarding'

function Section({ title, icon: Icon, children, description }) {
  return (
    <Card className="p-6">
      <div className="flex items-start gap-4 mb-5">
        <div className="w-10 h-10 rounded-2xl bg-brand-50 ring-1 ring-brand-100 flex items-center justify-center shrink-0">
          <Icon className="w-5 h-5 text-brand-600" />
        </div>
        <div>
          <h3 className="font-extrabold text-brand-900 text-lg tracking-tight">{title}</h3>
          {description && <p className="text-sm text-slate-500 mt-0.5">{description}</p>}
        </div>
      </div>
      {children}
    </Card>
  )
}

const inputClass = 'w-full px-3.5 py-2.5 bg-white dark:bg-dark-card rounded-xl ring-1 ring-slate-200 dark:ring-white/10 focus:ring-2 focus:ring-brand-500 text-sm outline-none transition-all'

export default function InterviewSettingsPanel() {
  const navigate = useNavigate()
  const { completeChecklistItem } = useOnboarding()
  const [config, setConfig] = useState(null)
  const [draft, setDraft] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const dirty = config && draft && JSON.stringify(config) !== JSON.stringify(draft)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const [voice, recruiter] = await Promise.all([
        getVoiceSettings().catch(() => null),
        getRecruiterConfig().catch(() => null),
      ])
      const merged = { voice, recruiter }
      setConfig(merged)
      setDraft(merged)
    } catch (err) {
      setError(err.message || 'Failed to load interview settings')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleSave() {
    try {
      setSaving(true)
      const results = {}
      if (draft.voice) results.voice = await updateVoiceSettings(draft.voice)
      if (draft.recruiter) results.recruiter = await updateRecruiterConfig(draft.recruiter)
      const updated = { voice: results.voice || draft.voice, recruiter: results.recruiter || draft.recruiter }
      setConfig(updated)
      setDraft(updated)
      completeChecklistItem('configuredInterviewSettings')
    } catch (err) {
      setError(err.message || 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
      </div>
    )
  }

  if (!draft) {
    return <p className="text-sm text-slate-500">Interview settings unavailable.</p>
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-500">{INTERVIEW.aiScreenCallHint}</p>

      {error && (
        <Card className="p-4 bg-red-50 ring-red-200 text-sm text-red-700">{error}</Card>
      )}

      {draft.voice && (
        <Section title="Bot identity" icon={Volume2} description="How ARIA presents on Quick Screen calls">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="interviewsettingspanel-bot-name-1" className="block text-sm font-semibold text-slate-700 mb-1.5">Bot name</label>
              <input id="interviewsettingspanel-bot-name-1"
                value={draft.voice.bot_name ?? ''}
                onChange={(e) => setDraft({ ...draft, voice: { ...draft.voice, bot_name: e.target.value } })}
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="interviewsettingspanel-caller-id-name-2" className="block text-sm font-semibold text-slate-700 mb-1.5">Caller ID name</label>
              <input id="interviewsettingspanel-caller-id-name-2"
                value={draft.voice.caller_id_name ?? ''}
                onChange={(e) => setDraft({ ...draft, voice: { ...draft.voice, caller_id_name: e.target.value } })}
                className={inputClass}
              />
            </div>
          </div>
        </Section>
      )}

      {draft.voice && (
        <Section title="Adaptive depth escalation" icon={Zap} description="Escalate high-scoring Quick Screens to Standard interviews">
          <div className="space-y-4">
            <label className="flex items-center gap-3 text-sm font-medium text-slate-700">
              <input
                type="checkbox"
                checked={draft.voice.auto_escalation_enabled ?? false}
                onChange={(e) => setDraft({ ...draft, voice: { ...draft.voice, auto_escalation_enabled: e.target.checked } })}
                className="w-4 h-4 rounded border-slate-300 text-brand-600"
              />
              Enable auto-escalation
            </label>
            <div>
              <div className="flex justify-between mb-2">
                <span className="text-sm font-semibold text-slate-700">Score threshold</span>
                <span className="text-sm font-bold text-brand-600">{draft.voice.auto_escalation_threshold ?? 70}</span>
              </div>
              <input
                type="range"
                min={40}
                max={100}
                step={5}
                value={draft.voice.auto_escalation_threshold ?? 70}
                onChange={(e) => setDraft({ ...draft, voice: { ...draft.voice, auto_escalation_threshold: parseInt(e.target.value, 10) } })}
                className="w-full accent-brand-600"
              />
            </div>
          </div>
        </Section>
      )}

      {draft.recruiter && (
        <Section title="Auto-trigger" icon={Zap} description="When Standard/Deep interviews start automatically">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label className="flex items-center gap-3 text-sm font-medium text-slate-700 md:col-span-2">
              <input
                type="checkbox"
                checked={draft.recruiter.auto_trigger_enabled ?? false}
                onChange={(e) => setDraft({ ...draft, recruiter: { ...draft.recruiter, auto_trigger_enabled: e.target.checked } })}
                className="w-4 h-4 rounded border-slate-300 text-brand-600"
              />
              Auto-trigger on shortlist
            </label>
            <div>
              <label htmlFor="interviewsettingspanel-min-fit-score-3" className="block text-sm font-semibold text-slate-700 mb-1.5">Min fit score</label>
              <input id="interviewsettingspanel-min-fit-score-3"
                type="number"
                value={draft.recruiter.min_score_threshold ?? 60}
                onChange={(e) => setDraft({ ...draft, recruiter: { ...draft.recruiter, min_score_threshold: parseInt(e.target.value, 10) || 60 } })}
                min={0}
                max={100}
                className={inputClass}
              />
            </div>
            <div>
              <label htmlFor="interviewsettingspanel-default-duration-min-4" className="block text-sm font-semibold text-slate-700 mb-1.5">Default duration (min)</label>
              <input id="interviewsettingspanel-default-duration-min-4"
                type="number"
                value={draft.recruiter.default_duration_minutes ?? 30}
                onChange={(e) => setDraft({ ...draft, recruiter: { ...draft.recruiter, default_duration_minutes: parseInt(e.target.value, 10) || 30 } })}
                min={10}
                max={60}
                className={inputClass}
              />
            </div>
          </div>
        </Section>
      )}

      <Section title="Advanced" icon={SettingsIcon} description="Engine-specific configuration">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <button
            type="button"
            onClick={() => navigate('/voice-screening?tab=settings')}
            className="flex items-center justify-between gap-3 px-4 py-3 panel-surface rounded-xl hover:ring-brand-300 transition-all text-left"
          >
            <span className="flex items-center gap-3">
              <Phone className="w-4 h-4 text-brand-500" />
              <span>
                <span className="block text-sm font-semibold text-slate-800">Voice call settings</span>
                <span className="block text-xs text-slate-500">Windows, consent, retries</span>
              </span>
            </span>
            <ChevronRight className="w-4 h-4 text-slate-400" />
          </button>
          <button
            type="button"
            onClick={() => navigate('/recruiter-interviews?tab=config')}
            className="flex items-center justify-between gap-3 px-4 py-3 panel-surface rounded-xl hover:ring-brand-300 transition-all text-left"
          >
            <span className="flex items-center gap-3">
              <Brain className="w-4 h-4 text-brand-500" />
              <span>
                <span className="block text-sm font-semibold text-slate-800">Recruiter automation</span>
                <span className="block text-xs text-slate-500">Strategy engine & sessions</span>
              </span>
            </span>
            <ChevronRight className="w-4 h-4 text-slate-400" />
          </button>
        </div>
      </Section>

      {dirty && (
        <div className="sticky bottom-4 z-10 panel-surface rounded-2xl shadow-brand-lg px-5 py-4 flex items-center justify-between">
          <p className="text-sm font-medium text-slate-600">Unsaved changes</p>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setDraft(config)}>Discard</Button>
            <Button loading={saving} onClick={handleSave}>
              <Save className="w-4 h-4 mr-1.5" />
              Save
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
